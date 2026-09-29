"""Compare pretrained and fine-tuned backbones on untouched GAMUS HDF5 test tiles.

Reports per-image, reference-affine-aligned errors. The alignment uses each
tile's full AGL reference, so these are relative-shape diagnostics, not an
operational metric DSM score. Test tiles must not be used for training.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import h5py
import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard.depth import DepthBackbone  # noqa: E402


def pairs(root: Path):
    heights = {}
    for path in (root / "heights" / "test").glob("*.h5"):
        heights[re.sub(r"_agl$", "", path.stem.lower())] = path
    for image in sorted((root / "images" / "test").glob("*.h5")):
        key = re.sub(r"_(rgb|img)$", "", image.stem.lower())
        if key in heights:
            yield image, heights[key]


def score(pred: np.ndarray, truth: np.ndarray):
    valid = np.isfinite(pred) & np.isfinite(truth) & (truth != -5.0)
    if valid.sum() < 100:
        return None
    p = pred[valid].astype(np.float64)
    y = truth[valid].astype(np.float64)
    a, b = np.linalg.lstsq(np.stack((p, np.ones_like(p)), axis=1), y, rcond=None)[0]
    aligned = a * p + b
    err = aligned - y
    corr = float(np.corrcoef(aligned, y)[0, 1]) if y.std() > 1e-8 else None
    return dict(n=int(valid.sum()), rmse_m=float(np.sqrt(np.mean(err ** 2))),
                mae_m=float(np.mean(np.abs(err))),
                r=corr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    scene_pairs = list(pairs(args.root))
    if not scene_pairs:
        raise SystemExit("No GAMUS test RGB/AGL pairs found")
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for model_name in args.models:
        backbone = DepthBackbone(model_name)
        label = "pretrained-small" if model_name == "small" else Path(model_name).name
        for number, (image_path, height_path) in enumerate(scene_pairs, 1):
            with h5py.File(image_path) as file:
                rgb = file["image"][()].astype(np.uint8)
            with h5py.File(height_path) as file:
                truth = file["image"][()].astype(np.float32)
            pred = ndimage.median_filter(backbone.predict(rgb), size=3)
            metrics = score(pred, truth)
            if metrics is None:
                print(f"{label} {image_path.stem}: no usable reference", flush=True)
                continue
            row = dict(model=label, city=image_path.stem.split("_")[0],
                       tile=image_path.stem, **metrics)
            results.append(row)
            print(f"{label} {number}/{len(scene_pairs)} {row['tile']}: "
                  f"RMSE {row['rmse_m']:.2f} m, r {row['r'] if row['r'] is not None else 'n/a'}", flush=True)
    with (args.out / "results.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    groups = defaultdict(list)
    for row in results:
        groups[(row["model"], row["city"])].append(row)
        groups[(row["model"], "all")].append(row)
    summary = {f"{model}/{city}": {
        "tiles": len(group),
        "mean_rmse_m": float(np.mean([row["rmse_m"] for row in group])),
        "mean_mae_m": float(np.mean([row["mae_m"] for row in group])),
        "mean_r": float(np.mean([row["r"] for row in group if row["r"] is not None]))
                  if any(row["r"] is not None for row in group) else None,
    } for (model, city), group in groups.items()}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
