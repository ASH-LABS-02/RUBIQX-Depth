"""Download the CC BY 4.0 Quesenbank RGB, DSM and DEM source rasters.

Source: https://zenodo.org/records/7554598 (Jackisch et al., 2023).
The files come from the same UAV survey. The DSM is reference data only;
the DEM is supplied as the calibration source when evaluating the pipeline.
"""
from __future__ import annotations

import hashlib
import csv
import math
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1] / "samples" / "quesenbank"
BASE = "https://zenodo.org/records/7554598/files"
FILES = {
    "M300_Quesenbank_AltumMSI_Orthomosaic_RGB.tif": "ae2d92f02a14d82869420d66132fc1b5",
    "M300_Quesenbank_AltumMSI_DSM.tif": "181f3491a51b8f98f9c52b31575211ee",
    "M300_Quesenbank_AltumMSI_DEM.tif": "b43c627ba6b931d400afef59b74cbfff",
}
CROPS = {"forest_north": (768, 512), "forest_south": (1280, 1024)}
SIZE = 1024


def md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - matching publisher's file checksums
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    raw = ROOT / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        dest = raw / name
        if dest.exists() and md5(dest) == expected:
            print(f"verified {name}", flush=True)
            continue
        temp = dest.with_suffix(dest.suffix + ".part")
        print(f"downloading {name}", flush=True)
        request = Request(f"{BASE}/{name}?download=1", headers={"User-Agent": "DepthWizard/1.0"})
        with urlopen(request, timeout=60) as response, temp.open("wb") as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                output.write(chunk)
        if md5(temp) != expected:
            raise RuntimeError(f"MD5 mismatch for {name}")
        temp.replace(dest)
        print(f"verified {name}: {dest.stat().st_size:,} bytes", flush=True)
    make_crops(raw)


def make_crops(raw: Path) -> None:
    rgb_path = raw / "M300_Quesenbank_AltumMSI_Orthomosaic_RGB.tif"
    dsm_path = raw / "M300_Quesenbank_AltumMSI_DSM.tif"
    dem_path = raw / "M300_Quesenbank_AltumMSI_DEM.tif"
    rows = []
    with rasterio.open(rgb_path) as rgb, rasterio.open(dsm_path) as dsm, rasterio.open(dem_path) as dem:
        if rgb.crs != dsm.crs:
            raise RuntimeError("RGB and DSM coordinate systems do not match")
        # The published DEM has UTM-like coordinates but no CRS/nodata tags.
        # Its transform overlaps the source DSM; assign the DSM CRS and treat
        # zeros outside the survey footprint as void during resampling.
        if dem.crs is not None and dem.crs != dsm.crs:
            raise RuntimeError("DEM coordinate system differs from DSM")
        for name, (x, y) in CROPS.items():
            window = Window(x, y, SIZE, SIZE)
            transform = dsm.window_transform(window)
            with WarpedVRT(rgb, crs=dsm.crs, transform=transform,
                           width=SIZE, height=SIZE,
                           resampling=Resampling.bilinear) as aligned_rgb:
                color = aligned_rgb.read()
            truth = dsm.read(1, window=window)
            valid = (color.max(axis=0) > 5) & np.isfinite(truth) & (truth != dsm.nodata)
            if valid.mean() < 0.95:
                raise RuntimeError(f"{name}: only {valid.mean():.1%} valid coverage")
            width_m, height_m = SIZE * abs(transform.a), SIZE * abs(transform.e)
            coarse_width = math.ceil(width_m / 30)
            coarse_height = math.ceil(height_m / 30)
            coarse_transform = from_origin(transform.c, transform.f, 30, 30)
            coarse = np.full((coarse_height, coarse_width), -9999, np.float32)
            reproject(
                source=dem.read(1), destination=coarse,
                src_transform=dem.transform, src_crs=dsm.crs,
                src_nodata=0, dst_transform=coarse_transform,
                dst_crs=dsm.crs, dst_nodata=-9999,
                resampling=Resampling.average,
            )
            if (coarse != -9999).sum() < 8:
                raise RuntimeError(f"{name}: insufficient DEM coverage")
            image_name = f"{name}_rgb.tif"
            truth_name = f"{name}_reference_dsm.tif"
            dem_name = f"{name}_dem_30m.tif"
            profile = dict(driver="GTiff", crs=dsm.crs, transform=transform,
                           height=SIZE, width=SIZE, compress="deflate")
            with rasterio.open(ROOT / image_name, "w", count=3, dtype="uint8", **profile) as out:
                out.write(color)
            with rasterio.open(ROOT / truth_name, "w", count=1, dtype="float32",
                               nodata=dsm.nodata, **profile) as out:
                out.write(truth, 1)
            with rasterio.open(ROOT / dem_name, "w", driver="GTiff", count=1,
                               dtype="float32", crs=dsm.crs, transform=coarse_transform,
                               height=coarse_height, width=coarse_width,
                               nodata=-9999, compress="deflate") as out:
                out.write(coarse, 1)
            print(f"prepared {name}: {valid.mean():.1%} valid, "
                  f"DSM {np.percentile(truth[valid], [5, 95])} m", flush=True)
            rows.append(dict(image=image_name, reference=truth_name,
                             dem=dem_name, scene="forest"))
    with (ROOT / "manifest.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=("image", "reference", "dem", "scene"))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
