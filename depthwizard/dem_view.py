"""Visualise an existing elevation raster (single-band DEM/DSM GeoTIFF).

This is NOT height estimation: the heights come straight from the file. It
lets users load a Copernicus / SRTM / CartoDEM / LiDAR tile and use every
viewer tool (topo, slope hazard, flood, landslide, exports). Every output is
labelled "input DEM (not estimated)" so it can't be mistaken for a result of
the single-image model.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine

from . import io as dio

_TOPO = [(0.0, (27, 56, 43)), (0.16, (60, 110, 66)), (0.33, (142, 160, 90)), (0.5, (216, 198, 137)),
         (0.67, (176, 137, 97)), (0.84, (153, 150, 147)), (1.0, (248, 249, 250))]


def is_elevation_raster(path) -> bool:
    """True for a single-band floating-point (or 16-bit signed) GeoTIFF."""
    p = Path(path)
    if p.suffix.lower() not in (".tif", ".tiff"):
        return False
    try:
        with rasterio.open(p) as src:
            if src.count != 1:
                return False
            kind = np.dtype(src.dtypes[0]).kind
            if kind == "f":
                return True
            # signed 16-bit DEMs (SRTM) are common, but so are single-band panchromatic
            # images; only accept integers when the name says it is an elevation model
            named = any(k in p.stem.lower() for k in ("dem", "dsm", "dtm", "srtm", "elev", "height", "cop30", "glo30"))
            return kind == "i" and named
    except Exception:  # noqa: BLE001
        return False


def _tint(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1)
    out = np.zeros(t.shape + (3,), np.float32)
    for (t0, c0), (t1, c1) in zip(_TOPO[:-1], _TOPO[1:]):
        m = (t >= t0) & (t <= t1)
        f = ((t - t0) / max(t1 - t0, 1e-9))[m][:, None]
        out[m] = np.array(c0) * (1 - f) + np.array(c1) * f
    return out


def shaded_relief(z: np.ndarray, gsd: float, azimuth: float = 315, elevation: float = 45) -> np.ndarray:
    """Hypsometric tint x hillshade, as an RGB texture for the 3D viewer."""
    zf = np.where(np.isfinite(z), z, np.nanmin(z))
    gy, gx = np.gradient(zf, gsd)
    az, el = np.radians(azimuth), np.radians(elevation)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(el) * np.cos(slope) + np.cos(el) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip(0.35 + 0.75 * shade, 0, 1.15)
    lo, hi = np.nanpercentile(zf, (2, 98))
    rgb = _tint((zf - lo) / max(hi - lo, 1e-6)) * shade[..., None]
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _sha16(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def run_dem_only(path, out_dir, *, max_pixels: int = 64_000_000, assumed_gsd_m: float = 1.0, log=print) -> dict:
    from .analysis import landslide_susceptibility
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log("input is a single-band elevation raster: visualising it directly (no height estimation)")
    with rasterio.open(path) as src:
        z = src.read(1).astype(np.float32)
        if src.nodata is not None:
            z[z == src.nodata] = np.nan
        crs, transform = src.crs, src.transform
        units_tag = (src.tags().get("UNITS") or "").lower()
    z[(z < -1000) | (z > 10000)] = np.nan
    geo = crs is not None and transform != Affine.identity()
    gsd = dio._metres_per_pixel(crs, transform, z.shape[0]) if geo else assumed_gsd_m
    if not np.isfinite(z).any():
        raise ValueError("the elevation raster has no valid cells")
    fill = float(np.nanmin(z))
    img = dio.InputImage(rgb=shaded_relief(z, gsd), path=Path(path), georeferenced=geo,
                         crs=crs if geo else None, transform=transform if geo else Affine.identity(),
                         pixel_size_m=gsd if geo else None)
    img, shrink = dio.limit_pixels(img, max_pixels)
    if shrink > 1:
        from PIL import Image
        z = np.asarray(Image.fromarray(np.where(np.isfinite(z), z, fill)).resize((img.shape[1], img.shape[0]), Image.BILINEAR))
        gsd = gsd * shrink
        log(f"  downsampled {shrink:.2f}x to {img.shape[1]}x{img.shape[0]} px")
    z = np.where(np.isfinite(z), z, fill).astype(np.float32)
    datum = "as input raster"
    label = "Input DEM (not estimated)"
    dio.write_dsm(out / "dsm.tif", z, img, units="metre", description=label, vertical_datum=datum)
    dio.write_dsm(out / "dtm.tif", z, img, units="metre", description=label + " used as terrain", vertical_datum=datum)
    susc, sstats = landslide_susceptibility(z, img.rgb, gsd)
    meta = {
        "input": Path(path).name,
        "input_paths": {"image": str(Path(path).resolve())},
        "input_kind": "elevation_raster",
        "backbone": "none – input DEM visualised directly",
        "georeferenced": geo, "crs": crs.to_string() if geo else None,
        "transform": list(img.transform)[:6] if geo else None,
        "units": "metre", "vertical_datum": datum,
        "calibration": {"method": "input-dem", "scale_source": "heights read from the input raster",
                        "evidence_level": "input data (not estimated)",
                        "note": "Not a DepthWizard estimate. Units tag: " + (units_tag or "not stated; assumed metres")},
        "scene": "terrain", "assumed_gsd_m": assumed_gsd_m, "dsm_file": "dsm.tif",
        "analytics": {"landslide": sstats}, "buildings_count": 0, "total_footprint_m2": 0,
        "evidence_bundle": {"image_sha256": _sha16(path), "model_identifier": "none", "calibration_method": "input-dem",
                            "evidence_level": "input data (not estimated)"},
    }
    if geo:
        try:
            from rasterio.warp import transform as _tr
            hh, ww = img.shape
            xs, ys = zip(*[img.transform * (c, r) for c, r in ((0, 0), (ww, 0), (0, hh), (ww, hh))])
            lon, lat = _tr(img.crs, "EPSG:4326", list(xs), list(ys))
            meta["corners_lonlat"] = [[float(a), float(b)] for a, b in zip(lon, lat)]
        except Exception:  # noqa: BLE001
            pass
    meta["timing_s"] = {"total": round(time.time() - t0, 2)}
    dio.export_viewer_assets(out / "viewer", img, z, meta, dtm=z, susceptibility=susc)
    dio.save_preview(out / "preview.png", z, gsd=gsd)
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=float))
    log(f"done in {meta['timing_s']['total']} s -> {out}")
    return meta
