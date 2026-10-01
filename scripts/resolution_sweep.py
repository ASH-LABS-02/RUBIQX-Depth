"""Resolution stress test: how does single-image height hold up from 0.33 m to 10 m?

SAC requires the method to work for 0.35-10 m imagery without overfitting to
0.6 m. For every held-out GAMUS test tile (0.33 m) and every target resolution
G, the RGB image is area-averaged to G m/px (a coarser sensor) and the LiDAR
above-ground height is averaged to the same grid; the model then predicts in
metres from the coarse image alone (no fitting) and is scored on that grid.

At 5-10 m a GAMUS tile is only 34-68 px wide and objects are sub-pixel, so
those rows show where single-image detail stops and the DEM must take over.

  python scripts/resolution_sweep.py --models D:/DepthWizard/checkpoints/da2-gamus-full \\
      --gsds 0.33 0.6 1 1.5 2.5 5 10 --out D:/DepthWizard/evaluation/res-sweep
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from depthwizard.depth import DepthBackbone  # noqa: E402
from evaluate_gamus_h5 import pairs, score  # noqa: E402

NATIVE = 0.33


def area_resize(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """NaN-aware area average to (w, h)."""
    valid = np.isfinite(a).astype(np.float32)
    filled = np.where(valid > 0, a, 0).astype(np.float32)
    num = np.asarray(Image.fromarray(filled).resize(size, Image.BOX))
    den = np.asarray(Image.fromarray(valid).resize(size, Image.BOX))
    out = num / np.maximum(den, 1e-6)
    out[den < 0.5] = np.nan
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=os.environ.get("DEPTHWIZARD_GAMUS_ROOT", "D:/DepthWizard/GAMUS"))
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--gsds", type=float, nargs="+", default=[0.33, 0.6, 1.0, 1.5, 2.5, 5.0, 10.0])
    ap.add_argument("--tta", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    tiles = list(pairs(a.root))
    if not tiles:
        raise SystemExit("no GAMUS test tiles found under --root")
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in a.models:
        bb = DepthBackbone(model)
        if not getattr(bb, "agl", False):
            print(f"{model}: not a metric (AGL) checkpoint, skipped"); continue
        label = Path(model).name
        for img_p, h_p in tiles:
            with h5py.File(img_p) as f:
                rgb = f["image"][()].astype(np.uint8)
            with h5py.File(h_p) as f:
                truth = f["image"][()].astype(np.float32)
            truth[truth == -5.0] = np.nan
            H, W = truth.shape
            for g in a.gsds:
                k = g / NATIVE
                w, h = max(8, round(W / k)), max(8, round(H / k))
                rgb_g = rgb if k <= 1.01 else np.asarray(Image.fromarray(rgb).resize((w, h), Image.BOX))
                tru_g = truth if k <= 1.01 else area_resize(truth, (w, h))
                pred = np.maximum(bb.predict_agl_metric(rgb_g, g, tta=a.tta)[0], 0)
                m = score(pred, tru_g, align=False)
                if m is None:
                    continue
                rows.append(dict(model=label, gsd_m=g, tile=img_p.stem, city=img_p.stem.split("_")[0], **m))
                print(f"{label} {g:>5} m  {img_p.stem}: RMSE {m['rmse_m']:.2f} m, r {m['r'] or float('nan'):.2f}", flush=True)
    with (a.out / "results.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=rows[0].keys()); wr.writeheader(); wr.writerows(rows)
    grp = defaultdict(list)
    for r in rows:
        grp[(r["model"], r["gsd_m"])].append(r)
    summary = {}
    print(f"\n{'model':<28}{'GSD m':>7}{'RMSE':>8}{'MAE':>8}{'r':>7}{'bias':>8}{'tall':>7}")
    for (mdl, g), rs in sorted(grp.items()):
        rr = [x["r"] for x in rs if x["r"] is not None]
        tl = [x["median_ratio_tall"] for x in rs if x["median_ratio_tall"] is not None]
        s = dict(tiles=len(rs), rmse_m=float(np.mean([x["rmse_m"] for x in rs])),
                 mae_m=float(np.mean([x["mae_m"] for x in rs])), r=float(np.mean(rr)) if rr else None,
                 bias_m=float(np.mean([x["bias_m"] for x in rs])), tall=float(np.median(tl)) if tl else None)
        summary[f"{mdl}@{g}"] = s
        print(f"{mdl:<28}{g:>7}{s['rmse_m']:>8.2f}{s['mae_m']:>8.2f}{(s['r'] or 0):>7.2f}{s['bias_m']:>8.2f}"
              f"{(s['tall'] or 0):>7.2f}")
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
