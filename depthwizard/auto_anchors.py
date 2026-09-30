"""Conservative, provenance-bearing automatic building-height anchors.

Shadow geometry is usable only when the solar elevation is known (from input
metadata or a caller). A shadow seen in one RGB image cannot determine both
sun elevation and building height. OSM ``height`` is a mapped maximum height;
``building:levels`` is converted using a deliberately labelled 3 m/level
heuristic. Neither is independent survey ground truth.

The public functions return ``(anchors, diagnostics)``. An anchor contains the
``building_id`` and ``height_m`` expected by ``calibrate.apply_height_anchors``
plus evidence fields. No global scale is changed in this module: callers should
inspect diagnostics and preserve the calibration provenance when applying it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import rasterize
from rasterio.warp import transform as transform_points
from scipy import ndimage

from .analysis import water_mask as detect_water
from .shadows import dominant_shadow_azimuth, observed_shadow_mask

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "osm"
_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m|metres?|meters?|ft|feet|')?\s*$", re.I)
_FEET_INCHES = re.compile(r"^\s*(\d+)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?\s*$")


def parse_osm_height_m(raw: Any) -> float | None:
    """Parse a single OSM height value; reject lists, ranges and odd units."""
    if raw is None:
        return None
    s = str(raw).strip()
    feet_inches = _FEET_INCHES.fullmatch(s)
    if feet_inches and ('"' in s or feet_inches.group(2) is not None):
        value = (float(feet_inches.group(1)) * 12 + float(feet_inches.group(2) or 0)) * 0.0254
    else:
        match = _NUMBER.fullmatch(s)
        if not match:
            return None
        value = float(match.group(1))
        if (match.group(2) or "").lower() in ("ft", "feet", "'"):
            value *= 0.3048
    return round(value, 3) if 1.5 <= value <= 250 and math.isfinite(value) else None


def _metadata_tags(path: str | Path) -> dict[str, str]:
    try:
        import rasterio

        with rasterio.open(path) as src:
            tags = {str(k).lower(): str(v) for k, v in src.tags().items()}
            for ns in src.tag_namespaces():
                if ns in ("IMAGE_STRUCTURE", "DERIVED_SUBDATASETS"):
                    continue
                tags.update({str(k).lower(): str(v) for k, v in src.tags(ns=ns).items()})
            return tags
    except Exception:  # ordinary PNG/JPEG, or TIFF without readable tags
        return {}


def sun_angles_from_metadata(image: Any) -> dict[str, Any]:
    """Read explicit solar angles from GeoTIFF tags; never invent an elevation.

    Raster providers use different names, so the source tag is returned for
    provenance. The value must be in degrees above the horizon for elevation
    and degrees clockwise from north for azimuth.
    """
    path = getattr(image, "path", image)
    tags = _metadata_tags(path)

    def read(names: tuple[str, ...], lo: float, hi: float):
        for name in names:
            for key, value in tags.items():
                if key == name or key.endswith("_" + name) or key.endswith(":" + name):
                    try:
                        number = float(value)
                    except (ValueError, TypeError):
                        continue
                    if math.isfinite(number) and lo <= number <= hi:
                        return number, key
        return None, None

    elevation, elevation_tag = read(
        ("sun_elevation", "sun_elevation_angle", "solar_elevation", "solar_elevation_angle", "mean_sun_elevation"),
        0.0, 90.0,
    )
    azimuth, azimuth_tag = read(
        ("sun_azimuth", "sun_azimuth_angle", "solar_azimuth", "solar_azimuth_angle", "mean_sun_azimuth"),
        0.0, 360.0,
    )
    return {"elevation_deg": elevation, "azimuth_deg": azimuth,
            "elevation_tag": elevation_tag, "azimuth_tag": azimuth_tag}


def _shadow_pixel_direction(image: Any, azimuth_deg: float, gsd_m: float) -> tuple[float, float, float]:
    """Return shadow (row, col) unit vector and metres per pixel along it.

    For CRS inputs, a 10 m local east/north displacement is transformed through
    the image CRS and inverse affine. This handles rotated imagery and unequal
    pixel scales without interpreting angular CRS units as metres.
    """
    az = math.radians(azimuth_deg)
    if getattr(image, "georeferenced", False) and getattr(image, "crs", None):
        h, w = image.shape
        x0, y0 = image.transform * (w / 2, h / 2)
        lon, lat = transform_points(image.crs, "EPSG:4326", [x0], [y0])
        lat0, lon0 = float(lat[0]), float(lon[0])
        cos_lat = max(math.cos(math.radians(lat0)), 0.05)
        east_m, north_m = 10 * math.sin(az), 10 * math.cos(az)
        lon1 = lon0 + east_m / (111_320 * cos_lat)
        lat1 = lat0 + north_m / 111_133
        x1, y1 = transform_points("EPSG:4326", image.crs, [lon1], [lat1])
        c0, r0 = ~image.transform * (x0, y0)
        c1, r1 = ~image.transform * (x1[0], y1[0])
        dr, dc = -(r1 - r0), -(c1 - c0)  # shadow runs away from the sun
        norm = math.hypot(dr, dc)
        if norm < 1e-6:
            raise ValueError("image transform has no usable ground scale")
        return dr / norm, dc / norm, 10 / norm
    if not (math.isfinite(gsd_m) and gsd_m > 0):
        raise ValueError("positive pixel ground spacing required")
    return math.cos(az), -math.sin(az), float(gsd_m)


def shadow_height_anchors(
    image: Any,
    labels: np.ndarray,
    buildings: list[dict[str, Any]],
    *,
    gsd_m: float,
    sun_elevation_deg: float | None,
    sun_azimuth_deg: float | None,
    ndsm: np.ndarray | None = None,
    dtm: np.ndarray | None = None,
    water: np.ndarray | None = None,
    max_anchors: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure clear shadows cast by detected buildings.

    A usable ray must start at the anti-sun building edge, remain dark for a
    measurable length, terminate at brighter ground, and avoid other buildings
    and open water. Ambiguous or image-clipped shadows are discarded. The RGB
    detector is intentionally conservative; it will miss many valid shadows.
    """
    rgb = np.asarray(image.rgb)
    labels = np.asarray(labels)
    diagnostics: dict[str, Any] = {"source": "RGB shadow geometry", "candidates": len(buildings),
                                   "accepted": 0, "rejected": {}, "sun_elevation_deg": sun_elevation_deg,
                                   "sun_azimuth_deg": sun_azimuth_deg,
                                   "notes": "single-image shadow length needs known solar elevation"}
    if labels.shape != rgb.shape[:2]:
        raise ValueError("building labels and RGB image must share one pixel grid")
    if sun_elevation_deg is None or not 8 <= sun_elevation_deg <= 82:
        diagnostics["skipped"] = "known solar elevation between 8 and 82 degrees is required"
        return [], diagnostics
    dark = observed_shadow_mask(rgb)
    if water is None:
        water = detect_water(rgb, gsd_m)
    water = np.asarray(water, dtype=bool)
    if water.shape != labels.shape:
        raise ValueError("water mask and building labels must share one pixel grid")
    if dtm is not None and np.asarray(dtm).shape != labels.shape:
        raise ValueError("DTM and building labels must share one pixel grid")
    az_estimated = sun_azimuth_deg is None
    if az_estimated:
        if ndsm is None or np.asarray(ndsm).shape != labels.shape:
            diagnostics["skipped"] = "sun azimuth or nDSM is required"
            return [], diagnostics
        if dark.mean() < 0.005:
            diagnostics["skipped"] = "too few detected dark pixels to estimate sun azimuth"
            return [], diagnostics
        sun_azimuth_deg = dominant_shadow_azimuth(np.asarray(ndsm), dark)
    diagnostics["sun_azimuth_deg"] = float(sun_azimuth_deg)
    diagnostics["azimuth_estimated"] = az_estimated
    dr, dc, metre_step = _shadow_pixel_direction(image, float(sun_azimuth_deg), gsd_m)
    tan_el = math.tan(math.radians(float(sun_elevation_deg)))
    h, w = labels.shape
    accepted: list[dict[str, Any]] = []
    footprint_boxes = ndimage.find_objects(labels)

    def reject(reason: str) -> None:
        diagnostics["rejected"][reason] = diagnostics["rejected"].get(reason, 0) + 1

    for building in buildings:
        bid = int(building.get("id", 0))
        box = footprint_boxes[bid - 1] if 0 < bid <= len(footprint_boxes) else None
        if box is None:
            reject("small or missing footprint")
            continue
        mask = labels[box] == bid
        if mask.sum() < 15:
            reject("small or missing footprint")
            continue
        boundary = mask & ~ndimage.binary_erosion(mask)
        rr, cc = np.nonzero(boundary)
        rr, cc = rr + box[0].start, cc + box[1].start
        if len(rr) < 8:
            reject("insufficient edge pixels")
            continue
        # Keep the boundary facing away from the sun and whose next pixel is
        # outside this building. This avoids roof-darkness being called shadow.
        projection = rr * dr + cc * dc
        facing = projection >= np.percentile(projection, 62)
        rr, cc = rr[facing], cc[facing]
        nr, nc = np.rint(rr + dr * 2).astype(int), np.rint(cc + dc * 2).astype(int)
        inside = (nr >= 0) & (nr < h) & (nc >= 0) & (nc < w)
        rr, cc, nr, nc = rr[inside], cc[inside], nr[inside], nc[inside]
        outward = labels[nr, nc] != bid
        rr, cc = rr[outward], cc[outward]
        if len(rr) < 6:
            reject("insufficient anti-sun edge")
            continue
        # Distances are capped to bound work and avoid making a height claim
        # from a dark corridor extending to the edge of the image.
        max_step = int(min(140, max(18, 125.0 / max(tan_el * metre_step, 0.1))))
        distances = np.arange(2, max_step + 1)
        sample_r = np.rint(rr[:, None] + dr * distances[None, :]).astype(int)
        sample_c = np.rint(cc[:, None] + dc * distances[None, :]).astype(int)
        valid = (sample_r >= 0) & (sample_r < h) & (sample_c >= 0) & (sample_c < w)
        safe_r = np.clip(sample_r, 0, h - 1)
        safe_c = np.clip(sample_c, 0, w - 1)
        blocked = ((labels[safe_r, safe_c] > 0) | water[safe_r, safe_c]) & valid
        usable = valid & ~blocked
        coverage = usable.mean(axis=0)
        dark_frac = (dark[safe_r, safe_c] & usable).sum(axis=0) / np.maximum(usable.sum(axis=0), 1)
        # A detected shadow begins immediately outside the footprint. Allow
        # one antialiased transition pixel, then require ≥4 dark steps.
        start_choices = [i for i in range(min(3, len(distances)))
                         if dark_frac[i] >= 0.5 and coverage[i] >= 0.9]
        if not start_choices:
            reject("no attached dark shadow")
            continue
        start = start_choices[0]
        end = None
        for i in range(start + 4, len(distances) - 3):
            if np.all(dark_frac[i:i + 3] < 0.35):
                end = i - 1
                break
        if end is None:
            reject("no clear shadow termination")
            continue
        if np.any(coverage[start:end + 1] < 0.9) or blocked[:, start:end + 1].any():
            reject("shadow crosses building, water, or image edge")
            continue
        run_dark = float(np.mean(dark_frac[start:end + 1]))
        tail_dark = float(np.mean(dark_frac[end + 1:end + 4]))
        if run_dark < 0.62 or tail_dark > 0.27:
            reject("weak shadow-ground contrast")
            continue
        length_px = float(distances[end])
        height_m = length_px * metre_step * tan_el
        terrain_delta_m = 0.0
        if dtm is not None:
            ground_start = float(building.get("ground_elevation_m", np.nan))
            ground_tips = np.asarray(dtm)[safe_r[:, end], safe_c[:, end]]
            ground_tips = ground_tips[np.isfinite(ground_tips)]
            if not np.isfinite(ground_start) or ground_tips.size < max(3, len(rr) // 2):
                reject("missing ground elevations for slope correction")
                continue
            terrain_delta_m = float(np.median(ground_tips) - ground_start)
            if abs(terrain_delta_m) > length_px * metre_step * math.tan(math.radians(12)):
                reject("shadow crosses steep terrain")
                continue
            height_m += terrain_delta_m
        if not 2 <= height_m <= 120:
            reject("implausible geometric height")
            continue
        # Confidence is a ranking score, not a calibrated probability.
        contrast = max(0.0, min(1.0, run_dark - tail_dark))
        confidence = float(min(0.9, (0.25 + 0.5 * contrast + 0.15 * min(length_px / 12, 1))
                               * (0.72 if az_estimated else 1.0)))
        accepted.append({"building_id": bid, "height_m": round(height_m, 2),
                         "source": "shadow geometry", "evidence_level": "provisional",
                         "confidence": round(confidence, 3),
                         "shadow_length_m": round(length_px * metre_step, 2),
                         "terrain_delta_m": round(terrain_delta_m, 2),
                         "sun_elevation_deg": float(sun_elevation_deg),
                         "sun_azimuth_deg": float(sun_azimuth_deg),
                         "sun_azimuth_estimated": az_estimated,
                         "uncertainty_note": "dark-pixel edge measurement; roofs, occlusion and sun metadata may bias height"})
    accepted.sort(key=lambda a: a["confidence"], reverse=True)
    result = accepted[:max(0, int(max_anchors))]
    diagnostics["accepted"] = len(result)
    diagnostics["usable_before_cap"] = len(accepted)
    diagnostics["gsd_along_shadow_m"] = round(metre_step, 4)
    return result, diagnostics


def _image_lonlat_bbox(image: Any) -> tuple[float, float, float, float]:
    if not getattr(image, "georeferenced", False) or not getattr(image, "crs", None):
        raise ValueError("OSM height lookup requires a georeferenced image")
    h, w = image.shape
    native = [image.transform * (c, r) for c, r in ((0, 0), (w, 0), (w, h), (0, h))]
    lon, lat = transform_points(image.crs, "EPSG:4326", [p[0] for p in native],
                                [p[1] for p in native])
    return min(lat), min(lon), max(lat), max(lon)


def _fetch_overpass(bbox: tuple[float, float, float, float], *, cache_dir: Path,
                    timeout_s: float, cache_ttl_s: float, endpoint: str) -> tuple[dict, dict]:
    south, west, north, east = bbox
    key = hashlib.sha256(f"{endpoint}|{south:.6f}|{west:.6f}|{north:.6f}|{east:.6f}".encode()).hexdigest()[:20]
    cache_file = cache_dir / f"buildings-{key}.json"
    cached = None
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - cache_file.stat().st_mtime <= cache_ttl_s:
                return cached, {"cache": "fresh", "cache_file": str(cache_file)}
        except (OSError, ValueError):
            cached = None
    query = (f"[out:json][timeout:12];(way[\"building\"][\"height\"]"
             f"({south:.7f},{west:.7f},{north:.7f},{east:.7f});"
             f"way[\"building\"][\"building:levels\"]"
             f"({south:.7f},{west:.7f},{north:.7f},{east:.7f}););out tags geom;")
    request = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": query}).encode(),
                                     headers={"User-Agent": "DepthWizard-SIH/2.2 (small-area OSM building height lookup)",
                                              "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = response.read(5_000_001)
            if len(payload) > 5_000_000:
                raise ValueError("Overpass response exceeds 5 MB safety cap")
            data = json.loads(payload)
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            raise ValueError("Overpass returned an unexpected response")
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp = cache_file.with_suffix(".tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temp, cache_file)
        return data, {"cache": "network", "cache_file": str(cache_file)}
    except (OSError, ValueError, TimeoutError) as exc:
        if cached is not None:
            return cached, {"cache": "stale", "cache_file": str(cache_file),
                            "network_error": str(exc)}
        raise RuntimeError(f"OSM Overpass unavailable: {exc}") from exc


def osm_height_anchors(
    image: Any,
    labels: np.ndarray,
    *,
    cache_dir: str | Path | None = None,
    timeout_s: float = 15.0,
    cache_ttl_s: float = 7 * 86400,
    max_area_km2: float = 9.0,
    max_anchors: int = 30,
    endpoint: str = OVERPASS_URL,
    osm_data: dict | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match OSM building ``height``/``building:levels`` to detected footprints.

    ``osm_data`` permits offline caller-supplied Overpass JSON. Network fetches
    use a capped small-area query, a disk cache and a short timeout. Reusing a
    stale cached response is explicit in diagnostics. Public Overpass instances
    are suitable for occasional local analysis, not a production backend.
    """
    labels = np.asarray(labels)
    diagnostics: dict[str, Any] = {"source": "OpenStreetMap", "accepted": 0,
                                   "height_tags": 0, "levels_estimates": 0, "rejected": {},
                                   "attribution": "© OpenStreetMap contributors",
                                   "license": "ODbL", "endpoint": endpoint}
    if labels.shape != image.shape:
        raise ValueError("building labels and image must share one pixel grid")
    if not getattr(image, "georeferenced", False):
        diagnostics["skipped"] = "OSM requires a georeferenced image"
        return [], diagnostics
    bbox = _image_lonlat_bbox(image)
    south, west, north, east = bbox
    width_km = (east - west) * 111.32 * max(math.cos(math.radians((north + south) / 2)), 0.05)
    height_km = (north - south) * 111.13
    area_km2 = width_km * height_km
    diagnostics["bbox_south_west_north_east"] = [round(x, 7) for x in bbox]
    diagnostics["bbox_km2"] = round(area_km2, 3)
    if not (0 < area_km2 <= max_area_km2) or east - west > 1:
        diagnostics["skipped"] = f"footprint exceeds {max_area_km2:g} km² small-area OSM limit"
        return [], diagnostics
    if osm_data is None:
        try:
            osm_data, fetch_info = _fetch_overpass(bbox, cache_dir=Path(cache_dir or DEFAULT_CACHE),
                                                    timeout_s=timeout_s, cache_ttl_s=cache_ttl_s,
                                                    endpoint=endpoint)
            diagnostics.update(fetch_info)
        except RuntimeError as exc:
            diagnostics["skipped"] = str(exc)
            return [], diagnostics
    else:
        diagnostics["cache"] = "caller-supplied data"
    elements = osm_data.get("elements", []) if isinstance(osm_data, dict) else []
    if not isinstance(elements, list):
        diagnostics["skipped"] = "invalid OSM data"
        return [], diagnostics
    geometries: list[tuple[dict, int]] = []
    records: list[dict[str, Any]] = []
    for element in elements[:10_000]:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        if not isinstance(tags, dict) or not tags.get("building"):
            continue
        value = parse_osm_height_m(tags.get("height"))
        mode = "height"
        if value is None:
            raw_levels = str(tags.get("building:levels", "")).strip()
            if not re.fullmatch(r"\d{1,2}", raw_levels):
                continue
            levels = int(raw_levels)
            if not 1 <= levels <= 80:
                continue
            value, mode = 3.0 * levels, "building:levels"
        vertices = element.get("geometry") or []
        if not isinstance(vertices, list) or not 4 <= len(vertices) <= 4000:
            continue
        try:
            lon = [float(v["lon"]) for v in vertices]
            lat = [float(v["lat"]) for v in vertices]
            if not all(math.isfinite(x) for x in lon + lat):
                continue
            xx, yy = transform_points("EPSG:4326", image.crs, lon, lat)
            ring = list(zip(xx, yy))
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) < 4:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        index = len(records) + 1
        geometries.append(({"type": "Polygon", "coordinates": [ring]}, index))
        records.append({"osm_way_id": element.get("id"), "height_m": value,
                        "tag": mode, "tag_value": tags.get(mode),
                        "tag_source": tags.get("source:height"),
                        "height_accuracy": tags.get("height:accuracy")})
    diagnostics["tagged_ways"] = len(records)
    if not geometries:
        return [], diagnostics
    osm_grid = rasterize(geometries, out_shape=labels.shape, transform=image.transform,
                         fill=0, dtype="int32")
    osm_count = np.bincount(osm_grid.ravel(), minlength=len(records) + 1)
    building_count = np.bincount(labels.astype(np.int32).ravel())
    both = (osm_grid > 0) & (labels > 0)
    if not both.any():
        diagnostics["rejected"]["no matching detected footprint"] = len(records)
        return [], diagnostics
    pair_codes = osm_grid[both].astype(np.int64) * (len(building_count) + 1) + labels[both]
    pair_ids, pair_counts = np.unique(pair_codes, return_counts=True)
    matches: dict[int, tuple[int, int]] = {}
    for pair, count in zip(pair_ids, pair_counts):
        osm_id, building_id = divmod(int(pair), len(building_count) + 1)
        if osm_id not in matches or count > matches[osm_id][1]:
            matches[osm_id] = (building_id, int(count))
    selected: dict[int, dict[str, Any]] = {}
    for osm_id, record in enumerate(records, 1):
        match = matches.get(osm_id)
        if match is None:
            diagnostics["rejected"]["no matching detected footprint"] = diagnostics["rejected"].get("no matching detected footprint", 0) + 1
            continue
        bid, overlap = match
        union = int(osm_count[osm_id] + building_count[bid] - overlap)
        iou = overlap / max(union, 1)
        osm_coverage = overlap / max(int(osm_count[osm_id]), 1)
        if iou < 0.25 or osm_coverage < 0.45 or overlap < 8:
            diagnostics["rejected"]["weak footprint match"] = diagnostics["rejected"].get("weak footprint match", 0) + 1
            continue
        explicit = record["tag"] == "height"
        confidence = min(0.95, iou * (0.95 if explicit else 0.65))
        anchor = {"building_id": bid, "height_m": round(float(record["height_m"]), 2),
                  "source": "OpenStreetMap height" if explicit else "OpenStreetMap levels estimate",
                  "evidence_level": "mapped height" if explicit else "approximate levels",
                  "confidence": round(confidence, 3), "footprint_iou": round(iou, 3),
                  "osm_way_id": record["osm_way_id"], "osm_url": f"https://www.openstreetmap.org/way/{record['osm_way_id']}",
                  "osm_tag": record["tag"], "osm_tag_value": record["tag_value"],
                  "osm_source_height": record["tag_source"], "osm_height_accuracy": record["height_accuracy"],
                  "uncertainty_note": ("mapped height; not survey verified" if explicit else
                                       "3 m per level heuristic; roof height and local floor heights unknown")}
        priority = confidence + (0.5 if explicit else 0)
        if bid not in selected or priority > selected[bid]["_priority"]:
            anchor["_priority"] = priority
            selected[bid] = anchor
    anchors = sorted(selected.values(), key=lambda a: a["_priority"], reverse=True)[:max(0, int(max_anchors))]
    for a in anchors:
        a.pop("_priority", None)
    diagnostics["accepted"] = len(anchors)
    diagnostics["height_tags"] = sum(a["osm_tag"] == "height" for a in anchors)
    diagnostics["levels_estimates"] = len(anchors) - diagnostics["height_tags"]
    return anchors, diagnostics


def automatic_height_anchors(
    image: Any,
    labels: np.ndarray,
    buildings: list[dict[str, Any]],
    *,
    gsd_m: float,
    ndsm: np.ndarray | None = None,
    dtm: np.ndarray | None = None,
    water: np.ndarray | None = None,
    sun_elevation_deg: float | None = None,
    sun_azimuth_deg: float | None = None,
    use_osm: bool = True,
    cache_dir: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Gather automatic anchors, preferring explicit OSM height to a shadow.

    Returns zero anchors cleanly when metadata or OSM coverage is unavailable.
    The caller should retain ``diagnostics`` in scene metadata and should not
    present an anchor-derived DSM as independently validated.
    """
    sun_meta = sun_angles_from_metadata(image)
    elevation = sun_elevation_deg if sun_elevation_deg is not None else sun_meta["elevation_deg"]
    azimuth = sun_azimuth_deg if sun_azimuth_deg is not None else sun_meta["azimuth_deg"]
    shadows, shadow_diag = shadow_height_anchors(image, labels, buildings, gsd_m=gsd_m,
                                                   sun_elevation_deg=elevation, sun_azimuth_deg=azimuth,
                                                   ndsm=ndsm, dtm=dtm, water=water, max_anchors=10)
    osm, osm_diag = (osm_height_anchors(image, labels, cache_dir=cache_dir) if use_osm else
                     ([], {"skipped": "OSM disabled", "accepted": 0}))
    combined = {a["building_id"]: a for a in shadows}
    for anchor in osm:
        current = combined.get(anchor["building_id"])
        if current is None or anchor["osm_tag"] == "height":
            combined[anchor["building_id"]] = anchor
    diagnostics = {"sun_metadata": sun_meta, "shadow": shadow_diag, "osm": osm_diag,
                   "selected_count": len(combined),
                   "warning": "automatic anchors are external/physics estimates, not independent validation"}
    return list(combined.values()), diagnostics
