"""Image / raster input-output for DepthWizard.

Handles PNG, JPG and (Geo)TIFF input, detects whether the image carries a
usable coordinate reference system, and writes DSM outputs as GeoTIFF plus
the lightweight assets the 3D viewer consumes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine
from PIL import Image


@dataclass
class InputImage:
    rgb: np.ndarray                 # H x W x 3, uint8
    path: Path
    georeferenced: bool = False
    crs: object = None              # rasterio CRS or None
    transform: Affine = field(default_factory=Affine.identity)
    pixel_size_m: float | None = None   # ground sample distance (metres), if known

    @property
    def shape(self):
        return self.rgb.shape[:2]


def _to_uint8(bands: np.ndarray) -> np.ndarray:
    """Stretch arbitrary-dtype bands (e.g. 11/12/16-bit satellite DN) to uint8
    using a 2-98 percentile stretch per band."""
    if bands.dtype == np.uint8:
        return bands
    out = np.empty(bands.shape, dtype=np.uint8)
    for i in range(bands.shape[-1]):
        b = bands[..., i].astype(np.float32)
        valid = b[np.isfinite(b) & (b > 0)]
        if valid.size == 0:
            out[..., i] = 0
            continue
        lo, hi = np.percentile(valid, (2, 98))
        out[..., i] = np.clip((b - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    return out


def _metres_per_pixel(crs, transform: Affine, height: int) -> float | None:
    if crs is None:
        return None
    px = abs(transform.a)
    if crs.is_geographic:
        # degrees -> metres at the scene centre latitude
        lat = transform.f + transform.e * height / 2
        return px * 111_320 * np.cos(np.radians(lat))
    return px  # projected CRS, assume metres


def read_image(path: str | Path) -> InputImage:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff"):
        with rasterio.open(path) as src:
            count = src.count
            idx = [1, 2, 3] if count >= 3 else [1, 1, 1]
            bands = np.stack([src.read(i) for i in idx], axis=-1)
            crs = src.crs
            transform = src.transform
            geo = crs is not None and transform != Affine.identity()
        rgb = _to_uint8(bands)
        return InputImage(rgb=rgb, path=path, georeferenced=geo,
                          crs=crs if geo else None,
                          transform=transform if geo else Affine.identity(),
                          pixel_size_m=_metres_per_pixel(crs, transform, rgb.shape[0]) if geo else None)
    img = Image.open(path).convert("RGB")
    return InputImage(rgb=np.asarray(img), path=path)


def read_raster(path: str | Path):
    """Read a single-band raster (DEM, reference DSM). Returns (array, profile)."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        return arr, src.profile


def write_dsm(path: str | Path, dsm: np.ndarray, image: InputImage, *,
              units: str, description: str, vertical_datum: str | None = None) -> None:
    """Write the DSM as a Float32 GeoTIFF. Georeferenced inputs keep their
    CRS/transform so the DSM overlays exactly in QGIS/ArcGIS."""
    profile = dict(driver="GTiff", height=dsm.shape[0], width=dsm.shape[1],
                   count=1, dtype="float32", nodata=-9999.0,
                   compress="deflate", predictor=3, tiled=True)
    if image.georeferenced:
        profile.update(crs=image.crs, transform=image.transform)
    out = np.where(np.isfinite(dsm), dsm, -9999.0).astype(np.float32)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(out, 1)
        tags = dict(UNITS=units, DESCRIPTION=description, SOFTWARE="DepthWizard")
        if vertical_datum:
            tags["VERTICAL_DATUM"] = vertical_datum
        dst.update_tags(**tags)


def _resize(arr: np.ndarray, size: tuple[int, int], resample) -> np.ndarray:
    return np.asarray(Image.fromarray(arr).resize(size, resample))


def export_viewer_assets(out_dir: str | Path, image: InputImage, dsm: np.ndarray,
                         meta: dict, reference: np.ndarray | None = None,
                         dtm: np.ndarray | None = None,
                         confidence: np.ndarray | None = None,
                         buildings: dict | None = None,
                         mesh_max: int = 512, tex_max: int = 4096) -> None:
    """Assets for the Three.js viewer:
    texture.jpg    – the optical image (draped on the mesh)
    height.bin     – Float32 heights at mesh resolution (row-major)
    dtm.bin        – Float32 bare-earth terrain heights at mesh resolution (optional)
    ref.bin        – Float32 reference heights at mesh resolution (optional)
    confidence.bin – Float32 confidence values in [0, 1] (optional)
    buildings.json – LoD1 vector polygons and attributes (optional)
    meta.json      – dimensions, units, ground size, metrics, datum
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    h, w = dsm.shape

    s = min(1.0, tex_max / max(h, w))
    tex = image.rgb if s == 1.0 else _resize(image.rgb, (int(w * s), int(h * s)), Image.LANCZOS)
    Image.fromarray(tex).save(out / "texture.jpg", quality=92)

    m = min(1.0, mesh_max / max(h, w))
    mw, mh = max(2, int(round(w * m))), max(2, int(round(h * m)))

    def down(a):
        a = np.where(np.isfinite(a), a, np.nanmin(a)).astype(np.float32)
        return _resize(a, (mw, mh), Image.BILINEAR).astype("<f4")

    down(dsm).tofile(out / "height.bin")

    has_dtm = dtm is not None
    if has_dtm:
        down(dtm).tofile(out / "dtm.bin")

    has_ref = reference is not None
    if has_ref:
        down(reference).tofile(out / "ref.bin")

    has_conf = confidence is not None
    if has_conf:
        down(confidence).tofile(out / "confidence.bin")

    if buildings is not None:
        (out / "buildings.json").write_text(json.dumps(buildings, indent=2), encoding="utf-8")

    gsd = image.pixel_size_m or meta.get("assumed_gsd_m", 1.0)
    meta = dict(meta)
    meta.update(grid_w=mw, grid_h=mh, src_w=w, src_h=h,
                ground_w_m=w * gsd, ground_h_m=h * gsd, gsd_m=gsd,
                has_reference=has_ref, has_dtm=has_dtm, has_confidence=has_conf,
                buildings_count=len(buildings.get("buildings", [])) if buildings else 0,
                h_min=float(np.nanmin(dsm)), h_max=float(np.nanmax(dsm)))
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=float))


def _colormap(t: np.ndarray) -> np.ndarray:
    """Perceptual blue-green-yellow-white ramp for heights, t in [0,1]."""
    stops = np.array([[0.0, 0.16, 0.20, 0.45], [0.3, 0.10, 0.55, 0.55],
                      [0.6, 0.55, 0.78, 0.30], [0.85, 0.96, 0.84, 0.35], [1.0, 1.0, 0.97, 0.92]])
    out = np.empty(t.shape + (3,), np.float32)
    for c in range(3):
        out[..., c] = np.interp(t, stops[:, 0], stops[:, c + 1])
    return out


def save_preview(path, height: np.ndarray, gsd: float = 1.0) -> None:
    """Colour-relief + hillshade PNG of the DSM (for reports / slides)."""
    h = np.where(np.isfinite(height), height, np.nanmin(height))
    lo, hi = np.percentile(h, (1, 99))
    t = np.clip((h - lo) / max(hi - lo, 1e-6), 0, 1)
    gy, gx = np.gradient(h, gsd)
    az, el = np.radians(315), np.radians(45)
    n = np.sqrt(gx ** 2 + gy ** 2 + 1)
    shade = np.clip((-gx * np.sin(az) * np.cos(el) + gy * np.cos(az) * np.cos(el) + np.sin(el)) / n, 0, 1)
    rgb = _colormap(t) * (0.45 + 0.55 * shade[..., None])
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).save(path)
