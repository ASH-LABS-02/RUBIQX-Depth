"""End-to-end pipeline: image -> relative height -> (calibrated) DSM -> viewer assets."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import io as dio
from .buildings import extract_buildings
from .calibrate import calibrate, fetch_srtm
from .depth import relative_height
from .metrics import evaluate, reference_on_grid


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
        match_dem_30m=True, tta=False, log=print) -> dict:
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    log("reading image")
    img = dio.read_image(image_path)
    gsd = img.pixel_size_m or assumed_gsd_m
    log(f"  {img.shape[1]}x{img.shape[0]} px, georeferenced={img.georeferenced}"
        + (f", GSD≈{gsd:.2f} m" if img.pixel_size_m else ""))

    if fetch_dem and not dem and img.georeferenced:
        log(f"fetching {dem_source} DEM for footprint")
        dem = str(fetch_srtm(img, out / "dem.tif", demtype=dem_source))

    log(f"relative height ({model})")
    t1 = time.time()
    rel, backbone, unc = relative_height(
        img.rgb, model=model, allow_fallback=allow_fallback, device=device,
        tta=tta, return_uncertainty=True,
    )
    t_depth = time.time() - t1
    rel = ndimage.median_filter(rel, 3)  # suppress tile/speckle artefacts

    # Detect if backbone is AGL-trained (GAMUS fine-tune)
    is_agl = "gamus" in str(model).lower() or "agl" in str(model).lower() or "gamus" in backbone.lower()
    datum = "EGM2008" if dem_source == "COP30" else "EGM96" if dem else "WGS84"

    log(f"scale calibration (is_agl={is_agl}, datum={datum}, match_30m={match_dem_30m})")
    dsm, units, cal = calibrate(
        rel, img, dem_path=dem, gcp_path=gcp, scene=scene,
        is_agl=is_agl, match_dem_30m=match_dem_30m, vertical_datum=datum,
    )
    log(f"  method={cal.method} {cal.note}")

    name = "dsm.tif" if units == "metre" else "rdsm.tif"
    dio.write_dsm(out / name, dsm, img, units=units,
                  description=f"DepthWizard {cal.method} ({backbone})",
                  vertical_datum=datum if units == "metre" else None)

    # Save additional rasters when available (Part 8: E1)
    if units == "metre" and cal.dtm is not None:
        dio.write_dsm(out / "dtm.tif", cal.dtm, img, units=units,
                      description=f"DepthWizard bare-earth DTM ({backbone})",
                      vertical_datum=datum)
    if units == "metre" and cal.ndsm is not None:
        dio.write_dsm(out / "ndsm.tif", cal.ndsm, img, units=units,
                      description=f"DepthWizard normalised DSM above-ground ({backbone})",
                      vertical_datum=datum)

    log("extracting LoD1 building footprints")
    world_w = img.shape[1] * gsd
    world_h = img.shape[0] * gsd
    buildings = extract_buildings(
        dsm, dtm=cal.dtm if units == "metre" else None,
        gsd=gsd, world_w=world_w, world_h=world_h,
    )
    log(f"  detected {buildings['count']} buildings (total footprint {buildings['total_footprint_m2']} m²)")

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
        "buildings_count": buildings["count"],
        "total_footprint_m2": buildings["total_footprint_m2"],
        "timing_s": {"depth": round(t_depth, 2)},
        "evidence_bundle": {
            "image_sha256": _file_sha256(image_path),
            "dem_sha256": _file_sha256(dem),
            "model_identifier": backbone,
            "calibration_method": cal.method,
            "scale_k": cal.scale_k,
            "software_version": "DepthWizard 2.1 (SIH26175)",
        },
    }
    if units == "relative":
        # display scale for the viewer: relief ~ 8% of scene width unless given
        meta["display_height_m"] = relative_display_height_m or 0.08 * max(img.shape) * gsd

    ref = None
    if reference:
        log("validating against reference")
        ref = reference_on_grid(reference, img)
        baseline = None
        if dem and img.georeferenced:
            from .calibrate import dem_to_grid
            baseline = dem_to_grid(dem, img)
        meta["metrics"] = evaluate(dsm, ref, units, rgb=img.rgb,
                                   gsd=gsd, baseline=baseline)
        (out / "metrics.json").write_text(json.dumps(meta["metrics"], indent=2))

    view_h = dsm if units == "metre" else dsm * meta["display_height_m"]
    view_ref = ref if units == "metre" else None
    view_dtm = cal.dtm if units == "metre" else None
    confidence_map = np.clip(1.0 - unc, 0.0, 1.0).astype(np.float32)

    meta["timing_s"]["total"] = round(time.time() - t0, 2)
    dio.export_viewer_assets(
        out / "viewer", img, view_h, meta, reference=view_ref,
        dtm=view_dtm, confidence=confidence_map, buildings=buildings,
    )
    dio.save_preview(out / "preview.png", view_h, gsd=gsd)
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=float))
    log(f"done in {meta['timing_s']['total']} s -> {out}")
    return meta
