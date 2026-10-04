"""Frozen GAMUS validation subset, raw/median/water height and resolution diagnostics."""
import argparse
import glob
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h5py
import numpy as np
from PIL import Image
from scipy import ndimage
from depthwizard.depth import relative_height
from depthwizard.analysis import water_mask
from depthwizard.validation import stratified


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--gsds", type=float, nargs="+", default=[0.33, 0.65, 1.5, 2.5, 3])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--preserve-metric-tail", action="store_true")
    a = ap.parse_args()
    files = sorted((a.root / "images" / "val").glob("*.h5"))
    rng = np.random.default_rng(2404)
    files = [files[i] for i in sorted(rng.choice(len(files), min(a.count, len(files)), replace=False))]
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for image_path in files:
        stem = image_path.stem
        key = stem.replace("_RGB", "").replace("_IMG", "")
        heights = list((a.root / "heights" / "val").glob(key + "_AGL.h5"))
        if len(heights) != 1:
            raise ValueError(f"Cannot pair {image_path}")
        with h5py.File(image_path) as f:
            rgb = np.array(f["image"])
        with h5py.File(heights[0]) as f:
            gt = np.array(f["image"], dtype=np.float32).squeeze()
        if rgb.shape[0] == 3:
            rgb = np.moveaxis(rgb, 0, -1)
        gt[gt < 0] = np.nan
        # Centre crop is fixed, and restricts diagnostic VRAM/time without touching the test split.
        hh, ww = gt.shape
        r, c = max(0, (hh - 1024) // 2), max(0, (ww - 1024) // 2)
        rgb, gt = rgb[r:r + 1024, c:c + 1024], gt[r:r + 1024, c:c + 1024]
        for gsd in a.gsds:
            size = (max(14, round(rgb.shape[1] * .33 / gsd)), max(14, round(rgb.shape[0] * .33 / gsd)))
            inp = np.array(Image.fromarray(rgb.astype(np.uint8)).resize(size, Image.Resampling.BOX))
            rel, backbone, _, info = relative_height(inp, model=a.model, device="cuda", tta=4,
                return_uncertainty=True, return_info=True, gsd=gsd, preserve_metric_tail=a.preserve_metric_tail)
            scale = info["learned_scale"](gsd)
            raw = rel * scale
            median = ndimage.median_filter(raw, 3)
            wm = water_mask(inp, gsd)
            stages = {"raw": raw, "median": median, "post_water": np.where(wm, 0, median)}
            for stage, pred in stages.items():
                pred = np.array(Image.fromarray(pred).resize((gt.shape[1], gt.shape[0]), Image.Resampling.BILINEAR))
                rows.append({"tile": key, "gsd_m": gsd, "stage": stage,
                    "upper_tail_pixels":info.get("upper_tail_pixels"),
                    "water_fraction": float(wm.mean()), **stratified(pred, gt, agl=gt, route="raw-learned-AGL")})
            (a.out / "results.json").write_text(json.dumps({"split": "development/val", "seed": 2404,
                "model": a.model, "test_split_used": False, "preserve_metric_tail":a.preserve_metric_tail,
                "results": rows}, indent=2), encoding="utf-8")
            print(key, gsd, rows[-1]["all"], flush=True)


if __name__ == "__main__":
    main()
