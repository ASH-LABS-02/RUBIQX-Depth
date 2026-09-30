"""Interchange exports for GIS, planning and 3D tools.

* CityJSON 1.1 LoD1 buildings (flat-roof solids) – opens in QGIS (CityJSON
  loader), FME, ninja.cityjson.org, and converts to CityGML with citygml-tools.
* PLY point cloud of the DSM, coloured from the optical image – opens in
  CloudCompare, MeshLab, Blender.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image


def _job_meta(job_dir: Path) -> dict:
    return json.loads((job_dir / "meta.json").read_text())


def export_cityjson(job_dir: str | Path, destination: str | Path) -> Path:
    job_dir = Path(job_dir)
    meta = _job_meta(job_dir)
    bj = json.loads((job_dir / "viewer" / "buildings.json").read_text())
    vmeta = json.loads((job_dir / "viewer" / "meta.json").read_text())
    metric = meta.get("units") == "metre"
    t = meta.get("transform")
    W, H = vmeta["ground_w_m"], vmeta["ground_h_m"]
    src_w, src_h = vmeta["src_w"], vmeta["src_h"]

    def to_xy(u, v):
        if t:  # georeferenced: pixel -> map coordinates
            col, row = u * src_w, v * src_h
            return t[0] * col + t[1] * row + t[2], t[3] * col + t[4] * row + t[5]
        return u * W, (1 - v) * H          # local metres, y north-up

    vertices, objects = [], {}
    for b in bj.get("buildings", []):
        ring = b.get("polygon_uv") or []
        if len(ring) < 4:
            continue
        ring = ring[:-1] if ring[0] == ring[-1] else ring
        pts = [to_xy(u, v) for u, v in ring]
        # CityJSON needs counter-clockwise exterior rings seen from above
        area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                   for i in range(len(pts)))
        if area < 0:
            pts = pts[::-1]
        z0, z1 = float(b["ground_elevation_m"]), float(b["roof_elevation_m"])
        base = len(vertices)
        n = len(pts)
        vertices += [[x, y, z0] for x, y in pts] + [[x, y, z1] for x, y in pts]
        bottom = [[list(range(base + n - 1, base - 1, -1))]]
        top = [[list(range(base + n, base + 2 * n))]]
        walls = [[[base + i, base + (i + 1) % n, base + n + (i + 1) % n, base + n + i]] for i in range(n)]
        attrs = {"measuredHeight": round(z1 - z0, 2), "roofElevation": z1, "groundElevation": z0,
                 "footprintArea": b.get("area_m2"), "confidence": b.get("confidence"),
                 "heightSource": meta.get("calibration", {}).get("scale_source")}
        if b.get("storeys"):
            attrs["storeysAboveGround"] = b["storeys"]
        objects[f"B{b['id']}"] = {"type": "Building", "attributes": attrs,
                                   "geometry": [{"type": "Solid", "lod": "1",
                                                 "boundaries": [bottom + top + walls]}]}
    scale = 0.001
    verts = np.round(np.array(vertices or [[0, 0, 0]]) / scale).astype(np.int64)
    origin = verts.min(0)
    doc = {"type": "CityJSON", "version": "1.1",
           "transform": {"scale": [scale] * 3, "translate": (origin * scale).tolist()},
           "metadata": {"title": f"DepthWizard LoD1 buildings – {meta.get('input')}",
                        "referenceSystem": (f"https://www.opengis.net/def/crs/{meta['crs'].replace(':', '/0/')}"
                                            if meta.get("crs") and meta["crs"].startswith("EPSG") else None)},
           "CityObjects": objects,
           "vertices": (verts - origin).tolist() if vertices else []}
    if not metric:
        doc["metadata"]["note"] = "relative heights (non-georeferenced input)"
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
