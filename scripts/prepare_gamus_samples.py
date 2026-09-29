"""Download two held-out GAMUS RGB/AGL pairs and convert them for DepthWizard.

Usage: python scripts/prepare_gamus_samples.py
Requires h5py, numpy, pillow, and rasterio.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from urllib.request import urlopen

import h5py
import numpy as np
import rasterio
from PIL import Image


ROOT = Path(__file__).resolve().parents[1] / "samples" / "gamus"
SOURCE = "https://huggingface.co/datasets/earthflow/GAMUS/resolve/main/"
FILES = {
    "images/test/DC_03_26_RGB.h5": "90644e40ba507fee7fe3a21a53c28a3eb36c94cb6e233f4dc80d3e1f97060934",
    "heights/test/DC_03_26_AGL.h5": "5594d976c568c53827b29c2fb096dad68935c64a76053a052a57313fc5836f1b",
    "images/test/NYC_00735_IMG.h5": "4eca31b7e69ff0e6856dd889ce79afdeefce29774c6c94823fbc40ac74a7539d",
    "heights/test/NYC_00735_AGL.h5": "50bbe5ac5f48f36e169b19f0317df9b59b65db0982286470a41856262dc9dca8",
}
PAIRS = (
    ("DC_03_26", "DC_03_26_RGB.h5", "DC_03_26_AGL.h5"),
    ("NYC_00735", "NYC_00735_IMG.h5", "NYC_00735_AGL.h5"),
)


def download() -> None:
    raw = ROOT / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for remote, expected in FILES.items():
        target = raw / Path(remote).name
        if not target.exists():
            part = target.with_suffix(".part")
            with urlopen(SOURCE + remote + "?download=true", timeout=120) as response, part.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            part.replace(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"Unexpected GAMUS content in {target}: {digest}")
        print(f"Verified {target.name}")


def convert() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, rgb_name, height_name in PAIRS:
        with h5py.File(ROOT / "raw" / rgb_name) as file:
            rgb = file["image"][()]
        with h5py.File(ROOT / "raw" / height_name) as file:
            height = file["image"][()].astype(np.float32)
        if rgb.shape != (1024, 1024, 3) or rgb.dtype != np.uint8 or height.shape != rgb.shape[:2]:
            raise ValueError(f"Unexpected array shape or type for {name}")

        image_file = f"{name}_rgb.png"
        reference_file = f"{name}_agl.tif"
        Image.fromarray(rgb, "RGB").save(ROOT / image_file)

        # Exact -5 values in the DC tile are void pixels. Preserve all other
        # values, including small negative measurement noise.
        height[height == -5.0] = -9999.0
        with rasterio.open(ROOT / reference_file, "w", driver="GTiff",
                           width=height.shape[1], height=height.shape[0], count=1,
                           dtype="float32", nodata=-9999.0, compress="deflate",
                           predictor=3, tiled=True) as dst:
            dst.write(height, 1)
            dst.update_tags(SOURCE="GAMUS test split", ELEVATION_TYPE="AGL/nDSM",
                            UNITS="metres")
        rows.append({"image": image_file, "reference": reference_file,
                     "dem": "", "scene": "urban"})
        print(f"Prepared {image_file} + {reference_file}")

    with (ROOT / "manifest.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("image", "reference", "dem", "scene"))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    download()
    convert()
