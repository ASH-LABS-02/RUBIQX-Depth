"""Building footprint extraction and LoD1 3D city model generation.

Detects building footprints from the normalized Digital Surface Model (nDSM),
computes robust planar roof heights, floor counts, footprint areas, and
simplifies vector polygons (Ramer-Douglas-Peucker) for clean LoD1 extruded walls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import shapes
from scipy import ndimage


def rdp(points: list[tuple[float, float]], epsilon: float = 1.2) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker polygon simplification for clean straight LoD1 walls."""
    if len(points) < 3:
        return points
    p1, p2 = np.array(points[0]), np.array(points[-1])
    line_vec = p2 - p1
    line_len = np.linalg.norm(line_vec)
    if line_len < 1e-6:
        dists = np.linalg.norm(np.array(points) - p1, axis=1)
    else:
        line_unit = line_vec / line_len
        vecs = np.array(points) - p1
        proj = np.sum(vecs * line_unit, axis=1)
        dists = np.linalg.norm(vecs - np.outer(proj, line_unit), axis=1)
    idx = int(np.argmax(dists))
    max_d = dists[idx]
    if max_d > epsilon:
        left = rdp(points[:idx + 1], epsilon)
        right = rdp(points[idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


def extract_buildings(
    dsm: np.ndarray,
    dtm: np.ndarray | None = None,
    gsd: float = 1.0,
    min_height_m: float = 2.5,
    min_area_m2: float = 30.0,
    max_buildings: int = 1500,
    world_w: float = 1.0,
    world_h: float = 1.0,
    rgb: np.ndarray | None = None,
    uncertainty_m: np.ndarray | None = None,
    return_labels: bool = False,
) -> dict[str, Any]:
    """Extract LoD1 building footprints, heights, storeys and polygon coordinates."""
    h, w = dsm.shape
    if dtm is None:
        # Approximate bare-earth terrain via morphological opening
        filter_size = max(3, int(round(60.0 / max(gsd, 0.1))))
        ground = ndimage.grey_opening(dsm, size=(filter_size, filter_size))
        dtm = ndimage.gaussian_filter(ground, max(1.0, 15.0 / max(gsd, 0.1)))

    ndsm = np.maximum(dsm - dtm, 0.0)

    # Threshold for candidate structures; exclude sunlit vegetation (excess-green),
    # which otherwise turns tree canopy into "buildings".
    mask = ndsm >= min_height_m
    if rgb is not None and rgb.shape[:2] == ndsm.shape:
        f = rgb.astype(np.float32)
        exg = (2 * f[..., 1] - f[..., 0] - f[..., 2]) / (f.sum(-1) + 1e-6)
        mask &= ~(exg > 0.06)
    # Morphological cleaning to separate close buildings and remove tree-leaf speckle
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3), bool))
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 3), bool))

    labeled, num_features = ndimage.label(mask)
    if num_features == 0:
        out = {"count": 0, "total_footprint_m2": 0.0, "buildings": []}
        if return_labels:
            out["_labels"] = np.zeros(ndsm.shape, np.int32)
        return out
    kept = np.zeros(ndsm.shape, np.int32)

    pixel_area_m2 = gsd * gsd
    min_pixels = int(round(min_area_m2 / max(pixel_area_m2, 0.01)))

    # Compute component sizes
    counts = np.bincount(labeled.ravel())

    buildings = []
    # Extract polygon contours via rasterio.features.shapes
    for geom, val in shapes(labeled.astype(np.int32), mask=mask):
        val = int(val)
        if val <= 0 or counts[val] < min_pixels:
            continue
        coords = geom.get("coordinates", [])
        if not coords:
            continue
        exterior = coords[0]  # exterior ring: list of (col, row)
        if len(exterior) < 4:
            continue

        # Simplify polygon for crisp, non-jaggy vertical LoD1 walls
        simplified = rdp(exterior, epsilon=1.2)
        if len(simplified) < 4:
            simplified = exterior

        comp_mask = labeled == val
        if not np.any(comp_mask):
            continue

        dsm_vals = dsm[comp_mask]
        dtm_vals = dtm[comp_mask]
        ndsm_vals = ndsm[comp_mask]

        # robust LoD1 roof: 70th percentile of height above ground inside the
        # footprint (insensitive to parapets, mixed edge pixels and sloping terrain)
        ground_h = float(np.median(dtm_vals))
        height_agl = max(min_height_m, float(np.percentile(ndsm_vals, 70)))
        
        roof_h = ground_h + height_agl
        area_m2 = float(round(comp_mask.sum() * pixel_area_m2, 1))
        volume_m3 = float(round(area_m2 * height_agl, 1))
        
        # Storey snapping applied only to display variables
        storeys = max(1, int(round(height_agl / 3.0)))
        roof_elevation_m = ground_h + float(storeys * 3.0)

        # Center in world coordinates (Three.js coordinates: X along width, Z along height)
        cols = [p[0] for p in exterior]
        rows = [p[1] for p in exterior]
        mean_c = float(np.mean(cols))
        mean_r = float(np.mean(rows))

        world_center_x = float((mean_c / (w - 1) - 0.5) * world_w)
        world_center_z = float((mean_r / (h - 1) - 0.5) * world_h)

        # Polygon coordinates in normalized UV and Three.js World coordinates
        poly_uv = [[round(p[0] / max(w - 1, 1), 4), round(p[1] / max(h - 1, 1), 4)] for p in simplified]
        poly_world = [
            [
                round((p[0] / max(w - 1, 1) - 0.5) * world_w, 3),
                round((p[1] / max(h - 1, 1) - 0.5) * world_h, 3),
            ]
            for p in simplified
        ]

        # Confidence from the rotation-ensemble spread when available
        # (exp(-sigma/2 m): 1.0 = all passes agree, 0.61 = 2 m disagreement);
        # otherwise from roof flatness. Reported with its basis, never invented.
        if uncertainty_m is not None:
            sigma = float(np.mean(uncertainty_m[comp_mask]))
            conf, conf_basis = float(np.exp(-sigma / 2.0)), f"ensemble spread {sigma:.2f} m"
        else:
            var = float(np.std(ndsm_vals))
            conf, conf_basis = float(np.exp(-var / max(height_agl, 1.0))), "roof-height spread"

        b_id = len(buildings) + 1
        kept[comp_mask] = b_id
        buildings.append({
            "id": b_id,
            "roof_elevation_m": round(roof_elevation_m, 2),
            "ground_elevation_m": round(ground_h, 2),
            "height_m": round(height_agl, 2),
            "storeys": storeys,
            "area_m2": area_m2,
            "volume_m3": volume_m3,
            "confidence": round(conf, 2),
            "confidence_basis": conf_basis,
            "source": "LoD1 model",
            "center": [round(world_center_x, 2), round(world_center_z, 2)],
            "polygon_world": poly_world,
            "polygon_uv": poly_uv,
        })

        if len(buildings) >= max_buildings:
            break

    total_area = sum(b["area_m2"] for b in buildings)
    heights = sorted(b["height_m"] for b in buildings)
    out = {
        "count": len(buildings),
        "total_footprint_m2": round(total_area, 1),
        "median_height_m": heights[len(heights) // 2] if heights else None,
        "max_height_m": heights[-1] if heights else None,
        "buildings": buildings,
    }
    if return_labels:
        out["_labels"] = kept
    return out


def save_buildings_json(path: str | Path, buildings_data: dict[str, Any]) -> None:
    """Save building footprints and attributes as JSON."""
    Path(path).write_text(json.dumps(buildings_data, indent=2), encoding="utf-8")
