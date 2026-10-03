"""End-to-end pipeline: image -> relative height -> (calibrated) DSM -> viewer assets."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import io as dio
from .buildings import extract_buildings
from .calibrate import DATUMS, calibrate, fetch_srtm
from .depth import relative_height
from .dem_fetch import cached_tile_names, fetch_copernicus_glo30
from .metrics import building_level, evaluate, reference_on_grid, vs_copernicus_30m

SOFTWARE_VERSION = "DepthWizard 2.2 (SIH26175)"


def _clean_numpy(d):
    if isinstance(d, dict):
        return {k: _clean_numpy(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_clean_numpy(v) for v in d]
    if isinstance(d, (np.integer, np.floating, np.bool_)):
        return d.item()
    if isinstance(d, np.ndarray):
        return d.tolist()
    return d

def _file_sha256(path: str | Path | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


COARSE_GSD_M = 2.5  # at/above this pixel size auto-Copernicus uses surface mode


def run(image_path, out_dir, *, dem=None, gcp=None, reference=None, model="small",
        scene="auto", fetch_dem=True, assumed_gsd_m=1.0, allow_fallback=False,
        relative_display_height_m=None, device=None, dem_source="COP30",
        match_dem_30m=True, tta=4, dem_kind="auto", sun_elevation=None, sun_azimuth=None,
        vertical_datum=None, gcp_height_type="orthometric", anchors=None,
        max_pixels=None, cop_scale=False, semantic_model=None, log=print) -> dict:
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    from .dem_view import is_elevation_raster, run_dem_only
    if is_elevation_raster(image_path):
        mp = int(float(os.environ.get("DEPTHWIZARD_MAX_MP", "64")) * 1e6) if max_pixels is None else int(max_pixels)
        return run_dem_only(image_path, out_dir, max_pixels=mp, assumed_gsd_m=assumed_gsd_m, log=log)

    log("reading image")
    img = dio.read_image(image_path)
    max_pixels = int(float(os.environ.get("DEPTHWIZARD_MAX_MP", "64")) * 1e6) if max_pixels is None else int(max_pixels)
    img, shrink = dio.limit_pixels(img, max_pixels)
    if shrink > 1.0:
        assumed_gsd_m = assumed_gsd_m * shrink
        log(f"  large scene: downsampled {shrink:.2f}x to {img.shape[1]}x{img.shape[0]} px "
            f"(limit {max_pixels / 1e6:.0f} MP; set DEPTHWIZARD_MAX_MP to change)")
    gsd = img.pixel_size_m or assumed_gsd_m
    log(f"  {img.shape[1]}x{img.shape[0]} px, georeferenced={img.georeferenced}"
        + (f", GSD~{gsd:.2f} m" if img.pixel_size_m else ""))

    dem_origin = "user" if dem else None
    dem_tiles = []
    effective_source = (dem_source or "COP30").upper() if not dem else (dem_source or "").upper()
    downloaded = False
    if fetch_dem and not dem and img.georeferenced:
        log("fetching keyless Copernicus GLO-30 for footprint")
        try:
            dem = str(fetch_copernicus_glo30(img, out / "dem.tif"))
            dem_origin, effective_source, downloaded = "copernicus-glo30-auto", "COP30", True
            dem_tiles = cached_tile_names(dem)
            log(f"  Copernicus tiles: {', '.join(dem_tiles)}")
        except (RuntimeError, ValueError) as exc:
            log(f"  WARNING: {exc}; continuing with learned scale")
            if (dem_source or "").upper() == "SRTMGL1" and os.environ.get("OPENTOPO_API_KEY"):
                try:
                    dem = str(fetch_srtm(img, out / "dem.tif", demtype="SRTMGL1"))
                    dem_origin, effective_source, downloaded = "opentopography", "SRTMGL1", True
                    log("  OpenTopography SRTM fallback downloaded")
                except Exception as fallback_exc:  # noqa: BLE001
                    log(f"  WARNING: OpenTopography fallback failed: {fallback_exc}")
                    dem = None
    effective_kind = dem_kind  # explicit user override always respected
    cop_terrain_dem = None     # path to derived terrain proxy; None for all non-Mode-C paths
    if dem and dem_origin == "copernicus-glo30-auto" and dem_kind == "auto" and gsd >= COARSE_GSD_M:
        # Coarse imagery (>= 2.5 m): single buildings are 1-3 pixels and the
        # learned metric scale is not trusted, so follow the Copernicus surface
        # model at its 30 m cells and use the image for texture/finer detail.
        # Bengaluru Sentinel-2 10 m: agreement with Copernicus 11.1 m -> 0.71 m.
        effective_kind = "surface"
        log(f"  coarse image ({gsd:.1f} m): Copernicus used as surface model (30 m consistency)")
    elif dem and dem_origin == "copernicus-glo30-auto" and dem_kind == "auto":
        # Mode C: derive approximate bare-earth terrain from the Copernicus DSM so
        # that nDSM captures real building heights instead of being suppressed by
        # the surface signal already embedded in the Copernicus DSM.
        from .calibrate import copernicus_terrain_from_dsm, dem_resolution_m
        import rasterio as _rio
        cop_terrain_path = out / "dem_terrain.tif"
        with _rio.open(dem) as _src:
            _arr  = _src.read(1).astype(np.float32)
            _prof = _src.profile.copy()
        _prof.update(dtype="float32", nodata=np.nan)
        _res_m = dem_resolution_m(dem)
        _terrain = copernicus_terrain_from_dsm(_arr, _res_m)
        with _rio.open(cop_terrain_path, "w", **_prof) as _dst:
            _dst.write(_terrain, 1)
            _dst.update_tags(
                SOURCE="Copernicus GLO-30 terrain proxy (morphological opening)",
                VERTICAL_DATUM="EGM2008 geoid (Copernicus GLO-30)")
        cop_terrain_dem = str(cop_terrain_path)
        effective_kind  = "terrain"
        log("  Mode C: derived terrain proxy from Copernicus DSM (morphological opening)")

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

    # A tagged user raster wins over a source hint; untagged user rasters keep
    # the explicit source datum or "same as input DEM" when source is unknown.
    source_datum = None
    if dem:
        import rasterio
        with rasterio.open(dem) as source:
            source_datum = source.tags().get("VERTICAL_DATUM")
    datum = vertical_datum or source_datum or DATUMS.get(effective_source)
    log(f"scale calibration (above-ground model={is_agl}, 30 m match={match_dem_30m})")
    def do_calibration(source_dem, kind, source, datum_name):
        return calibrate(
            rel, img, dem_path=source_dem, gcp_path=gcp, scene=scene, agl=is_agl,
            learned_scale=learned, dem_kind=kind, reference_consistent=match_dem_30m,
            sun_elevation=sun_elevation, sun_azimuth=sun_azimuth,
            dem_source=source or None, vertical_datum=datum_name,
            gcp_height_type=gcp_height_type)
    try:
        cal_dem = cop_terrain_dem if cop_terrain_dem else dem
        dsm, units, cal = do_calibration(cal_dem, effective_kind, effective_source, datum)
    except ValueError as exc:
        if not downloaded:
            raise
        log(f"  WARNING: downloaded DEM rejected ({exc}); continuing with learned scale")
        dem, dem_origin, dem_tiles, effective_source = None, None, [], ""
        cop_terrain_dem = None
        dsm, units, cal = do_calibration(None, dem_kind, "", vertical_datum)
    if dem_origin:
        cal.dem_origin = dem_origin
        cal.dem_tile_names = dem_tiles
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

    cop_scale_dem = None
    if cop_scale and img.georeferenced and gsd < COARSE_GSD_M and units == "metre" and cal.dtm is not None:
        try:
            cop_scale_dem = dem if effective_source == "COP30" else str(
                fetch_copernicus_glo30(img, out / "cop_scale_dem.tif"))
            from .calibrate import copernicus_height_scale
            est_nd = dsm - cal.dtm
            s, n = copernicus_height_scale(est_nd, cal.dtm, img, cop_scale_dem)
            cal.cop_height_scale_cells = n
            if s is None:
                log(f"  Copernicus height scale skipped: {n} usable cells (need at least 30)")
            else:
                cal.cop_height_scale = s
                cal.ndsm = (s * est_nd).astype(np.float32)
                dsm = (cal.dtm + cal.ndsm).astype(np.float32)
                cal.scale_k = (cal.scale_k or 1.0) * s
                cal.extras["ndsm"] = cal.ndsm
                cal.note += f" Above-ground height scale multiplied by {s:.3f} from {n} Copernicus cells."
                log(f"  Copernicus height scale s={s:.4f} ({n} cells); terrain unchanged")
        except (RuntimeError, ValueError, OSError) as exc:
            cop_scale_dem = None
            log(f"  WARNING: Copernicus height scale skipped: {exc}")

    name = "dsm.tif" if units == "metre" else "rdsm.tif"
    dio.write_dsm(out / name, dsm, img, units=units,
                  description=f"DepthWizard {cal.method} ({backbone})", vertical_datum=datum)
    if units == "metre" and cal.dtm is not None:
        dio.write_dsm(out / "dtm.tif", cal.dtm, img, units=units,
                      description=f"DepthWizard bare-earth DTM ({backbone})", vertical_datum=datum)
    if units == "metre" and cal.ndsm is not None:
        dio.write_dsm(out / "ndsm.tif", cal.ndsm, img, units=units,
                      description=f"DepthWizard above-ground heights nDSM ({backbone})",
                      vertical_datum=datum, compound_vertical=False)

    # per-pixel 1-sigma uncertainty from the rotation ensemble, in output units
    std_rel = dinfo.get("std_rel")
    unc_units = None
    ensemble_available = (backbone != "heuristic-fallback" and dinfo.get("tta", 0) > 1
                          and std_rel is not None)
    if ensemble_available:
        unc_units = std_rel * (cal.scale_k if units == "metre" and cal.scale_k else 1.0)
        if units == "metre":
            # exported error bar: calibrated 1-sigma in metres (see uncertainty.py);
            # the raw spread stays available and still drives the relative
            # reliability (confidence) layer in the viewer
            from .uncertainty import calibrated_sigma
            dio.write_dsm(out / "ensemble_spread.tif", unc_units, img, units="metre",
                          description="1-sigma spread of the rotation ensemble (relative reliability, not an error bar)")
            dio.write_dsm(out / "uncertainty.tif", calibrated_sigma(unc_units), img, units="metre",
                          description="calibrated 1-sigma height error (provisional; see uncertainty.py)")
        else:
            dio.write_dsm(out / "uncertainty.tif", unc_units, img, units="relative",
                          description="1-sigma spread of the rotation ensemble")

    meta = {
        "input": Path(image_path).name,
        "input_paths": {k: str(Path(v).resolve()) for k, v in
                        (("image", image_path), ("dem", dem), ("gcp", gcp), ("reference", reference)) if v},
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
        "has_uncertainty": ensemble_available,
        "has_confidence": ensemble_available,
        "uncertainty_status": ("provisional - two-scene error calibration" if units == "metre"
                               else "ensemble agreement only - not an error bar")
                              if ensemble_available else "unavailable - no model ensemble",
        "water_fraction": meta_water,
        "agl_model": is_agl,
        "learned_scale_m_per_unit": learned,
        "timing_s": {"depth": round(t_depth, 2)},
        "sun_input": {"elevation_deg": sun_elevation, "azimuth_deg": sun_azimuth},
    }
    if cop_scale_dem:
        meta["input_paths"]["copernicus_scale"] = str(Path(cop_scale_dem).resolve())
    if units == "relative":
        # display scale for the viewer: relief ~ 8% of scene width unless given
        meta["display_height_m"] = relative_display_height_m or 0.08 * max(img.shape) * gsd
    view_h = dsm if units == "metre" else dsm * meta["display_height_m"]
    unc_view = None
    if unc_units is not None:
        unc_view = unc_units if units == "metre" else unc_units * meta["display_height_m"]

    semantic_labels = None
    semantic_info = {"status": "disabled"}
    semantic_model = semantic_model or os.environ.get("DEPTHWIZARD_SEMANTIC_CHECKPOINT")
    if semantic_model and units == "metre" and gsd < COARSE_GSD_M:
        from .semantic import SemanticSegmenter
        log("experimental overhead semantic segmentation")
        segmenter = SemanticSegmenter(semantic_model, device=device)
        semantic_labels, semantic_info = segmenter.predict(img.rgb, log=log)
        del segmenter
        import rasterio
        with rasterio.open(out / "dsm.tif") as src:
            profile = src.profile.copy()
        profile.update(count=1, dtype="uint8", nodata=0)
        with rasterio.open(out / "semantic.tif", "w", **profile) as dst:
            dst.write(semantic_labels, 1)
            dst.update_tags(CLASSES=json.dumps(semantic_info["classes"]),
                            SOURCE="experimental semantic classifier; not height truth")
    elif semantic_model:
        semantic_info = {"status": "skipped", "reason": "requires metric imagery finer than 2.5 m/px"}
    meta["semantic_segmentation"] = semantic_info
    if semantic_labels is None:
        (out / "semantic.tif").unlink(missing_ok=True)
    log("extracting LoD1 building footprints")
    b_dtm = cal.dtm if units == "metre" else None
    min_h = 2.5 if units == "metre" else 0.12 * float(np.percentile(view_h - view_h.min(), 98))
    if gsd >= COARSE_GSD_M:
        # buildings are 1-3 pixels at >= 2.5 m: footprints would be whole blocks
        # with meaningless heights, so LoD1 extraction is skipped
        min_h = float("inf")
        log(f"  coarse image ({gsd:.1f} m): LoD1 building extraction skipped")
    buildings = extract_buildings(view_h, dtm=b_dtm, gsd=gsd, world_w=img.shape[1] * gsd,
                                  world_h=img.shape[0] * gsd, rgb=img.rgb,
                                  uncertainty_m=unc_view, min_height_m=max(min_h, 1e-3),
                                  edge_refine_for_footprints=True,
                                  semantic_labels=semantic_labels,
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
            b["confidence_basis"] = ("relative ensemble agreement" if ensemble_available
                                     else "relative roof-height consistency")
    log(f"  detected {buildings['count']} buildings (footprint {buildings['total_footprint_m2']} m²)")
    meta["buildings_count"] = buildings["count"]
    meta["total_footprint_m2"] = buildings["total_footprint_m2"]

    # Automatic anchors are independent height cues, not reference validation.
    # Only a consistent group may change metric scale; all candidates/rejections
    # remain visible in metadata for audit and manual review.
    auto_applied = False
    # Keep the optional segmentation experiment separate from automatic scale
    # fitting: changing candidate IDs must not silently select new scale cues.
    if semantic_labels is not None:
        log("  automatic height anchors skipped for the segmentation experiment; supplied anchors remain explicit")
        meta["semantic_segmentation"]["automatic_anchors"] = "skipped to isolate mask changes"
    if semantic_labels is None and units == "metre" and cal.ndsm is not None and cal.dtm is not None and labels is not None and buildings["count"] and not anchors:
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
                
            np.add(dsm, (s - 1) * cal.ndsm, out=dsm)
            np.multiply(cal.ndsm, s, out=cal.ndsm)
            
            cal.scale_k = (cal.scale_k or 1.0) * s
            
            if unc_units is not None:
                unc_units = unc_units * s
                
            dio.write_dsm(out / "dsm.tif", dsm, img, units="metre", description=f"DepthWizard height-anchor ({backbone})", vertical_datum=datum)
            dio.write_dsm(out / "ndsm.tif", cal.ndsm, img, units="metre", description=f"DepthWizard above-ground heights nDSM ({backbone})", vertical_datum=datum)
            if unc_units is not None:
                from .uncertainty import calibrated_sigma
                dio.write_dsm(out / "ensemble_spread.tif", unc_units, img, units="metre",
                              description="1-sigma spread of the rotation ensemble (relative reliability, not an error bar)")
                dio.write_dsm(out / "uncertainty.tif", calibrated_sigma(unc_units), img, units="metre",
                              description="calibrated 1-sigma height error (provisional; see uncertainty.py)")
            
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
    metrics = {}
    if reference:
        log("validating against reference")
        ref = reference_on_grid(reference, img)
        baseline = getattr(cal, "extras", {}).get("dem") if units == "metre" else None
        metrics = evaluate(dsm, ref, units, rgb=img.rgb, gsd=gsd, baseline=baseline)
        if units == "metre" and labels is not None and buildings["count"] >= 5:
            est_nd = dsm - (cal.dtm if cal.dtm is not None else np.percentile(dsm, 2))
            bm = building_level(labels, est_nd, ref, gsd)
            if bm:
                metrics["buildings"] = bm
                log(f"  per-building heights vs reference: n={bm['n']} "
                    f"RMSE {bm['rmse']:.2f} m, r {bm['r']:.2f}")
    cop_score_dem = cop_scale_dem or (dem if effective_source == "COP30" else None)
    if cop_score_dem and units == "metre":
        try:
            agreement = vs_copernicus_30m(dsm, img, cop_score_dem)
            agreement["note"] = ("Copernicus was the calibration input; this measures consistency, "
                                 "not independent accuracy")
            metrics["vs_copernicus_30m"] = agreement
            log(f"  agreement with Copernicus GLO-30: n={agreement['n']} "
                f"RMSE {agreement['rmse']:.2f} m, bias {agreement['bias']:.2f} m")
        except ValueError as exc:
            log(f"  WARNING: Copernicus agreement skipped: {exc}")
    if metrics:
        meta["metrics"] = metrics
        (out / "metrics.json").write_text(json.dumps(metrics, indent=2))

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        hashes = list(executor.map(_file_sha256, [image_path, dem, gcp, reference]))
        
    meta["evidence_bundle"] = {
        "image_sha256": hashes[0],
        "dem_sha256": hashes[1],
        "gcp_sha256": hashes[2],
        "reference_sha256": hashes[3],
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
        confidence_map = None
    meta["confidence_definition"] = ("exp(-spread / 2 m), spread = rotation-ensemble spread; "
                                     "a relative reliability index, not a probability"
                                     if units == "metre" else "relative ensemble agreement; not a probability") if ensemble_available else "unavailable - no model ensemble"
    if units == "metre" and unc_units is not None:
        from .uncertainty import PROVENANCE
        meta["uncertainty_calibration"] = dict(PROVENANCE, file="uncertainty.tif",
                                               raw_spread_file="ensemble_spread.tif")

    meta["timing_s"]["total"] = round(time.time() - t0, 2)
    dio.export_viewer_assets(
        out / "viewer", img, view_h, meta, reference=view_ref,
        dtm=view_dtm, confidence=confidence_map, buildings=buildings,
        baseline=getattr(cal, "extras", {}).get("dem") if units == "metre" else None,
        uncertainty=unc_view, susceptibility=susc, semantic_labels=semantic_labels)
    dio.save_preview(out / "preview.png", view_h, gsd=gsd)
    (out / "meta.json").write_text(json.dumps(_clean_numpy(meta), separators=(',', ':'), default=float))
    log(f"done in {meta['timing_s']['total']} s -> {out}")
    return meta
