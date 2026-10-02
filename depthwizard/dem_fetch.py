"""Keyless, windowed access to public Copernicus GLO-30 COG tiles.

Only the image footprint is cached. The cache is reusable offline for the
same footprint and avoids copying ~40 MB tiles for a small optical image.
"""
from __future__ import annotations

import math
import os
import urllib.error
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path

import numpy as np

BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
DOWNLOAD_ERROR = "Could not download Copernicus DEM (offline?). Supply a DEM file instead."


def tile_name(lat: int, lon: int) -> str:
    """Name the 1-degree cell by its integer south-west corner."""
    if not -90 <= lat <= 89 or not -180 <= lon <= 179:
        raise ValueError("Copernicus tile coordinates are outside latitude/longitude bounds")
    return f"Copernicus_DSM_COG_10_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM"


def tile_names_for_bounds(west: float, south: float, east: float, north: float) -> list[str]:
    if not all(map(math.isfinite, (west, south, east, north))) or west >= east or south >= north:
        raise ValueError("Invalid optical footprint for Copernicus DEM")
    if west < -180 or east > 180 or south < -90 or north > 90:
        raise ValueError("Optical footprint crosses unsupported latitude/longitude bounds")
    names = [tile_name(lat, lon)
             for lat in range(math.floor(south), math.ceil(north))
             for lon in range(math.floor(west), math.ceil(east))]
    if len(names) > 64:
        raise ValueError("Optical footprint spans too many Copernicus tiles (64 maximum)")
    return names


def _footprint(image) -> tuple[float, float, float, float]:
    from rasterio.warp import transform_bounds

    if not image.georeferenced or image.crs is None:
        raise ValueError("Copernicus DEM requires a georeferenced image")
    h, w = image.shape
    xy = [image.transform * (c, r) for c, r in ((0, 0), (w, 0), (0, h), (w, h))]
    x, y = zip(*xy)
    west, south, east, north = transform_bounds(
        image.crs, "EPSG:4326", min(x), min(y), max(x), max(y), densify_pts=21)
    return west - .005, south - .005, east + .005, north + .005


def _head_exists(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=12) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def _cached_window(name: str, bounds, cache: Path) -> Path | None:
    import rasterio
    from rasterio.windows import from_bounds, transform as window_transform

    # Four-decimal coordinates are stable for repeated identical image grids;
    # include all coordinates to prevent two different windows colliding.
    import hashlib
    key = hashlib.sha256((name + ":" + ",".join(f"{v:.10f}" for v in bounds)).encode()).hexdigest()[:20]
    target = cache / f"{name}-{key}.tif"
    if target.is_file():
        return target
    url = f"{BASE}/{name}/{name}.tif"
    if not _head_exists(url):
        return None
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="1"):
        with rasterio.open(url) as src:
            left = max(bounds[0], src.bounds.left)
            bottom = max(bounds[1], src.bounds.bottom)
            right = min(bounds[2], src.bounds.right)
            top = min(bounds[3], src.bounds.top)
            if right <= left or top <= bottom:
                return None
            window = from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()
            window = window.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
            values = src.read(1, window=window, masked=True)
            profile = src.profile.copy()
            profile.update(driver="GTiff", width=values.shape[1], height=values.shape[0],
                           transform=window_transform(window, src.transform), compress="deflate")
            tmp = target.with_name(f"{target.stem}-{uuid.uuid4().hex}.tmp.tif")
            try:
                with rasterio.open(tmp, "w", **profile) as dst:
                    dst.write(values.filled(src.nodata if src.nodata is not None else np.nan), 1)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
    return target


def fetch_copernicus_glo30(image, out_path: str | Path,
                           cache_dir: str | Path | None = None) -> Path:
    """Mosaic cached COG windows over the optical footprint into a GeoTIFF."""
    import rasterio
    from rasterio.merge import merge

    bounds = _footprint(image)
    cache = Path(cache_dir or os.environ.get("DEPTHWIZARD_DEM_CACHE", "data/dem_cache"))
    cache.mkdir(parents=True, exist_ok=True)
    windows = []
    try:
        for name in tile_names_for_bounds(*bounds):
            path = _cached_window(name, bounds, cache)
            if path is not None:
                windows.append(path)
        if not windows:
            raise RuntimeError("No Copernicus DEM tile covers this image. Supply a DEM file instead.")
        with ExitStack() as stack:
            sources = [stack.enter_context(rasterio.open(path)) for path in windows]
            data, transform = merge(sources, bounds=bounds, nodata=np.nan)
            profile = sources[0].profile.copy()
            profile.update(width=data.shape[2], height=data.shape[1], transform=transform,
                           count=1, dtype="float32", nodata=np.nan, compress="deflate")
        output = Path(out_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output, "w", **profile) as dst:
            dst.write(data[0].astype(np.float32), 1)
            dst.update_tags(SOURCE="Copernicus DEM GLO-30 Public (AWS Open Data)",
                            VERTICAL_DATUM="EGM2008 geoid (Copernicus GLO-30)",
                            SOURCE_TILES=",".join(path.name.split("-")[0] for path in windows))
        return output
    except (OSError, urllib.error.URLError, rasterio.errors.RasterioError) as exc:
        raise RuntimeError(DOWNLOAD_ERROR) from exc


def cached_tile_names(dem_path: str | Path) -> list[str]:
    """Read the tile provenance written by this fetcher."""
    import rasterio
    with rasterio.open(dem_path) as src:
        return [x for x in src.tags().get("SOURCE_TILES", "").split(",") if x]
