"""Fetch a small DC urban holdout from public DCGIS image services.

The 2023 optical image and 2024 LiDAR DSM are different acquisitions/sensors.
Only the 2018 bare-earth DTM is used for scale calibration. The 2024 DSM is
held aside until evaluation. All downloaded rasters are cropped to fixed
coordinates in NAD83 / Maryland (EPSG:26985) for reproducibility.

    python scripts/prepare_dc_lidar_sample.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "dc_lidar"
BASE = "https://imagery.dcgis.dc.gov/dcgis/rest/services"
SERVICES = {
    "rgb": f"{BASE}/Ortho/Ortho_2023/ImageServer",
    "reference": f"{BASE}/Lidar/DSM_2024/ImageServer",
    "calibration_terrain": f"{BASE}/Lidar/Hydro_Enforced_DTM_2018/ImageServer",
}
# West of Wisconsin Avenue in northwest DC; away from federal redaction zones.
SCENES = {
    "glover_park": (394200, 138100, 394712, 138612),
    "capitol_hill_east": (400000, 135500, 400512, 136012),
}
CRS = "EPSG:26985"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_url(service: str, box: tuple[int, ...], size: int):
    params = {
        "f": "image", "bbox": ",".join(map(str, box)), "bboxSR": "26985",
        "imageSR": "26985", "size": f"{size},{size}", "format": "tiff",
        "interpolation": "RSP_BilinearInterpolation",
    }
    return service + "/exportImage?" + urllib.parse.urlencode(params)


def export_image(service: str, box: tuple[int, ...], size: int, dest: Path):
    url = export_url(service, box, size)
    req = urllib.request.Request(url, headers={"User-Agent": "DepthWizard research sample/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        body = response.read()
    if body[:2] not in (b"II", b"MM"):
        raise RuntimeError(f"ImageServer did not return a TIFF: {body[:300]!r}")
    dest.write_bytes(body)
    return url


def georeference(exported: Path, final: Path, box, *, bands: int):
    with rasterio.open(exported) as src:
        arr = src.read()[:bands]
        if bands == 1:
            arr = arr.astype("float32")
            good = np.isfinite(arr) & (arr > -1000) & (arr < 10000)
            if good.mean() < 0.95:
                raise RuntimeError(f"Unusable elevation coverage {good.mean():.1%}: {exported}")
            arr[~good] = -9999.0
        profile = dict(driver="GTiff", height=src.height, width=src.width,
                       count=bands, dtype=str(arr.dtype), crs=CRS,
                       transform=from_bounds(*box, src.width, src.height),
                       compress="deflate", tiled=True)
        if bands == 1:
            profile.update(nodata=-9999.0, predictor=3)
        else:
            profile.update(photometric="RGB")
        with rasterio.open(final, "w", **profile) as dst:
            dst.write(arr)
    exported.unlink()


def coarse_dtm(src_path: Path, dest: Path, box):
    # 512 m AOI / 16 cells = 32 m terrain cells. This is a genuinely coarse
    # bare-earth anchor. The 2024 DSM reference is never read here.
    width = height = 16
    with rasterio.open(src_path) as src:
        arr = np.full((height, width), -9999.0, "float32")
        transform = from_bounds(*box, width, height)
        reproject(source=rasterio.band(src, 1), destination=arr,
                  src_transform=src.transform, src_crs=src.crs,
                  src_nodata=src.nodata, dst_transform=transform,
                  dst_crs=CRS, dst_nodata=-9999.0, resampling=Resampling.average)
        if np.count_nonzero(arr != -9999.0) < arr.size * 0.95:
            raise RuntimeError("The 2018 terrain crop has inadequate coverage")
        with rasterio.open(dest, "w", driver="GTiff", height=height, width=width,
                           count=1, dtype="float32", crs=CRS, transform=transform,
                           nodata=-9999.0, compress="deflate") as dst:
            dst.write(arr, 1)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    provenance = {"services": SERVICES, "crs": CRS, "scenes": {}}
    for name, box in SCENES.items():
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        print(f"Fetching {name}", flush=True)
        urls = {}
        for key, size, bands, filename in (
            ("rgb", 1024, 3, "rgb.tif"),
            ("reference", 512, 1, "lidar_dsm_2024.tif"),
            ("calibration_terrain", 512, 1, "dtm_2018_1m.tif"),
        ):
            raw = folder / f"{key}_export.tif"
            final = folder / filename
            urls[key] = export_url(SERVICES[key], box, size)
            if not final.exists():
                export_image(SERVICES[key], box, size, raw)
                georeference(raw, final, box, bands=bands)
        coarse_dtm(folder / "dtm_2018_1m.tif", folder / "dtm_2018_32m.tif", box)
        files = {p.name: {"size_bytes": p.stat().st_size, "sha256": sha256(p)}
                 for p in sorted(folder.glob("*.tif"))}
        provenance["scenes"][name] = {"bbox_epsg26985": box, "export_urls": urls,
                                      "files": files}
        manifest.append({"image": f"{name}/rgb.tif",
                         "dem": f"{name}/dtm_2018_32m.tif",
                         "reference": f"{name}/lidar_dsm_2024.tif", "scene": "urban"})
    import csv
    with (OUT / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader()
        w.writerows(manifest)
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2))
    print(f"Ready: {OUT / 'manifest.csv'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        raise
