"""Compare pretrained and fine-tuned backbones on untouched GAMUS HDF5 test tiles.

Two scoring modes (``--mode``):

* ``aligned``  - fit a per-tile scale and offset to the reference first, then
  score. This measures *shape* only; it uses the answer, so it is not an
  operational accuracy number.
* ``absolute`` - no fitting at all: the fine-tuned (AGL) checkpoint's own metric
  output (learned pixel-footprint scale, see depth.py) against the reference.
  This is the honest single-image number. Only AGL checkpoints can be scored
  this way; relative models are skipped.

GAMUS tiles are 0.33 m/pixel. Test tiles must not be used for training.

Reproduce (GAMUS from https://github.com/EarthNets/RSI-MMSegmentation, or
scripts/download_gamus_training.py for the layout <root>/images/test,
<root>/heights/test):

  python scripts/evaluate_gamus_h5.py --root $DEPTHWIZARD_GAMUS_ROOT \
      --models small models/da2-gamus-full --mode both --out out/gamus-eval
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import os

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


def score(pred: np.ndarray, truth: np.ndarray, align: bool = True):
    valid = np.isfinite(pred) & np.isfinite(truth) & (truth != -5.0)
    if valid.sum() < 100:
        return None
    p = pred[valid].astype(np.float64)
    y = truth[valid].astype(np.float64)
    if align:
        a, b = np.linalg.lstsq(np.stack((p, np.ones_like(p)), axis=1), y, rcond=None)[0]
        p = a * p + b
    err = p - y
    corr = float(np.corrcoef(p, y)[0, 1]) if y.std() > 1e-8 and p.std() > 1e-8 else None
    tall = y > 3.0     # above-ground objects (buildings, trees)
    return dict(n=int(valid.sum()), rmse_m=float(np.sqrt(np.mean(err ** 2))),
                mae_m=float(np.mean(np.abs(err))), bias_m=float(np.mean(err)),
                r=corr,
                median_ratio_tall=float(np.median(p[tall]) / np.median(y[tall])) if tall.sum() > 100 else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=os.environ.get("DEPTHWIZARD_GAMUS_ROOT"),
                    help="GAMUS root with images/test and heights/test (or set DEPTHWIZARD_GAMUS_ROOT)")
    ap.add_argument("--mode", choices=["aligned", "absolute", "both"], default="aligned")
    ap.add_argument("--gsd", type=float, default=0.33, help="GAMUS ground sampling distance (m/px)")
    ap.add_argument("--tta", type=int, default=1)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.root is None:
        raise SystemExit("give --root or set DEPTHWIZARD_GAMUS_ROOT")
    scene_pairs = list(pairs(args.root))
    if not scene_pairs:
        raise SystemExit("No GAMUS test RGB/AGL pairs found")
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    modes = ["aligned", "absolute"] if args.mode == "both" else [args.mode]
    for model_name in args.models:
        backbone = DepthBackbone(model_name)
        base_label = "pretrained-small" if model_name == "small" else Path(model_name).name
        for number, (image_path, height_path) in enumerate(scene_pairs, 1):
            with h5py.File(image_path) as file:
                rgb = file["image"][()].astype(np.uint8)
            with h5py.File(height_path) as file:
                truth = file["image"][()].astype(np.float32)
            preds = {}
            if "aligned" in modes:
                preds["aligned"] = ndimage.median_filter(backbone.predict(rgb), size=3)
            if "absolute" in modes:
                if not getattr(backbone, "agl", False):
                    if number == 1:
                        print(f"{base_label}: relative model, no absolute score", flush=True)
                else:
                    metric = backbone.predict_agl_metric(rgb, args.gsd, tta=args.tta)[0]
                    preds["absolute"] = ndimage.median_filter(np.maximum(metric, 0), size=3)
            for mode, pred in preds.items():
                metrics = score(pred, truth, align=(mode == "aligned"))
                if metrics is None:
                    print(f"{base_label} {image_path.stem}: no usable reference", flush=True)
                    continue
                label = f"{base_label}[{mode}]"
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
        "mean_bias_m": float(np.mean([row["bias_m"] for row in group])),
        "median_ratio_tall": float(np.median([row["median_ratio_tall"] for row in group
                                              if row["median_ratio_tall"] is not None]))
                             if any(row["median_ratio_tall"] is not None for row in group) else None,
    } for (model, city), group in groups.items()}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
