"""Compare a fixed 50:50 two-resolution blend on held-out GAMUS, without fitting."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard.depth import get_backbone
from evaluate_gamus_h5 import pairs, score


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--tta', type=int, default=4)
    args = ap.parse_args()
    samples = list(pairs(args.root))
    if not samples:
        raise SystemExit('No held-out RGB/AGL pairs found')
    args.out.mkdir(parents=True, exist_ok=True)
    bb = get_backbone(args.model)
    if not bb.agl:
        raise SystemExit('This comparison requires a metric AGL checkpoint')
    rows = []
    for image_path, height_path in samples:
        with h5py.File(image_path) as f:
            rgb = f['image'][()].astype(np.uint8)
        with h5py.File(height_path) as f:
            truth = f['image'][()].astype(np.float32)
        h, w = rgb.shape[:2]
        single = np.maximum(bb.predict_agl_metric(rgb, 0.33, tta=args.tta)[0], 0)
        size = (max(1, w // 2), max(1, h // 2))
        half = np.asarray(Image.fromarray(rgb).resize(size, Image.Resampling.BOX))
        coarse = bb.predict_agl_metric(half, 0.33 * w / size[0], tta=args.tta)[0]
        coarse = np.asarray(Image.fromarray(coarse).resize((w, h), Image.Resampling.BILINEAR))
        blend = np.maximum((single + coarse) * 0.5, 0)
        for method, pred in [('single', single), ('two_resolution', blend)]:
            metrics = score(pred, truth, align=False)
            valid_tall = np.isfinite(truth) & (truth >= 15) & np.isfinite(pred)
            metrics['tall_rmse_m'] = float(np.sqrt(np.mean((pred[valid_tall] - truth[valid_tall]) ** 2))) if valid_tall.any() else None
            rows.append(dict(tile=image_path.stem, method=method, **metrics))
            print(f'{image_path.stem} {method}: RMSE {metrics["rmse_m"]:.3f} m', flush=True)
    with (args.out / 'results.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    summary = {}
    for method in ['single', 'two_resolution']:
        subset = [r for r in rows if r['method'] == method]
        summary[method] = {key: float(np.mean([r[key] for r in subset if r[key] is not None]))
                           for key in ['rmse_m', 'mae_m', 'bias_m', 'tall_rmse_m']}
        summary[method]['tiles'] = len(subset)
    summary['protocol'] = 'Fixed 50:50 blend; 0.33 and 0.66 m RGB; TTA ' + str(args.tta) + '; no reference fitting; means over held-out tiles.'
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
