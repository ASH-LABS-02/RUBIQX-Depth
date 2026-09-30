"""Exploratory disaster-planning analyses for metric DepthWizard scenes.

These functions operate on the *unexaggerated* metre-valued DTM/DSM and
return ordinary JSON values.  They are screening tools: the input elevations,
building extraction, connectivity, occupancy and radio assumptions can all be
wrong.  In particular, a route is not a verified evacuation route, a building
is not a certified shelter, and line of sight is not radio service.

Raster coordinates are ``(row, column)``.  Returned pixel coordinates are
``[column, row]`` so they can be drawn directly on the optical image.
"""
from __future__ import annotations

import heapq
import math
from typing import Any, Iterable

import numpy as np
from scipy import ndimage


def _grid(values: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    if arr.ndim != 2 or min(arr.shape) < 2:
        raise ValueError(f"{name} must be a 2-D raster at least 2 by 2 pixels")
    return arr


def _gsd(gsd_m: float) -> float:
    gsd = float(gsd_m)
    if not math.isfinite(gsd) or gsd <= 0:
        raise ValueError("gsd_m must be a positive finite metre value")
    return gsd


def _cell(cell_rc: Iterable[int], shape: tuple[int, int]) -> tuple[int, int]:
    row, col = (int(x) for x in cell_rc)
    if not (0 <= row < shape[0] and 0 <= col < shape[1]):
        raise ValueError("cell_rc lies outside the raster")
    return row, col


def _uv_path(path_rc: list[tuple[int, int]], shape: tuple[int, int]) -> list[list[float]]:
    h, w = shape
    return [[round(c / (w - 1), 6), round(r / (h - 1), 6)] for r, c in path_rc]


def _pixel_path(path_rc: list[tuple[int, int]]) -> list[list[int]]:
    return [[int(c), int(r)] for r, c in path_rc]


def connected_flood_mask(
    dtm_m: np.ndarray,
    water_level_m: float,
    *,
    water_seed_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Below-water terrain connected to an edge or an explicit water seed.

    Four-neighbour connectivity prevents water passing through diagonal
    corners.  Without a water seed, only cells connected to the raster edge
    inundate.  This is a bathtub model; it does not model levees, drainage,
    rainfall duration or flow dynamics.
    """
    z = _grid(dtm_m, "dtm_m")
    level = float(water_level_m)
    if not math.isfinite(level):
        raise ValueError("water_level_m must be finite")
    low = np.isfinite(z) & (z <= level)
    seeds = np.zeros(z.shape, dtype=bool)
    if water_seed_mask is None:
        seeds[0, :] = low[0, :]
        seeds[-1, :] = low[-1, :]
        seeds[:, 0] = low[:, 0]
        seeds[:, -1] = low[:, -1]
    else:
        supplied = np.asarray(water_seed_mask, dtype=bool)
        if supplied.shape != z.shape:
            raise ValueError("water_seed_mask shape must match dtm_m")
        seeds = low & supplied
    if not np.any(seeds):
        return np.zeros(z.shape, dtype=bool)
    labels, _ = ndimage.label(low)
    ids = np.unique(labels[seeds])
    return np.isin(labels, ids[ids > 0])


def evacuation_route(
    dtm_m: np.ndarray,
    start_rc: tuple[int, int],
    water_level_m: float,
    gsd_m: float,
    *,
    flood_mask: np.ndarray | None = None,
    building_labels: np.ndarray | None = None,
    clearance_m: float = 2.0,
    max_slope_deg: float = 30.0,
    min_destination_area_m2: float = 25.0,
    max_expansions: int = 300_000,
) -> dict[str, Any]:
    """Find a least-cost dry footpath to connected terrain above the water.

    A* uses distance and an uphill/slope penalty.  Building footprints, steep
    cells and flooded cells are impassable.  No road/bridge, property-access,
    traffic, debris, culvert or real-time hazard data is considered.
    """
    z = _grid(dtm_m, "dtm_m")
    gsd = _gsd(gsd_m)
    start = _cell(start_rc, z.shape)
    level = float(water_level_m)
    if not math.isfinite(level):
        raise ValueError("water_level_m must be finite")
    if max_slope_deg <= 0 or min_destination_area_m2 < 0 or max_expansions < 1:
        raise ValueError("route slope, destination area or expansion limit is invalid")
    if flood_mask is None:
        flooded = connected_flood_mask(z, level)
        flood_basis = "edge-connected bathtub inundation"
    else:
        flooded = np.asarray(flood_mask, dtype=bool)
        if flooded.shape != z.shape:
            raise ValueError("flood_mask shape must match dtm_m")
        flood_basis = "supplied flood mask"
    if flooded[start]:
        return {"status": "blocked_start", "route_px": [], "route_uv": [],
                "message": "Selected start cell is flooded; no dry ground route can begin there.",
                "water_level_m": level, "flood_basis": flood_basis,
                "screening_only": True}

    # Smooth solely for a walkability screen; path elevations remain raw DTM.
    smooth = ndimage.gaussian_filter(np.where(np.isfinite(z), z,
                    float(np.nanmedian(z[np.isfinite(z)])) if np.isfinite(z).any() else 0), 1.0)
    gy, gx = np.gradient(smooth, gsd)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    passable = np.isfinite(z) & ~flooded & (slope <= float(max_slope_deg))
    if building_labels is not None:
        labels = np.asarray(building_labels)
        if labels.shape != z.shape:
            raise ValueError("building_labels shape must match dtm_m")
        passable &= labels == 0
    if not passable[start]:
        return {"status": "blocked_start", "route_px": [], "route_uv": [],
                "message": "Selected start cell is occupied or exceeds the slope limit.",
                "water_level_m": level, "flood_basis": flood_basis,
                "screening_only": True}

    high = passable & (z >= level + max(0.0, float(clearance_m)))
    components, n_components = ndimage.label(high)
    if n_components:
        sizes = np.bincount(components.ravel())
        required = max(1, int(math.ceil(min_destination_area_m2 / (gsd * gsd))))
        good_ids = np.flatnonzero(sizes >= required)
        good_ids = good_ids[good_ids > 0]
        high &= np.isin(components, good_ids)
    if not np.any(high):
        return {"status": "no_high_ground", "route_px": [], "route_uv": [],
                "message": "No walkable high-ground patch meets the clearance and area settings.",
                "water_level_m": level, "clearance_m": float(clearance_m),
                "flood_basis": flood_basis, "screening_only": True}

    if high[start]:
        path = [start]
        return {"status": "already_on_high_ground", "route_px": _pixel_path(path),
                "route_uv": _uv_path(path, z.shape), "length_m": 0.0, "ascent_m": 0.0,
                "destination_elevation_m": float(z[start]),
                "water_level_m": level, "clearance_m": float(clearance_m),
                "flood_basis": flood_basis, "screening_only": True}

    # Exact Euclidean distance to the nearest target gives an admissible
    # heuristic because each traversed metre costs at least one.
    heuristic = ndimage.distance_transform_edt(~high) * gsd
    h, w = z.shape
    dist = np.full(z.size, np.inf, np.float64)
    prev = np.full(z.size, -1, np.int32)
    closed = np.zeros(z.size, dtype=bool)
    start_i = start[0] * w + start[1]
    dist[start_i] = 0.0
    queue = [(float(heuristic[start]), 0.0, start_i)]
    neighbours = [(dr, dc, math.hypot(dr, dc) * gsd)
                  for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]
    found = -1
    expanded = 0
    while queue and expanded < max_expansions:
        _, old_cost, index = heapq.heappop(queue)
        if closed[index] or old_cost > dist[index] + 1e-9:
            continue
        closed[index] = True
        expanded += 1
        row, col = divmod(index, w)
        if high[row, col]:
            found = index
            break
        for dr, dc, step in neighbours:
            rr, cc = row + dr, col + dc
            if rr < 0 or rr >= h or cc < 0 or cc >= w or not passable[rr, cc]:
                continue
            if dr and dc and (not passable[row + dr, col] or not passable[row, col + dc]):
                continue  # no diagonal squeeze between barriers
            nxt = rr * w + cc
            if closed[nxt]:
                continue
            rise = max(0.0, float(z[rr, cc] - z[row, col]))
            # Prefer gentler climbs while still reaching the nearest safe patch.
            step_cost = step * (1.0 + 0.7 * (slope[rr, cc] / max_slope_deg) ** 2) + 0.25 * rise
            candidate = old_cost + step_cost
            if candidate < dist[nxt]:
                dist[nxt] = candidate
                prev[nxt] = index
                heapq.heappush(queue, (candidate + float(heuristic[rr, cc]), candidate, nxt))
    if found < 0:
        return {"status": "no_dry_route", "route_px": [], "route_uv": [],
                "message": "No connected dry path reached a qualifying high-ground patch.",
                "expanded_cells": expanded, "water_level_m": level,
                "flood_basis": flood_basis, "screening_only": True}

    indices = []
    i = found
    while i >= 0:
        indices.append(i)
        i = int(prev[i])
    path = [divmod(i, w) for i in reversed(indices)]
    length = sum(math.hypot(r1 - r0, c1 - c0) * gsd
                 for (r0, c0), (r1, c1) in zip(path, path[1:]))
    ascent = sum(max(0.0, float(z[r1, c1] - z[r0, c0]))
                 for (r0, c0), (r1, c1) in zip(path, path[1:]))
    return {"status": "route_found", "route_px": _pixel_path(path),
            "route_uv": _uv_path(path, z.shape), "length_m": round(length, 2),
            "ascent_m": round(ascent, 2),
            "destination_elevation_m": round(float(z[path[-1]]), 2),
            "expanded_cells": expanded, "water_level_m": level,
            "clearance_m": float(clearance_m), "flood_basis": flood_basis,
            "assumptions": {"max_slope_deg": float(max_slope_deg),
                            "min_destination_area_m2": float(min_destination_area_m2),
                            "buildings_impassable": building_labels is not None},
            "warning": "Exploratory terrain route only; verify roads, bridges, access and live conditions.",
            "screening_only": True}


def _buildings(building_data: dict | list[dict]) -> list[dict]:
    if isinstance(building_data, dict):
        return list(building_data.get("buildings", []))
    return list(building_data)


def population_exposure(
    building_data: dict | list[dict],
    water_level_m: float,
    *,
    flood_mask: np.ndarray | None = None,
    building_labels: np.ndarray | None = None,
    min_flooded_footprint_fraction: float = 0.05,
    floor_area_per_person_m2: float = 30.0,
    occupancy_fraction: float = 1.0,
    units: str = "metre",
) -> dict[str, Any]:
    """Very coarse flooded-footprint occupancy proxy, never a census count."""
    if units != "metre":
        return {"status": "unavailable", "reason": "Metric ground elevations are required."}
    level = float(water_level_m)
    density = float(floor_area_per_person_m2)
    occupancy = float(occupancy_fraction)
    if not math.isfinite(level) or not math.isfinite(density) or density <= 0 or not 0 <= occupancy <= 1:
        raise ValueError("invalid water level or occupancy assumptions")
    if not 0 <= min_flooded_footprint_fraction <= 1:
        raise ValueError("min_flooded_footprint_fraction must be in [0, 1]")
    if (flood_mask is None) != (building_labels is None):
        raise ValueError("flood_mask and building_labels must be supplied together")
    footprint_counts = wet_counts = None
    if flood_mask is not None:
        flooded = np.asarray(flood_mask, dtype=bool)
        labels = np.asarray(building_labels, dtype=np.int32)
        if labels.shape != flooded.shape or labels.ndim != 2 or np.any(labels < 0):
            raise ValueError("flood_mask and nonnegative building_labels must share a 2-D shape")
        footprint_counts = np.bincount(labels.ravel())
        wet_counts = np.bincount(labels[flooded], minlength=footprint_counts.size)
    affected = []
    checked = 0
    skipped_unmapped = 0
    for b in _buildings(building_data):
        try:
            ground, area, floors = float(b["ground_elevation_m"]), float(b["area_m2"]), int(b["storeys"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(map(math.isfinite, (ground, area))) or area <= 0 or floors <= 0:
            continue
        checked += 1
        flood_fraction = None
        if footprint_counts is not None:
            ident = b.get("id")
            if not isinstance(ident, (int, np.integer)) or ident < 1 or ident >= footprint_counts.size or footprint_counts[ident] == 0:
                skipped_unmapped += 1
                continue
            flood_fraction = float(wet_counts[ident] / footprint_counts[ident])
            if flood_fraction < min_flooded_footprint_fraction:
                continue
        elif ground >= level:
            continue
        gross_floor_area = area * floors
        people = gross_floor_area / density * occupancy
        item = {"building_id": b.get("id"), "ground_elevation_m": round(ground, 2),
                         "footprint_m2": round(area, 1), "storeys": floors,
                         "gross_floor_area_m2": round(gross_floor_area, 1),
                         "estimated_people": round(people, 1)}
        if flood_fraction is not None:
            item["flooded_footprint_fraction"] = round(flood_fraction, 3)
        affected.append(item)
    return {"status": "estimated", "water_level_m": level,
            "affected_buildings": len(affected), "buildings_checked": checked,
            "estimated_people_at_risk": round(sum(b["estimated_people"] for b in affected)),
            "affected": affected,
            "skipped_unmapped_buildings": skipped_unmapped,
            "flood_basis": ("supplied inundation mask on building footprints" if footprint_counts is not None
                            else "building ground elevation below water level"),
            "assumptions": {"floor_area_per_person_m2": density,
                            "occupancy_fraction": occupancy,
                            "min_flooded_footprint_fraction": (float(min_flooded_footprint_fraction)
                                                                 if footprint_counts is not None else None)},
            "warning": "Floor area is a proxy; use census, land use and occupancy data for population exposure.",
            "screening_only": True}


def vertical_shelters(
    building_data: dict | list[dict],
    water_level_m: float,
    *,
    min_confidence: float = 0.65,
    min_roof_freeboard_m: float = 2.0,
    min_storeys: int = 2,
    floor_height_m: float = 3.0,
    max_results: int = 30,
    units: str = "metre",
) -> dict[str, Any]:
    """Rank tall, dry-roof buildings as *candidates* for field inspection."""
    if units != "metre":
        return {"status": "unavailable", "reason": "Metric building heights are required."}
    level = float(water_level_m)
    if not math.isfinite(level) or floor_height_m <= 0:
        raise ValueError("invalid water level or floor height")
    candidates = []
    for b in _buildings(building_data):
        try:
            ground = float(b["ground_elevation_m"])
            height = float(b["height_m"])
            area = float(b["area_m2"])
            confidence = float(b["confidence"])
            storeys = int(b["storeys"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(map(math.isfinite, (ground, height, area, confidence))):
            continue
        if height <= 0 or area <= 0 or storeys < min_storeys or confidence < min_confidence:
            continue
        roof = ground + height  # raw model height, not display-only storey snapping
        freeboard = roof - level
        if freeboard < min_roof_freeboard_m:
            continue
        dry_floors = min(storeys, max(0, int(math.floor((roof - max(level, ground)) / floor_height_m))))
        if dry_floors < 1:
            continue
        dry_area = area * dry_floors
        candidates.append({"building_id": b.get("id"), "polygon_uv": b.get("polygon_uv"),
                           "height_m": round(height, 2), "roof_freeboard_m": round(freeboard, 2),
                           "confidence": round(confidence, 3), "confidence_basis": b.get("confidence_basis"),
                           "floors_above_water_est": dry_floors,
                           "floor_area_above_water_m2_est": round(dry_area, 1),
                           "ground_affected": ground < level})
    candidates.sort(key=lambda b: (b["confidence"] * b["floor_area_above_water_m2_est"],
                                   b["roof_freeboard_m"]), reverse=True)
    return {"status": "candidates" if candidates else "none_found", "water_level_m": level,
            "candidate_count": len(candidates), "candidates": candidates[:max_results],
            "criteria": {"min_confidence": float(min_confidence),
                         "min_roof_freeboard_m": float(min_roof_freeboard_m),
                         "min_storeys": int(min_storeys), "floor_height_m": float(floor_height_m)},
            "warning": "These are model-based inspection candidates, not certified shelters; structural safety, access and services are unknown.",
            "screening_only": True}


def landslide_runout(
    dtm_m: np.ndarray,
    susceptibility: np.ndarray,
    gsd_m: float,
    *,
    building_labels: np.ndarray | None = None,
    threshold: float = 0.7,
    max_sources: int = 24,
    min_source_spacing_m: float = 25.0,
    max_path_m: float = 500.0,
    building_buffer_m: float = 5.0,
) -> dict[str, Any]:
    """Trace steepest-descent D8 paths from high susceptibility cells.

    This is a drainage/downslope exposure screen.  It is not a debris-flow
    model: material volume, friction, runout energy and storm forcing are not
    represented.
    """
    z = _grid(dtm_m, "dtm_m")
    susc = _grid(susceptibility, "susceptibility")
    if susc.shape != z.shape:
        raise ValueError("susceptibility shape must match dtm_m")
    gsd = _gsd(gsd_m)
    if max_sources < 1 or min_source_spacing_m < 0 or max_path_m <= 0 or building_buffer_m < 0:
        raise ValueError("invalid runout sampling or distance settings")
    labels = None
    near_id = None
    near_distance = None
    if building_labels is not None:
        labels = np.asarray(building_labels, dtype=np.int32)
        if labels.shape != z.shape:
            raise ValueError("building_labels shape must match dtm_m")
        near_distance, near_indices = ndimage.distance_transform_edt(labels == 0, return_indices=True)
        near_id = labels[tuple(near_indices)]
    valid = np.isfinite(z) & np.isfinite(susc) & (susc >= threshold)
    source_candidates = np.flatnonzero(valid.ravel())
    order = source_candidates[np.argsort(susc.ravel()[source_candidates])[::-1]]
    seeds: list[tuple[int, int]] = []
    spacing_sq = (min_source_spacing_m / gsd) ** 2
    for flat in order:
        rc = divmod(int(flat), z.shape[1])
        if all((rc[0] - r) ** 2 + (rc[1] - c) ** 2 >= spacing_sq for r, c in seeds):
            seeds.append(rc)
        if len(seeds) >= max_sources:
            break
    smooth = ndimage.gaussian_filter(np.where(np.isfinite(z), z,
                    float(np.nanmedian(z[np.isfinite(z)])) if np.isfinite(z).any() else 0),
                    max(0.8, 2.0 / gsd))
    h, w = z.shape
    offsets = [(dr, dc, math.hypot(dr, dc)) for dr in (-1, 0, 1)
               for dc in (-1, 0, 1) if dr or dc]
    paths = []
    all_buildings: set[int] = set()
    max_steps = max(1, int(max_path_m / gsd))
    for seed in seeds:
        trace = [seed]
        seen = {seed}
        route_ids: set[int] = set()
        distance_m = 0.0
        row, col = seed
        for _ in range(max_steps):
            best = None
            best_gradient = 0.0
            for dr, dc, step_px in offsets:
                rr, cc = row + dr, col + dc
                if 0 <= rr < h and 0 <= cc < w and np.isfinite(z[rr, cc]) and (rr, cc) not in seen:
                    gradient = float(smooth[row, col] - smooth[rr, cc]) / (step_px * gsd)
                    if gradient > best_gradient:
                        best_gradient = gradient
                        best = (rr, cc, step_px * gsd)
            if best is None or best_gradient < 0.005:
                break
            row, col, segment_m = best
            distance_m += segment_m
            trace.append((row, col))
            seen.add((row, col))
            if distance_m >= max_path_m:
                break
        if len(trace) < 2:
            continue
        if labels is not None:
            rows, cols = np.array(trace).T
            close = near_distance[rows, cols] * gsd <= building_buffer_m
            route_ids = {int(i) for i in near_id[rows[close], cols[close]] if i > 0}
        all_buildings.update(route_ids)
        paths.append({"source_px": [seed[1], seed[0]],
                      "source_susceptibility": round(float(susc[seed]), 3),
                      "path_px": _pixel_path(trace), "path_uv": _uv_path(trace, z.shape),
                      "length_m": round(distance_m, 1),
                      "elevation_drop_m": round(max(0.0, float(z[seed] - z[trace[-1]])), 2),
                      "intersected_building_ids": sorted(route_ids)})
    return {"status": "screened" if paths else "no_high_sources", "source_cells_above_threshold": int(valid.sum()),
            "sources_traced": len(paths), "paths": paths,
            "screened_building_ids": sorted(all_buildings),
            "buildings_in_path_count": len(all_buildings),
            "assumptions": {"susceptibility_threshold": float(threshold),
                            "max_path_m": float(max_path_m),
                            "building_buffer_m": float(building_buffer_m)},
            "warning": "D8 downhill traces are screening paths, not a physical landslide-runout prediction.",
            "screening_only": True}


def _row_rle(mask: np.ndarray) -> list[list[int]]:
    """Compact true pixels as [row, start_column, end_column_exclusive]."""
    segments = []
    for row, line in enumerate(mask):
        edges = np.flatnonzero(np.diff(np.r_[False, line, False].astype(np.int8)))
        segments.extend([[int(row), int(a), int(b)] for a, b in zip(edges[::2], edges[1::2])])
    return segments


def relay_visibility(
    dsm_m: np.ndarray,
    origin_rc: tuple[int, int],
    gsd_m: float,
    *,
    observer_agl_m: float = 20.0,
    target_agl_m: float = 1.5,
    max_range_m: float = 3000.0,
    building_labels: np.ndarray | None = None,
) -> dict[str, Any]:
    """Approximate terrain/building line-of-sight from a tower or drone.

    Radial horizon bins encode obstacles at progressively greater range.  It
    deliberately stops at optical visibility; radio frequency, antenna power,
    Fresnel clearance, foliage and Earth curvature are outside the model.
    """
    z = _grid(dsm_m, "dsm_m")
    gsd = _gsd(gsd_m)
    origin = _cell(origin_rc, z.shape)
    if not np.isfinite(z[origin]):
        raise ValueError("origin must have a finite DSM height")
    if observer_agl_m < 0 or target_agl_m < 0 or max_range_m <= 0:
        raise ValueError("heights must be nonnegative and max_range_m positive")
    h, w = z.shape
    rr, cc = np.indices(z.shape)
    dy = rr - origin[0]
    dx = cc - origin[1]
    radius_px = np.hypot(dx, dy)
    radius_m = radius_px * gsd
    in_range = np.isfinite(z) & (radius_m <= max_range_m)
    max_px = min(float(max_range_m / gsd), math.hypot(h, w))
    bin_count = max(720, min(8192, int(math.ceil(2 * math.pi * max_px * 1.5))))
    bin_width = 2 * math.pi / bin_count
    angles = np.mod(np.arctan2(dy, dx), 2 * math.pi)
    bins = np.mod(np.rint(angles / bin_width).astype(np.int32), bin_count)
    observer_z = float(z[origin]) + float(observer_agl_m)
    safe_dist = np.maximum(radius_m, gsd * 0.5)
    target_slope = (z + float(target_agl_m) - observer_z) / safe_dist
    obstacle_slope = (z - observer_z) / safe_dist
    visible = np.zeros(z.shape, dtype=bool)
    visible[origin] = True
    horizon = np.full(bin_count, -np.inf, np.float32)
    ring = np.ceil(radius_px).astype(np.int32)
    for k in range(1, int(np.ceil(max_px)) + 1):
        cells = in_range & (ring == k)
        if not np.any(cells):
            continue
        idx = bins[cells]
        visible[cells] = target_slope[cells] >= horizon[idx] - 1e-6
        # Each source pixel blocks the angular span of its finite footprint.
        # Near obstacles therefore cast wider shadows than distant ones.
        rad = np.maximum(radius_px[cells], 0.5)
        spread = np.minimum(bin_count // 4, np.ceil(0.5 / rad / bin_width).astype(np.int32))
        obstacles = obstacle_slope[cells]
        max_spread = int(spread.max(initial=0))
        for offset in range(max_spread + 1):
            active = spread >= offset
            if not np.any(active):
                continue
            np.maximum.at(horizon, (idx[active] + offset) % bin_count, obstacles[active])
            if offset:
                np.maximum.at(horizon, (idx[active] - offset) % bin_count, obstacles[active])
    eligible = int(in_range.sum())
    building_coverage = []
    if building_labels is not None:
        labels = np.asarray(building_labels, dtype=np.int32)
        if labels.shape != z.shape:
            raise ValueError("building_labels shape must match dsm_m")
        ids = np.unique(labels[(labels > 0) & in_range])
        for ident in ids:
            footprint = (labels == ident) & in_range
            fraction = float(visible[footprint].mean())
            building_coverage.append({"building_id": int(ident),
                                      "visible_footprint_fraction": round(fraction, 3)})
    return {"status": "screened", "origin_px": [origin[1], origin[0]],
            "observer_elevation_m": round(observer_z, 2),
            "observer_agl_m": float(observer_agl_m), "target_agl_m": float(target_agl_m),
            "max_range_m": float(max_range_m), "grid_shape": [h, w],
            "visible_rle": _row_rle(visible),
            "visible_cells": int(visible.sum()), "eligible_cells": eligible,
            "visible_fraction": round(float(visible.sum()) / max(eligible, 1), 4),
            "visible_area_m2": round(float(visible.sum()) * gsd * gsd, 1),
            "building_coverage": building_coverage,
            "method": "radial horizon line-of-sight screening",
            "warning": "Line of sight is not radio coverage; antenna, frequency, Fresnel clearance and obstructions require field verification.",
            "screening_only": True}
