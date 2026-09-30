"""Interchange exports for GIS, planning and 3D tools.

* CityJSON 1.1 building solids: LoD1 flat-roof fallback and LoD2.0 inferred
  plane/gable roofs where geometry is sufficiently supported.  Opens in QGIS
  (CityJSON loader), FME, ninja.cityjson.org, and converts to CityGML.
* PLY point cloud of the DSM, coloured from the optical image – opens in
  CloudCompare, MeshLab, Blender.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image


def _job_meta(job_dir: Path) -> dict:
    return json.loads((job_dir / "meta.json").read_text())


def _signed_area(points: list[tuple[float, float]] | list[list[float]]) -> float:
    return 0.5 * sum(points[i][0] * points[(i + 1) % len(points)][1]
                     - points[(i + 1) % len(points)][0] * points[i][1]
                     for i in range(len(points)))


def _is_convex(points: list[tuple[float, float]] | list[list[float]]) -> bool:
    """Gable splitting is safe only on a convex footprint."""
    turns = []
    for i in range(len(points)):
        a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-8:
            turns.append(cross)
    return bool(turns) and (all(t > 0 for t in turns) or all(t < 0 for t in turns))


def _roof_perimeter(
    points: list[tuple[float, float]],
    world: list[tuple[float, float]],
    building: dict,
    vertical_scale: float = 1.0,
) -> tuple[list[tuple[float, float]], list[float], list[list[int]], str]:
    """Return roof perimeter, elevations and face rings; flat on invalid fit.

    The gable case inserts the two ridge/eave intersections into the perimeter
    so roof and wall faces share vertices and form one closed Solid shell.
    """
    z0 = float(building["ground_elevation_m"]) * vertical_scale
    z_flat = float(building["roof_elevation_m"]) * vertical_scale
    height = float(building.get("height_m") or 0) * vertical_scale
    flat = (points, [z_flat] * len(points), [list(range(len(points)))], "flat")
    roof = building.get("roof_fit") or {}
    kind = roof.get("type")
    if kind not in ("plane", "gable") or height <= 0:
        return flat

    def valid_top(z_values: list[float]) -> bool:
        # An extrapolated roof cannot pierce its own ground face or be more
        # than 1.6× the building height.  Fall back rather than clamp a plane.
        return (all(math.isfinite(z) for z in z_values)
                and min(z_values) > z0 + max(0.2, 0.04 * height)
                and max(z_values) < z0 + 1.6 * height)

    if kind == "plane":
        center = roof.get("center_world")
        grad = roof.get("gradient_fraction_per_m")
        if not (isinstance(center, list) and len(center) == 2
                and isinstance(grad, list) and len(grad) == 2):
            return flat
        try:
            fraction = float(roof["center_fraction"])
            top = [z0 + height * (fraction + float(grad[0]) * (x - float(center[0]))
                                  + float(grad[1]) * (y - float(center[1])))
                   for x, y in world]
        except (KeyError, TypeError, ValueError):
            return flat
        return (points, top, [list(range(len(points)))], "plane") if valid_top(top) else flat

    ridge = roof.get("ridge_world")
    if (not _is_convex(points) or not isinstance(ridge, list) or len(ridge) != 2
            or any(not isinstance(p, list) or len(p) != 2 for p in ridge)):
        return flat
    try:
        ax, ay = map(float, ridge[0])
        bx, by = map(float, ridge[1])
        rise_fraction_per_m = float(roof["pitch_fraction_per_m"])
        ridge_fraction = float(roof["ridge_fraction"])
    except (KeyError, TypeError, ValueError):
        return flat
    dx, dy = bx - ax, by - ay
    ridge_length = math.hypot(dx, dy)
    if ridge_length < 1e-3 or rise_fraction_per_m <= 0:
        return flat
    signed = [((dx * (wz - ay) - dy * (wx - ax)) / ridge_length) for wx, wz in world]
    eps = 1e-7
    if min(signed) >= -eps or max(signed) <= eps:
        return flat  # ridge does not split the building

    # Insert a shared vertex at every strict crossing of the infinite ridge
    # and footprint boundary.  Convexity guarantees exactly two crossings.
    perimeter = []
    distances = []
    for i, p in enumerate(points):
        j = (i + 1) % len(points)
        perimeter.append(p)
        distances.append(signed[i])
        s0, s1 = signed[i], signed[j]
        if s0 * s1 < -(eps * eps):
            alpha = s0 / (s0 - s1)
            p1 = points[j]
            perimeter.append((p[0] + alpha * (p1[0] - p[0]),
                              p[1] + alpha * (p1[1] - p[1])))
            distances.append(0.0)
    crossing_count = sum(abs(s) <= eps for s in distances)
    if crossing_count != 2:
        return flat
    positive = [i for i, s in enumerate(distances) if s >= -eps]
    negative = [i for i, s in enumerate(distances) if s <= eps]
    if len(positive) < 3 or len(negative) < 3:
        return flat
    whole_area = abs(_signed_area(perimeter))
    if (abs(_signed_area([perimeter[i] for i in positive])) < whole_area * 0.03
            or abs(_signed_area([perimeter[i] for i in negative])) < whole_area * 0.03):
        return flat
    top = [z0 + height * (ridge_fraction - rise_fraction_per_m * abs(s))
           for s in distances]
    return (perimeter, top, [positive, negative], "gable") if valid_top(top) else flat


def export_cityjson(job_dir: str | Path, destination: str | Path) -> Path:
    job_dir = Path(job_dir)
    meta = _job_meta(job_dir)
    bj = json.loads((job_dir / "viewer" / "buildings.json").read_text())
    vmeta = json.loads((job_dir / "viewer" / "meta.json").read_text())
    metric = meta.get("units") == "metre"
    t = meta.get("transform")
    W, H = vmeta["ground_w_m"], vmeta["ground_h_m"]
    src_w, src_h = vmeta["src_w"], vmeta["src_h"]
    # Relative z is dimensionless in buildings.json.  Convert it to the same
    # arbitrary local display frame used by the Three.js terrain, and label
    # that frame explicitly.  It must not be mistaken for surveyed metres.
    vertical_scale = 1.0 if metric else float(meta.get("display_height_m") or 1.0)
    if not math.isfinite(vertical_scale) or vertical_scale <= 0:
        raise ValueError("relative CityJSON requires a positive display_height_m")

    def to_xy(u, v):
        if t:  # georeferenced: pixel -> map coordinates
            # polygon_uv stores raster corner coordinates divided by (N-1).
            # Multiplying by N shifted far-edge buildings up to one pixel.
            col, row = u * max(src_w - 1, 1), v * max(src_h - 1, 1)
            return t[0] * col + t[1] * row + t[2], t[3] * col + t[4] * row + t[5]
        return u * W, (1 - v) * H          # local metres, y north-up

    vertices, objects = [], {}
    for b in bj.get("buildings", []):
        ring = b.get("polygon_uv") or []
        if len(ring) < 4:
            continue
        ring = ring[:-1] if ring[0] == ring[-1] else ring
        # Retain a parallel scene-world ring for the fitted roof equation.
        ring = [p for i, p in enumerate(ring) if i == 0 or p != ring[i - 1]]
        if len(ring) < 3:
            continue
        pts = [to_xy(u, v) for u, v in ring]
        scene_world = [((u - 0.5) * W, (v - 0.5) * H) for u, v in ring]
        # CityJSON needs counter-clockwise exterior rings seen from above
        area = _signed_area(pts)
        if abs(area) < 1e-8:
            continue
        if area < 0:
            pts = pts[::-1]
            scene_world = scene_world[::-1]
        z0 = float(b["ground_elevation_m"]) * vertical_scale
        pts, roof_z, roof_faces, roof_kind = _roof_perimeter(
            pts, scene_world, b, vertical_scale=vertical_scale)
        base = len(vertices)
        n = len(pts)
        vertices += [[x, y, z0] for x, y in pts] + [[x, y, roof_z[i]] for i, (x, y) in enumerate(pts)]
        shell = [
            [[base + i for i in range(n - 1, -1, -1)]],
            *[[[base + n + i for i in face]] for face in roof_faces],
            *[[[base + i, base + (i + 1) % n,
                base + n + (i + 1) % n, base + n + i]] for i in range(n)],
        ]
        top_elevation = float(max(roof_z))
        attrs = {"roofElevation": round(top_elevation, 2), "groundElevation": z0,
                 "footprintArea": b.get("area_m2"), "confidence": b.get("confidence"),
                 "heightSource": meta.get("calibration", {}).get("scale_source"),
                 "roofGeometry": roof_kind}
        if metric:
            attrs["measuredHeight"] = (round(top_elevation - z0, 2) if roof_kind == "flat"
                                       else round(float(b["height_m"]), 2))
        else:
            attrs["relativeHeight"] = float(b["height_m"])
            attrs["displayHeight"] = round(top_elevation - z0, 2)
            attrs["heightUnit"] = "arbitrary local display unit"
            attrs["verticalScaleFromRelative"] = vertical_scale
        if b.get("storeys"):
            attrs["storeysAboveGround"] = b["storeys"]
        if b.get("roof_fit"):
            attrs["roofShapeHypothesis"] = b["roof_fit"].get("type", "flat")
            attrs["roofInference"] = "single-view nDSM; unverified visual hypothesis"
            if b["roof_fit"].get("pitch_deg") is not None:
                attrs["roofPitchEstimateDeg"] = b["roof_fit"]["pitch_deg"]
        semantics = {"surfaces": [{"type": "GroundSurface"}, {"type": "RoofSurface"},
                                  {"type": "WallSurface"}],
                     "values": [[0] + [1] * len(roof_faces) + [2] * n]}
        objects[f"B{b['id']}"] = {"type": "Building", "attributes": attrs,
                                   "geometry": [{"type": "Solid",
                                                 "lod": "2.0" if roof_kind != "flat" else "1.0",
                                                 "boundaries": [shell],
                                                 "semantics": semantics}]}
    scale = 0.001
    verts = np.round(np.array(vertices or [[0, 0, 0]]) / scale).astype(np.int64)
    origin = verts.min(0)
    doc = {"type": "CityJSON", "version": "1.1",
           "transform": {"scale": [scale] * 3, "translate": (origin * scale).tolist()},
           "metadata": {"title": f"DepthWizard inferred building solids – {meta.get('input')}",
                        "referenceSystem": (f"https://www.opengis.net/def/crs/{meta['crs'].replace(':', '/0/')}"
                                            if meta.get("crs") and meta["crs"].startswith("EPSG") else None)},
           "CityObjects": objects,
           "vertices": (verts - origin).tolist() if vertices else []}
    if not metric:
        doc["metadata"]["title"] += " (non-georeferenced; arbitrary display scale)"
        doc["depthwizard"] = {
            "coordinateFrame": "local display grid; not georeferenced",
            "horizontalScale": f"{meta.get('assumed_gsd_m', 1.0)} assumed display units per pixel",
            "verticalScale": f"{vertical_scale} display units per relative-height unit",
            "metricElevation": False,
        }
    if doc["metadata"].get("referenceSystem") is None:
        doc["metadata"].pop("referenceSystem", None)
    out = Path(destination)
    out.write_text(json.dumps(doc))
    return out


def export_ply(job_dir: str | Path, destination: str | Path, max_points: int = 1_000_000) -> Path:
    job_dir = Path(job_dir)
    meta = _job_meta(job_dir)
    dsm_path = job_dir / meta.get("dsm_file", "dsm.tif")
    with rasterio.open(dsm_path) as src:
        z = src.read(1).astype(np.float32)
        nod = src.nodata
        tr = src.transform
        geo = src.crs is not None
    if nod is not None:
        z[z == nod] = np.nan
    h, w = z.shape
    step = max(1, int(np.ceil(np.sqrt(h * w / max_points))))
    z = z[::step, ::step]
    rgb = np.asarray(Image.open(job_dir / "viewer" / "texture.jpg").convert("RGB")
                     .resize((w, h), Image.BILINEAR))[::step, ::step]
    rows, cols = np.mgrid[0:h:step, 0:w:step]
    if geo:
        x = tr.a * cols + tr.b * rows + tr.c
        y = tr.d * cols + tr.e * rows + tr.f
    else:
        gsd = meta.get("assumed_gsd_m", 1.0)
        x, y = cols * gsd, -rows * gsd
        z = z * meta.get("display_height_m", 1.0)
    ok = np.isfinite(z)
    x0, y0 = float(np.nanmin(x)), float(np.nanmin(y))
    pts = np.stack([x[ok] - x0, y[ok] - y0, z[ok]], 1).astype("<f4")
    col = rgb[ok].astype(np.uint8)
    out = Path(destination)
    with out.open("wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\n"
                 f"comment DepthWizard DSM point cloud; add x0={x0:.3f} y0={y0:.3f} for map coordinates\n"
                 f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\n"
                 "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        rec = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                        ("r", "u1"), ("g", "u1"), ("b", "u1")])
        rec["x"], rec["y"], rec["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
        rec["r"], rec["g"], rec["b"] = col[:, 0], col[:, 1], col[:, 2]
        f.write(rec.tobytes())
    return out
