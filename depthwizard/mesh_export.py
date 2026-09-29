"""Portable textured terrain exports from a completed DepthWizard scene.

GLB and OBJ both use the saved, un-smoothed DSM grid.  Metric terrain is in
local metres with its minimum elevation subtracted for numerical stability;
the absolute elevation offset and source georeferencing travel in metadata.
Relative rDSM terrain uses unitless coordinates throughout.
"""
from __future__ import annotations

import json
import math
import os
import struct
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.crs import CRS


ALLOWED_RESOLUTIONS = (128, 256, 512)


def _metre_dimensions(meta: dict) -> tuple[float, float, str]:
    """Derive both horizontal spans from the source affine, including rotation."""
    transform, crs_text = meta.get("transform"), meta.get("crs")
    if transform and crs_text:
        a, b, _c, d, e, f = (float(v) for v in transform)
        crs = CRS.from_user_input(crs_text)
        if crs.is_projected:
            factor = crs.linear_units_factor[1]
            col_m = math.hypot(a, d) * factor
            row_m = math.hypot(b, e) * factor
            basis = f"{crs_text} projected axis units converted to metres"
        elif crs.is_geographic:
            # Ellipsoidal degree-length approximation at the scene centre.
            lat = math.radians(f + e * float(meta["src_h"]) / 2)
            m_lon = 111412.84 * math.cos(lat) - 93.5 * math.cos(3 * lat)
            m_lat = 111132.92 - 559.82 * math.cos(2 * lat) + 1.175 * math.cos(4 * lat)
            col_m = math.hypot(a * m_lon, d * m_lat)
            row_m = math.hypot(b * m_lon, e * m_lat)
            basis = f"{crs_text} local degree-length approximation"
        else:
            col_m = row_m = 0
            basis = "viewer ground dimensions"
        if col_m > 0 and row_m > 0:
            return col_m * float(meta["src_w"]), row_m * float(meta["src_h"]), basis
    return float(meta["ground_w_m"]), float(meta["ground_h_m"]), "viewer ground dimensions"


def _grid(job_dir: Path, resolution: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
    if resolution not in ALLOWED_RESOLUTIONS:
        raise ValueError(f"resolution must be one of {ALLOWED_RESOLUTIONS}")
    viewer = job_dir / "viewer"
    meta = json.loads((viewer / "meta.json").read_text(encoding="utf-8"))
    source_w, source_h = int(meta["grid_w"]), int(meta["grid_h"])
    width = max(2, round(source_w * min(1.0, resolution / max(source_w, source_h))))
    height = max(2, round(source_h * min(1.0, resolution / max(source_w, source_h))))

    if meta["units"] == "metre":
        h = np.fromfile(viewer / "height.bin", dtype="<f4")
        if h.size != source_w * source_h:
            raise ValueError("saved terrain grid does not match scene metadata")
        h = h.reshape(source_h, source_w)
        ground_w, ground_h, dimension_basis = _metre_dimensions(meta)
        elevation_base = float(np.nanmin(h))
        if not math.isfinite(elevation_base):
            raise ValueError("saved terrain has no finite heights")
        coordinate_units = "metre"
        vertical_basis = "raw metric DSM; minimum saved-grid elevation removed from mesh Y"
        y_scale = 1.0
    elif meta["units"] == "relative":
        # The viewer's height.bin has an arbitrary visual scale, and may have
        # been fitted to a supplied reference.  Preserve the actual rDSM here.
        with rasterio.open(job_dir / "rdsm.tif") as src:
            h = src.read(1).astype(np.float32)
            if src.nodata is not None:
                h[h == src.nodata] = np.nan
        source_h, source_w = h.shape
        aspect = source_w / source_h
        ground_w, ground_h = float(aspect), 1.0
        dimension_basis = "unitless image aspect ratio"
        elevation_base = float(np.nanmin(h))
        if not math.isfinite(elevation_base):
            raise ValueError("saved relative DSM has no finite heights")
        coordinate_units = "relative"
        vertical_basis = "raw relative DSM with arbitrary 0.08 display relief"
        y_scale = 0.08 * max(ground_w, ground_h)
    else:
        raise ValueError("unknown scene height units")

    h = np.where(np.isfinite(h), h, elevation_base).astype(np.float32)
    if h.shape != (height, width):
        h = np.asarray(Image.fromarray(h).resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)
    yy = (h - elevation_base) * y_scale
    zz, xx = np.meshgrid(np.linspace(-ground_h / 2, ground_h / 2, height, dtype=np.float32),
                         np.linspace(-ground_w / 2, ground_w / 2, width, dtype=np.float32), indexing="ij")
    positions = np.stack((xx, yy, zz), axis=-1).reshape(-1, 3).astype("<f4")

    dz, dx = np.gradient(yy, ground_h / (height - 1), ground_w / (width - 1))
    normals = np.stack((-dx, np.ones_like(dx), -dz), axis=-1)
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    normals = normals.reshape(-1, 3).astype("<f4")
    v, u = np.meshgrid(np.linspace(0, 1, height, dtype=np.float32),
                       np.linspace(0, 1, width, dtype=np.float32), indexing="ij")
    uv = np.stack((u, v), axis=-1).reshape(-1, 2).astype("<f4")

    r, c = np.divmod(np.arange((height - 1) * (width - 1), dtype=np.uint32), width - 1)
    i = r * width + c
    # X to the right and Z toward the bottom: these triangles face +Y.
    indices = np.stack((np.stack((i, i + width, i + 1), axis=-1),
                        np.stack((i + 1, i + width, i + width + 1), axis=-1)), axis=1)
    indices = indices.reshape(-1, 3).astype("<u4")

    info = {
        "source": meta.get("input"),
        "backbone": meta.get("backbone"),
        "calibration": meta.get("calibration"),
        "coordinate_units": coordinate_units,
        "georeferenced": bool(meta.get("georeferenced")),
        "crs": meta.get("crs"),
        "source_affine": meta.get("transform"),
        "local_axes": "X right across image, Y up, Z down image",
        "horizontal_extent": [ground_w, ground_h],
        "horizontal_basis": dimension_basis,
        "elevation_base": elevation_base,
        "vertical_basis": vertical_basis,
        "mesh_grid": [width, height],
        "source_dsm": meta.get("dsm_file"),
        "note": "Mesh samples a reduced grid; the GeoTIFF is the authoritative full-resolution DSM."
    }
    return positions, normals, uv, indices, info


def _buffer_view(binary: bytearray, payload: bytes, *, target: int | None = None) -> dict:
    binary.extend(b"\x00" * (-len(binary) % 4))
    view = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(payload)}
    if target is not None:
        view["target"] = target
    binary.extend(payload)
    return view


def export_glb(job_dir: str | Path, destination: str | Path, resolution: int = 256) -> Path:
    """Write a self-contained glTF 2.0 binary terrain with embedded JPEG."""
    job_dir, destination = Path(job_dir), Path(destination)
    positions, normals, uv, indices, info = _grid(job_dir, resolution)
    binary = bytearray()
    views = [
        _buffer_view(binary, positions.tobytes(), target=34962),
        _buffer_view(binary, normals.tobytes(), target=34962),
        _buffer_view(binary, uv.tobytes(), target=34962),
        _buffer_view(binary, indices.tobytes(), target=34963),
        _buffer_view(binary, (job_dir / "viewer" / "texture.jpg").read_bytes()),
    ]
    pos_min, pos_max = positions.min(axis=0).tolist(), positions.max(axis=0).tolist()
    gltf = {
        "asset": {"version": "2.0", "generator": "DepthWizard terrain exporter"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "Estimated terrain"}],
        "meshes": [{"name": "Optical drape on estimated DSM", "extras": info,
                    "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                    "indices": 3, "material": 0, "mode": 4}]}],
        "materials": [{"name": "Optical image", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
        "textures": [{"source": 0}],
        "images": [{"bufferView": 4, "mimeType": "image/jpeg", "name": "Optical image"}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": pos_min, "max": pos_max},
            {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": len(uv), "type": "VEC2"},
            {"bufferView": 3, "componentType": 5125, "count": indices.size, "type": "SCALAR"},
        ],
        "extras": info,
    }
    json_chunk = json.dumps(gltf, separators=(",", ":"), allow_nan=False).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    binary.extend(b"\x00" * (-len(binary) % 4))
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".tmp")
    with temp.open("wb") as out:
        out.write(struct.pack("<4sII", b"glTF", 2, total))
        out.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        out.write(json_chunk)
        out.write(struct.pack("<I4s", len(binary), b"BIN\x00"))
        out.write(binary)
    os.replace(temp, destination)
    return destination


def export_obj_zip(job_dir: str | Path, destination: str | Path, resolution: int = 256) -> Path:
    """Write OBJ, MTL, JPEG and coordinate notes as one downloadable ZIP."""
    job_dir, destination = Path(job_dir), Path(destination)
    positions, normals, uv, indices, info = _grid(job_dir, resolution)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(job_dir / "viewer" / "texture.jpg", "texture.jpg")
        archive.writestr("terrain.mtl", "newmtl optical\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\nillum 1\nmap_Kd texture.jpg\n")
        archive.writestr("metadata.json", json.dumps(info, indent=2))
        with archive.open("terrain.obj", "w") as out:
            def line(s: str):
                out.write(s.encode("ascii"))
            line("# DepthWizard estimated terrain; see metadata.json for units and elevation offset\n")
            line("mtllib terrain.mtl\no terrain\n")
            for x, y, z in positions:
                line(f"v {x:.6f} {y:.6f} {z:.6f}\n")
            # OBJ V increases upward, whereas the JPEG's first row is at top.
            for u, v in uv:
                line(f"vt {u:.7f} {1 - v:.7f}\n")
            for x, y, z in normals:
                line(f"vn {x:.7f} {y:.7f} {z:.7f}\n")
            line("usemtl optical\ns 1\n")
            for a, b, c in indices + 1:
                line(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n")
    os.replace(temp, destination)
    return destination
