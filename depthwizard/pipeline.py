"""End-to-end pipeline: image -> relative height -> (calibrated) DSM -> viewer assets."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import io as dio
from .buildings import extract_buildings
from .calibrate import DATUMS, calibrate, fetch_srtm
from .depth import relative_height
from .metrics import building_level, evaluate, reference_on_grid

SOFTWARE_VERSION = "DepthWizard 2.2 (SIH26175)"


def _file_sha256(path: str | Path | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def run(image_path, out_dir, *, dem=None, gcp=None, reference=None, model="small",
        scene="auto", fetch_dem=False, assumed_gsd_m=1.0, allow_fallback=True,
        relative_display_height_m=None, device=None, dem_source="COP30",
        match_dem_30m=True, tta=4, dem_kind="auto", sun_elevation=None, sun_azimuth=None,
        vertical_datum=None, anchors=None, log=print) -> dict:
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    log("reading image")
    img = dio.read_image(image_path)
    gsd = img.pixel_size_m or assumed_gsd_m
    log(f"  {img.shape[1]}x{img.shape[0]} px, georeferenced={img.georeferenced}"
        + (f", GSD≈{gsd:.2f} m" if img.pixel_size_m else ""))

    fetched = False
    if fetch_dem and not dem and img.georeferenced:
        log(f"fetching {dem_source} DEM for footprint")
        dem = str(fetch_srtm(img, out / "dem.tif", demtype=dem_source))
        fetched = True

    passes = 4 if tta is True else (1 if not tta else int(tta))
    log(f"relative height ({model}, {passes}-pass rotation ensemble)")
    t1 = time.time()
    rel, backbone, unc_norm, dinfo = relative_height(
        img.rgb, model=model, allow_fallback=allow_fallback, device=device,
        tta=passes, return_uncertainty=True, return_info=True, gsd=gsd)
    t_depth = time.time() - t1
    rel = ndimage.median_filter(rel, 3)  # suppress tile/speckle artefacts
    is_agl = bool(dinfo.get("agl"))
    learned = dinfo["learned_scale"](gsd) if dinfo.get("learned_scale") else None

    # a fetched global DEM has a known geoid; a user-supplied DEM keeps its own datum
    datum = vertical_datum or (DATUMS.get(dem_source.upper()) if fetched and dem_source else None)
    log(f"scale calibration (above-ground model={is_agl}, 30 m match={match_dem_30m})")
    dsm, units, cal = calibrate(
        rel, img, dem_path=dem, gcp_path=gcp, scene=scene, agl=is_agl,
        learned_scale=learned, dem_kind=dem_kind, reference_consistent=match_dem_30m,
        sun_elevation=sun_elevation, sun_azimuth=sun_azimuth,
        dem_source=dem_source if fetched else None, vertical_datum=datum)
    log(f"  method={cal.method} ({cal.scale_source}) {cal.note}")
    datum = cal.vertical_datum if units == "metre" else None
    if units == "metre" and cal.dtm is not None:
        from .analysis import water_mask
        wm = water_mask(img.rgb, gsd)
        if wm.any():   # open water is flat: remove spurious "structure" on it
            dsm = np.where(wm, cal.dtm, dsm).astype(np.float32)
            cal.ndsm = np.where(wm, 0, cal.ndsm).astype(np.float32)
            log(f"  flattened {wm.mean() * 100:.1f}% open water to the terrain")
        meta_water = float(wm.mean())
    else:
        meta_water = 0.0
        wm = None

    name = "dsm.tif" if units == "metre" else "rdsm.tif"
    dio.write_dsm(out / name, dsm, img, units=units,
                  description=f"DepthWizard {cal.method} ({backbone})", vertical_datum=datum)
    if units == "metre" and cal.dtm is not None:
        dio.write_dsm(out / "dtm.tif", cal.dtm, img, units=units,
                      description=f"DepthWizard bare-earth DTM ({backbone})", vertical_datum=datum)
    if units == "metre" and cal.ndsm is not None:
        dio.write_dsm(out / "ndsm.tif", cal.ndsm, img, units=units,
                      description=f"DepthWizard above-ground heights nDSM ({backbone})",
                      vertical_datum=datum)

    # per-pixel 1-sigma uncertainty from the rotation ensemble, in output units
    std_rel = dinfo.get("std_rel")
    unc_units = None
    if std_rel is not None:
        unc_units = std_rel * (cal.scale_k if units == "metre" and cal.scale_k else 1.0)
        dio.write_dsm(out / "uncertainty.tif", unc_units, img,
                      units=units if units == "metre" else "relative",
                      description="1-sigma spread of the rotation ensemble")

    meta = {
        "input": Path(image_path).name,
        "backbone": backbone,
        "georeferenced": img.georeferenced,
        "crs": img.crs.to_string() if img.crs else None,
        "transform": list(img.transform)[:6] if img.georeferenced else None,
        "units": units,
        "vertical_datum": datum if units == "metre" else "relative",
        "calibration": cal.as_dict(),
        "scene": scene,
        "assumed_gsd_m": assumed_gsd_m,
        "dsm_file": name,
        "tta": dinfo.get("tta"),
        "water_fraction": meta_water,
        "agl_model": is_agl,
        "learned_scale_m_per_unit": learned,
        "timing_s": {"depth": round(t_depth, 2)},
        "sun_input": {"elevation_deg": sun_elevation, "azimuth_deg": sun_azimuth},
    }
    if units == "relative":
        # display scale for the viewer: relief ~ 8% of scene width unless given
        meta["display_height_m"] = relative_display_height_m or 0.08 * max(img.shape) * gsd
    view_h = dsm if units == "metre" else dsm * meta["display_height_m"]
    unc_view = None
    if unc_units is not None:
        unc_view = unc_units if units == "metre" else unc_units * meta["display_height_m"]

    log("extracting LoD1 building footprints")
    b_dtm = cal.dtm if units == "metre" else None
    min_h = 2.5 if units == "metre" else 0.12 * float(np.percentile(view_h - view_h.min(), 98))
    buildings = extract_buildings(view_h, dtm=b_dtm, gsd=gsd, world_w=img.shape[1] * gsd,
                                  world_h=img.shape[0] * gsd, rgb=img.rgb,
                                  uncertainty_m=unc_view, min_height_m=max(min_h, 1e-3),
                                  edge_refine_for_footprints=True,
                                  return_labels=True)
    labels = buildings.pop("_labels", None)
    if units != "metre":
        # relative scene: express heights in relative units, not display metres
        f = meta["display_height_m"]
        for b in buildings["buildings"]:
            for key in ("roof_elevation_m", "ground_elevation_m", "height_m"):
                b[key] = round(b[key] / f, 4)
            b.pop("storeys", None)
            b["volume_m3"] = None
    log(f"  detected {buildings['count']} buildings (footprint {buildings['total_footprint_m2']} m²)")
    meta["buildings_count"] = buildings["count"]
    meta["total_footprint_m2"] = buildings["total_footprint_m2"]

    # Automatic anchors are independent height cues, not reference validation.
    # Only a consistent group may change metric scale; all candidates/rejections
    # remain visible in metadata for audit and manual review.
    auto_applied = False
    if units == "metre" and cal.ndsm is not None and cal.dtm is not None and labels is not None and buildings["count"] and not anchors:
        try:
            from .auto_anchors import automatic_height_anchors
            candidates, diagnostics = automatic_height_anchors(
                img, labels, buildings["buildings"], gsd_m=gsd, ndsm=cal.ndsm, dtm=cal.dtm,
                water=wm, sun_elevation_deg=sun_elevation, sun_azimuth_deg=sun_azimuth,
                cache_dir=out.parent / "osm_cache")
            reliable = [a for a in candidates if a.get("confidence", 0) >= 0.65 and
                        (a.get("source") == "shadow geometry" or a.get("osm_tag") == "height")]
            from .calibrate import apply_height_anchors
            estimated = {b["id"]: b["height_m"] for b in buildings["buildings"]}
            proposed_scale, proposed_stats = apply_height_anchors(cal.ndsm, labels, reliable, estimated)
            consistent = (len(reliable) >= 3 and proposed_stats["n_used"] >= 3 and
                          proposed_stats["cv"] <= 0.20 and 0.67 <= proposed_scale <= 1.5)
            if cal.n_gcp > 0 or cal.evidence_level == "measured":
                consistent = False
                diagnostics["application_reason"] = "kept stronger measured calibration"
            elif not consistent:
                diagnostics["application_reason"] = "fewer than three consistent high-confidence anchors"
            else:
                anchors = reliable
                auto_applied = True
                diagnostics["application_reason"] = "applied consistent provisional anchors"
            meta["auto_anchors"] = {"anchors": candidates, "diagnostics": diagnostics,
                                    "applied": auto_applied, "proposed_scale": proposed_scale,
                                    "proposed_stats": proposed_stats}
            log(f"  automatic anchors: {len(candidates)} candidate(s), {len(reliable)} reliable, "
                f"{'applied' if auto_applied else 'held for review'}")
        except Exception as exc:  # noqa: BLE001
            meta["auto_anchors"] = {"anchors": [], "diagnostics": {"error": str(exc)}, "applied": False}
            log(f"  automatic anchors unavailable: {exc}")

    # --- Apply Global Scale Anchors ---
    if anchors and cal.ndsm is None:
        log("  skipping anchors: this metric scene has no separate nDSM structure layer")
    if anchors and cal.ndsm is not None:
        if units != "metre":
            log("  skipping anchors: scene is relative")
        else:
            log("  applying height anchors")
            resolved_anchors = []
            for anc in anchors:
                if "lon" in anc and "lat" in anc:
                    if not img.georeferenced:
                        continue
                    from rasterio.warp import transform as _tr
                    try:
                        xs, ys = _tr("EPSG:4326", img.crs, [anc["lon"]], [anc["lat"]])
                        col, row = ~img.transform * (xs[0], ys[0])
                        c, r = int(col), int(row)
                        if 0 <= r < labels.shape[0] and 0 <= c < labels.shape[1]:
                            b_id = labels[r, c]
                            if b_id > 0:
                                resolved_anchors.append({"building_id": int(b_id), "height_m": anc["height_m"]})
                    except Exception as e:
                        log(f"    failed to resolve lon/lat anchor: {e}")
                elif "building_id" in anc:
                    resolved_anchors.append(anc)
                    
            from .calibrate import apply_height_anchors
            est = {b["id"]: b["height_m"] for b in buildings["buildings"]}
            s, stats = apply_height_anchors(cal.ndsm, labels, resolved_anchors, est)
            
            # Save raw files for potential reset via endpoint later
            import shutil
            if not (out / "ndsm_raw.tif").exists():
                shutil.copy2(out / "ndsm.tif", out / "ndsm_raw.tif")
            if not (out / "dsm_raw.tif").exists():
                shutil.copy2(out / "dsm.tif", out / "dsm_raw.tif")
            if (out / "uncertainty.tif").exists() and not (out / "uncertainty_raw.tif").exists():
                shutil.copy2(out / "uncertainty.tif", out / "uncertainty_raw.tif")
                
            ndsm0 = cal.ndsm.copy()
            dsm = dsm + (s - 1) * ndsm0
            cal.ndsm = ndsm0 * s
            
            cal.scale_k = (cal.scale_k or 1.0) * s
            
            if unc_units is not None:
                unc_units = unc_units * s
                
            dio.write_dsm(out / "dsm.tif", dsm, img, units="metre", description=f"DepthWizard height-anchor ({backbone})", vertical_datum=datum)
            dio.write_dsm(out / "ndsm.tif", cal.ndsm, img, units="metre", description=f"DepthWizard above-ground heights nDSM ({backbone})", vertical_datum=datum)
            if unc_units is not None:
                dio.write_dsm(out / "uncertainty.tif", unc_units, img, units="metre", description="1-sigma spread of the rotation ensemble")
            
            for b in buildings["buildings"]:
                from .roof_fit import rescale_roof_fit
                b["height_raw_m"] = b["height_m"]
                b["volume_raw_m3"] = b.get("volume_m3")
                b["height_m"] = round(b["height_m"] * s, 2)
                b["roof_elevation_m"] = round(b["ground_elevation_m"] + b["height_m"], 2)
                b["storeys"] = max(1, round(b["height_m"] / 3.0))
                if b["volume_raw_m3"] is not None:
                    b["volume_m3"] = round(b["volume_raw_m3"] * s, 1)
                if b.get("roof_fit"):
                    rescale_roof_fit(b["roof_fit"], s)
                    
            cal.method = "height-anchor"
            if auto_applied:
                cal.scale_source = f"{stats.get('n_used', 0)} automatic shadow/OSM estimate(s)"
                cal.evidence_level = "provisional"
            else:
                cal.scale_source = f"{stats.get('n_used', 0)} supplied building height(s)"
                cal.evidence_level = "measured" if stats.get("n_used", 0) >= 2 else "provisional"
            
            previous_calibration = meta["calibration"]
            meta["calibration"] = cal.as_dict()
            meta["calibration"]["base"] = previous_calibration
            meta["height_anchor"] = {"s": s, "anchors": resolved_anchors, "stats": stats,
                                     "source": "automatic" if auto_applied else "supplied"}
            meta["_tmp_height_anchor_s"] = s

            # The viewer and preview must show the same calibrated surface as
            # the exported GeoTIFF and the validation metrics below.
            view_h = dsm
            unc_view = unc_units

    ref = None
    if reference:
        log("validating against reference")
        ref = reference_on_grid(reference, img)
        baseline = getattr(cal, "extras", {}).get("dem") if units == "metre" else None
        meta["metrics"] = evaluate(dsm, ref, units, rgb=img.rgb, gsd=gsd, baseline=baseline)
        if units == "metre" and labels is not None and buildings["count"] >= 5:
            est_nd = dsm - (cal.dtm if cal.dtm is not None else np.percentile(dsm, 2))
            bm = building_level(labels, est_nd, ref, gsd)
            if bm:
                meta["metrics"]["buildings"] = bm
                log(f"  per-building heights vs reference: n={bm['n']} "
                    f"RMSE {bm['rmse']:.2f} m, r {bm['r']:.2f}")
        (out / "metrics.json").write_text(json.dumps(meta["metrics"], indent=2))

    meta["evidence_bundle"] = {
        "image_sha256": _file_sha256(image_path),
        "dem_sha256": _file_sha256(dem),
        "gcp_sha256": _file_sha256(gcp),
        "reference_sha256": _file_sha256(reference),
        "model_identifier": backbone,
        "rotation_passes": dinfo.get("tta"),
        "calibration_method": cal.method,
        "scale_source": cal.scale_source,
        "evidence_level": cal.evidence_level,
        "scale_k": cal.scale_k,
        "vertical_datum": datum,
        "software_version": SOFTWARE_VERSION,
    }
    if "_tmp_height_anchor_s" in meta:
        meta["evidence_bundle"]["height_anchor_s"] = meta.pop("_tmp_height_anchor_s")
    if img.georeferenced:
        try:
            from rasterio.warp import transform as _tr
            hh, ww = img.shape
            xs, ys = zip(*[img.transform * (c, r) for c, r in ((0, 0), (ww, 0), (0, hh), (ww, hh))])
            lon, lat = _tr(img.crs, "EPSG:4326", list(xs), list(ys))
            meta["corners_lonlat"] = [[float(a), float(b)] for a, b in zip(lon, lat)]  # TL, TR, BL, BR
        except Exception:  # noqa: BLE001
            pass

    # ---- disaster / planning analytics layers
    analytics = {}
    try:
        from .analysis import landslide_susceptibility, roof_solar
        susc = None
        if units == "metre" and cal.dtm is not None:   # needs bare-earth terrain in metres
            susc, sstats = landslide_susceptibility(cal.dtm, img.rgb, gsd)
            analytics["landslide"] = sstats
        if units == "metre" and labels is not None and buildings["count"]:
            lat = float(np.mean([c[1] for c in meta["corners_lonlat"]])) if meta.get("corners_lonlat") else 22.0
            sol = roof_solar(dsm, labels, gsd, lat_deg=lat)
            for b in buildings["buildings"]:
                if b["id"] in sol:
                    b.update(sol[b["id"]])
            analytics["solar"] = {"latitude_deg": lat, "buildings": len(sol),
                                  "total_pv_mwh_yr": round(sum(v["pv_kwh_yr"] for v in sol.values()) / 1000, 1),
                                  "assumptions": "GHI 1,900 kWh/m²/yr, 18 % modules, 70 % usable roof"}
        log(f"  analytics: landslide index, {'solar per roof' if 'solar' in analytics else 'no solar'}")
    except Exception as exc:  # noqa: BLE001
        susc = None
        log(f"  analytics skipped: {exc}")
    meta["analytics"] = analytics
    if labels is not None:
        np.save(out / "building_labels.npy", labels.astype(np.int32))

    view_ref = ref if units == "metre" else None
    view_dtm = cal.dtm if units == "metre" else None
    if unc_view is not None:
        scale = 2.0 if units == "metre" else max(float(np.percentile(unc_view, 95)), 1e-6)
        confidence_map = np.exp(-unc_view / scale).astype(np.float32)
    else:
        confidence_map = np.clip(1.0 - unc_norm, 0.0, 1.0).astype(np.float32)
    meta["confidence_definition"] = ("exp(-sigma / 2 m), sigma = rotation-ensemble spread"
                                     if units == "metre" else "relative ensemble agreement")

    meta["timing_s"]["total"] = round(time.time() - t0, 2)
    dio.export_viewer_assets(
        out / "viewer", img, view_h, meta, reference=view_ref,
        dtm=view_dtm, confidence=confidence_map, buildings=buildings,
        baseline=getattr(cal, "extras", {}).get("dem") if units == "metre" else None,
        uncertainty=unc_view, susceptibility=susc)
    dio.save_preview(out / "preview.png", view_h, gsd=gsd)
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=float))
    log(f"done in {meta['timing_s']['total']} s -> {out}")
    return meta
