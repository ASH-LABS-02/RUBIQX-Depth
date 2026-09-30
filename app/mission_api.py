"""Mission screening endpoints, kept separate from the upload/viewer server.

Mount with ``app.include_router(create_mission_router(_completed_scene, _files_lock))``
before the catch-all ``app.mount('/', ...)`` in ``app.server``.  The resolver
must reject scene IDs outside the application's jobs directory.
"""
from __future__ import annotations

import json
import math
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import rasterio
from rasterio.warp import transform as transform_coordinates
from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel

from depthwizard.analysis import labels_from_buildings, landslide_susceptibility
from depthwizard.disaster import (
    connected_flood_mask,
    evacuation_route,
    landslide_runout,
    population_exposure,
    relay_visibility,
    vertical_shelters,
)


MAX_MISSION_CELLS = 16_000_000
MAX_PROJECTION_SCALE_ERROR = 0.02


class MissionRequest(BaseModel):
    """All coordinates are normalised optical-image ``u, v`` in [0, 1]."""

    action: Literal["route", "shelters", "population", "runout", "relay"]
    u: float | None = None
    v: float | None = None
    water_level_m: float | None = None
    flood_source: Literal["edge", "point", "plane"] = "edge"
    flood_seed_u: float | None = None
    flood_seed_v: float | None = None
    clearance_m: float = 2.0
    max_slope_deg: float = 30.0
    floor_area_per_person_m2: float = 30.0
    occupancy_fraction: float = 1.0
    min_confidence: float = 0.65
    min_roof_freeboard_m: float = 2.0
    susceptibility_threshold: float = 0.7
    observer_agl_m: float = 20.0
    target_agl_m: float = 1.5
    max_range_m: float = 3000.0


def _finite_bounded(value: float, name: str, lo: float, hi: float) -> float:
    v = float(value)
    if not math.isfinite(v) or not lo <= v <= hi:
        raise HTTPException(422, f"{name} must be finite and within [{lo}, {hi}]")
    return v


def _point_rc(u: float | None, v: float | None, shape: tuple[int, int], name: str) -> tuple[int, int]:
    if u is None or v is None:
        raise HTTPException(422, f"{name} requires normalized u and v coordinates")
    uu = _finite_bounded(u, "u", 0.0, 1.0)
    vv = _finite_bounded(v, "v", 0.0, 1.0)
    return int(round(vv * (shape[0] - 1))), int(round(uu * (shape[1] - 1)))


def _read_raster(path: Path) -> tuple[np.ndarray, dict]:
    try:
        with rasterio.open(path) as src:
            if src.count < 1:
                raise HTTPException(409, f"{path.name} has no raster band")
            # Check the raster header before allocating a potentially huge array.
            if src.height * src.width > MAX_MISSION_CELLS:
                raise HTTPException(413, "mission raster exceeds 16 million cells; crop the scene")
            arr = src.read(1, masked=True).filled(np.nan).astype(np.float32)
            return arr, {"crs": src.crs, "transform": src.transform,
                         "shape": (src.height, src.width)}
    except rasterio.errors.RasterioIOError as exc:
        raise HTTPException(409, f"cannot read {path.name}: {exc}") from exc


def _ellipsoidal_step_m(lon0: float, lat0: float, lon1: float, lat1: float) -> float:
    """Accurate local WGS84 ground distance for adjacent raster pixel centres."""
    a = 6_378_137.0
    e2 = 0.0066943799901413165
    lat = math.radians((lat0 + lat1) / 2)
    sin_lat = math.sin(lat)
    denom = 1 - e2 * sin_lat * sin_lat
    meridian_radius = a * (1 - e2) / denom ** 1.5
    prime_radius = a / math.sqrt(denom)
    d_lat = math.radians(lat1 - lat0)
    d_lon = math.radians((lon1 - lon0 + 180) % 360 - 180)
    return math.hypot(meridian_radius * d_lat, prime_radius * math.cos(lat) * d_lon)


def _validate_ground_scale(crs, transform, shape: tuple[int, int], unit_factor: float) -> tuple[float, float]:
    """Check projected pixel size against true ground distance across the crop.

    A metre axis unit alone is insufficient: Web Mercator, for example, has
    substantial distance distortion away from the equator.  The route and
    viewshed grids assume a nearly constant, nearly square ground resolution.
    """
    label = crs.to_wkt().lower()
    if crs.to_epsg() == 3857 or any(term in label for term in (
            "pseudo-mercator", "web mercator", "auxiliary sphere")):
        raise HTTPException(422, "mission analysis needs a local projected metre CRS; Web Mercator is unsuitable for ground distances")
    h, w = shape
    sample_rc = [(0, 0), (0, w - 2), (h - 2, 0), (h - 2, w - 2), (h // 2, w // 2)]
    xs, ys = [], []
    for row, col in sample_rc:
        for dc, dr in ((0, 0), (1, 0), (0, 1)):
            x, y = transform * (col + dc + 0.5, row + dr + 0.5)
            xs.append(x)
            ys.append(y)
    try:
        lon, lat = transform_coordinates(crs, "EPSG:4326", xs, ys)
    except Exception as exc:  # malformed or unsupported source CRS
        raise HTTPException(422, "scene CRS cannot be converted to ground coordinates") from exc
    if not all(math.isfinite(v) for v in (*lon, *lat)):
        raise HTTPException(422, "scene CRS produced invalid ground coordinates")
    map_x = math.hypot(transform.a, transform.d) * unit_factor
    map_y = math.hypot(transform.b, transform.e) * unit_factor
    ground_steps = []
    errors = []
    for i in range(len(sample_rc)):
        j = 3 * i
        gx = _ellipsoidal_step_m(lon[j], lat[j], lon[j + 1], lat[j + 1])
        gy = _ellipsoidal_step_m(lon[j], lat[j], lon[j + 2], lat[j + 2])
        if min(gx, gy) <= 0:
            raise HTTPException(422, "scene CRS has an invalid ground pixel size")
        if max(gx, gy) / min(gx, gy) > 1.1:
            raise HTTPException(422, "mission analysis requires approximately square ground pixels")
        ground_steps.extend((gx, gy))
        errors.extend((abs(map_x / gx - 1), abs(map_y / gy - 1)))
    worst = max(errors)
    if worst > MAX_PROJECTION_SCALE_ERROR:
        raise HTTPException(422, f"scene projection distorts ground distances by up to {worst * 100:.1f}%; use a local metric CRS")
    return float(sum(ground_steps) / len(ground_steps)), worst


def _load_scene(folder: Path) -> dict:
    viewer = folder / "viewer"
    meta_path = viewer / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(404, "completed scene not found")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "scene metadata is unreadable") from exc
    if meta.get("units") != "metre":
        raise HTTPException(422, "mission analyses need an absolute metric DSM and DTM")
    if not meta.get("georeferenced"):
        raise HTTPException(422, "mission analyses require georeferenced metric imagery")
    dtm_path, dsm_path = folder / "dtm.tif", folder / "dsm.tif"
    if not dtm_path.is_file() or not dsm_path.is_file():
        raise HTTPException(409, "metric scene is missing dsm.tif or dtm.tif")
    dtm, dp = _read_raster(dtm_path)
    dsm, sp = _read_raster(dsm_path)
    shape = dtm.shape
    if shape != dsm.shape or dp["crs"] != sp["crs"] or not np.allclose(
            tuple(dp["transform"])[:6], tuple(sp["transform"])[:6], rtol=0, atol=1e-7):
        raise HTTPException(409, "DSM and DTM do not share the same grid and CRS")
    if meta.get("src_w") not in (None, shape[1]) or meta.get("src_h") not in (None, shape[0]):
        raise HTTPException(409, "viewer metadata does not match the full-resolution raster")
    if meta.get("transform") and not np.allclose(
            meta["transform"], tuple(dp["transform"])[:6], rtol=0, atol=1e-7):
        raise HTTPException(409, "viewer metadata transform does not match the DSM")

    crs = dp["crs"]
    if crs is None or not crs.is_projected:
        raise HTTPException(422, "mission analysis requires a projected metre CRS; reproject the scene")
    try:
        unit_factor = float(crs.linear_units_factor[1])
    except (TypeError, ValueError, rasterio.errors.CRSError) as exc:
        raise HTTPException(422, "projected CRS does not specify metre units") from exc
    if abs(unit_factor - 1.0) > 0.001:
        raise HTTPException(422, "mission analysis requires a metre-based projected CRS")
    transform = dp["transform"]
    pixel_x = math.hypot(transform.a, transform.d)
    pixel_y = math.hypot(transform.b, transform.e)
    if min(pixel_x, pixel_y) <= 0 or max(pixel_x, pixel_y) / min(pixel_x, pixel_y) > 1.1:
        raise HTTPException(422, "mission analysis requires approximately square ground pixels")
    map_gsd = (pixel_x + pixel_y) / 2
    if meta.get("gsd_m") is not None and abs(float(meta["gsd_m"]) - map_gsd) / map_gsd > 0.1:
        raise HTTPException(409, "viewer GSD disagrees with the full-resolution raster")
    gsd, scale_error = _validate_ground_scale(crs, transform, shape, unit_factor)

    buildings_path = viewer / "buildings.json"
    if buildings_path.is_file():
        try:
            buildings = json.loads(buildings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(409, "building data is unreadable") from exc
    else:
        buildings = {"buildings": []}
    label_path = folder / "building_labels.npy"
    if label_path.is_file():
        try:
            labels = np.load(label_path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, "building label raster is unreadable") from exc
        if labels.shape != shape or labels.ndim != 2 or not np.issubdtype(labels.dtype, np.integer):
            raise HTTPException(409, "building label raster does not align with the DSM")
        if np.any(labels < 0):
            raise HTTPException(409, "building label raster has invalid negative IDs")
        label_source = "full-resolution building_labels.npy"
    else:
        labels = labels_from_buildings(buildings, shape)
        label_source = "viewer building footprints rasterized onto full-resolution grid"
    return {"meta": meta, "dtm": dtm, "dsm": dsm, "labels": labels,
            "buildings": buildings, "gsd_m": gsd, "shape": shape,
            "label_source": label_source, "projection_scale_error": scale_error}


def _lowest_edge_seed(dtm: np.ndarray) -> np.ndarray:
    """Match the viewer's lowest 2% boundary source convention."""
    boundary = np.zeros(dtm.shape, dtype=bool)
    boundary[0, :] = boundary[-1, :] = True
    boundary[:, 0] = boundary[:, -1] = True
    points = np.argwhere(boundary & np.isfinite(dtm))
    seeds = np.zeros(dtm.shape, dtype=bool)
    if points.size:
        n = max(4, round(len(points) * 0.02))
        values = dtm[points[:, 0], points[:, 1]]
        chosen = points[np.argpartition(values, min(n, len(points)) - 1)[:n]]
        seeds[chosen[:, 0], chosen[:, 1]] = True
    return seeds


def _flood(scene: dict, body: MissionRequest) -> tuple[np.ndarray, float]:
    if body.water_level_m is None:
        raise HTTPException(422, f"{body.action} requires water_level_m")
    level = _finite_bounded(body.water_level_m, "water_level_m", -12_000.0, 12_000.0)
    dtm = scene["dtm"]
    if body.flood_source == "plane":
        return np.isfinite(dtm) & (dtm <= level), level
    if body.flood_source == "point":
        rc = _point_rc(body.flood_seed_u, body.flood_seed_v, dtm.shape, "point flood source")
        if not np.isfinite(dtm[rc]) or dtm[rc] > level:
            raise HTTPException(422, "point flood source is above water level or has no terrain value")
        seeds = np.zeros(dtm.shape, dtype=bool)
        seeds[rc] = True
    else:
        seeds = _lowest_edge_seed(dtm)
    return connected_flood_mask(dtm, level, water_seed_mask=seeds), level


def _susceptibility(folder: Path, scene: dict) -> tuple[np.ndarray, str]:
    viewer = folder / "viewer"
    vm = scene["meta"]
    path = viewer / "susc.bin"
    if path.is_file():
        gw, gh = vm.get("grid_w"), vm.get("grid_h")
        if not isinstance(gw, int) or not isinstance(gh, int) or gw < 2 or gh < 2:
            raise HTTPException(409, "viewer susceptibility dimensions are invalid")
        if path.stat().st_size != gw * gh * 4:
            raise HTTPException(409, "viewer susceptibility layer has the wrong byte length")
        src = np.fromfile(path, dtype="<f4").reshape(gh, gw)
        if not np.isfinite(src).all():
            raise HTTPException(409, "viewer susceptibility layer contains invalid cells")
        # PIL's bilinear pixel-centre mapping is the same resampling convention
        # used when the full-resolution source was exported to viewer assets.
        h, w = scene["shape"]
        aligned = np.asarray(Image.fromarray(src).resize((w, h), Image.BILINEAR), dtype=np.float32)
        return np.clip(aligned, 0, 1), "viewer/susc.bin bilinearly aligned to full-resolution DTM"
    index, _ = landslide_susceptibility(scene["dtm"], None, scene["gsd_m"])
    return index, "calculated from full-resolution DTM; neutral vegetation factor"


def create_mission_router(
    resolve_scene: Callable[[str], Path],
    scene_lock=None,
) -> APIRouter:
    """Create an API router using the host app's validated scene resolver.

    Pass the inference/rescale lock when available so a request cannot read a
    half-written DSM while height anchors are being applied.
    """
    router = APIRouter()

    @router.post("/api/scenes/{job_id}/mission")
    def mission(job_id: str, body: MissionRequest):
        folder = Path(resolve_scene(job_id))
        with (scene_lock if scene_lock is not None else nullcontext()):
            scene = _load_scene(folder)
            if body.action == "runout":
                susc, susc_source = _susceptibility(folder, scene)
            else:
                susc = susc_source = None
        dtm, dsm, labels = scene["dtm"], scene["dsm"], scene["labels"]
        buildings, gsd = scene["buildings"], scene["gsd_m"]
        provenance = {
            "surface": "full-resolution dsm.tif",
            "ground": "full-resolution dtm.tif",
            "buildings": "viewer/buildings.json",
            "building_labels": scene["label_source"],
            "calibration_evidence": scene["meta"].get("calibration", {}).get("evidence_level"),
            "vertical_datum": scene["meta"].get("vertical_datum"),
            "max_projection_scale_error_pct": round(scene["projection_scale_error"] * 100, 3),
        }
        if body.action in ("route", "shelters", "population"):
            flood, level = _flood(scene, body)
            provenance["flood_source"] = body.flood_source
            provenance["flood_model"] = "four-connected bathtub inundation; no hydraulics or drainage"
        if body.action == "route":
            origin = _point_rc(body.u, body.v, scene["shape"], "route")
            clearance = _finite_bounded(body.clearance_m, "clearance_m", 0.0, 100.0)
            max_slope = _finite_bounded(body.max_slope_deg, "max_slope_deg", 1.0, 60.0)
            result = evacuation_route(dtm, origin, level, gsd, flood_mask=flood,
                                      building_labels=labels, clearance_m=clearance,
                                      max_slope_deg=max_slope)
        elif body.action == "shelters":
            confidence = _finite_bounded(body.min_confidence, "min_confidence", 0.0, 1.0)
            freeboard = _finite_bounded(body.min_roof_freeboard_m, "min_roof_freeboard_m", 0.0, 100.0)
            result = vertical_shelters(buildings, level, min_confidence=confidence,
                                       min_roof_freeboard_m=freeboard)
            # The supplied flood mask marks whether the footprint is actually
            # inundated, rather than relying only on a scalar ground estimate.
            footprint = np.bincount(labels.ravel())
            wet = np.bincount(labels[flood], minlength=len(footprint))
            for candidate in result.get("candidates", []):
                ident = candidate["building_id"]
                if isinstance(ident, int) and 0 < ident < len(footprint) and footprint[ident]:
                    candidate["flooded_footprint_fraction"] = round(float(wet[ident] / footprint[ident]), 3)
        elif body.action == "population":
            area_per_person = _finite_bounded(body.floor_area_per_person_m2,
                                              "floor_area_per_person_m2", 5.0, 200.0)
            occupancy = _finite_bounded(body.occupancy_fraction, "occupancy_fraction", 0.0, 1.0)
            result = population_exposure(buildings, level, flood_mask=flood,
                                         building_labels=labels,
                                         floor_area_per_person_m2=area_per_person,
                                         occupancy_fraction=occupancy)
        elif body.action == "runout":
            threshold = _finite_bounded(body.susceptibility_threshold,
                                        "susceptibility_threshold", 0.0, 1.0)
            result = landslide_runout(dtm, susc, gsd, building_labels=labels,
                                      threshold=threshold)
            provenance["susceptibility"] = susc_source
        else:  # relay
            origin = _point_rc(body.u, body.v, scene["shape"], "relay")
            observer_h = _finite_bounded(body.observer_agl_m, "observer_agl_m", 0.0, 500.0)
            target_h = _finite_bounded(body.target_agl_m, "target_agl_m", 0.0, 100.0)
            maximum = _finite_bounded(body.max_range_m, "max_range_m", 1.0, 20_000.0)
            result = relay_visibility(dsm, origin, gsd, observer_agl_m=observer_h,
                                      target_agl_m=target_h, max_range_m=maximum,
                                      building_labels=labels)
        return {"action": body.action, "scene_id": job_id,
                "grid_w": scene["shape"][1], "grid_h": scene["shape"][0],
                "gsd_m": round(gsd, 6), **result, "provenance": provenance}

    return router
