"""Does a post-hoc height correction help the fine-tuned model?

Fits three simple corrections of the metric AGL output on GAMUS *validation*
tiles only - a single gain, a gain above a height threshold, and a power
curve - and scores each on the untouched *test* tiles (no per-tile fitting).
Result (40 val / 30 test tiles, Oct 2026): best test gain 3.46 -> 3.44 m RMSE,
i.e. no meaningful improvement, so no correction is applied in the pipeline.

  python scripts/check_height_correction.py --root $DEPTHWIZARD_GAMUS_ROOT \
      --checkpoint models/da2-gamus-full --val-tiles 40
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import h5py
import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard.depth import DepthBackbone  # noqa: E402


def load(bb, root: Path, split: str, limit: int | None, gsd: float):
    heights = {re.sub(r"_agl$", "", p.stem.lower()): p for p in (root / "heights" / split).glob("*.h5")}
    images = [p for p in sorted((root / "images" / split).glob("*.h5"))
              if re.sub(r"_(rgb|img)$", "", p.stem.lower()) in heights]
    if limit and len(images) > limit:          # evenly spaced, deterministic
        images = images[:: max(1, len(images) // limit)][:limit]
    out = []
    for im in images:
        key = re.sub(r"_(rgb|img)$", "", im.stem.lower())
        with h5py.File(im) as f:
            rgb = f["image"][()].astype(np.uint8)
        with h5py.File(heights[key]) as f:
            y = f["image"][()].astype(np.float32)
        p = ndimage.median_filter(np.maximum(bb.predict_agl_metric(rgb, gsd)[0], 0), size=3)
        ok = np.isfinite(p) & np.isfinite(y) & (y != -5.0)
        out.append((key, p[ok][::4], y[ok][::4]))
        print(split, key, flush=True)
    return out


def score(data, f):
    rmse = [np.sqrt(np.mean((f(p) - y) ** 2)) for _, p, y in data]
    return float(np.mean(rmse))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--val-tiles", type=int, default=40)
    ap.add_argument("--gsd", type=float, default=0.33)
    a = ap.parse_args()
    bb = DepthBackbone(a.checkpoint)
    val, test = load(bb, a.root, "val", a.val_tiles, a.gsd), load(bb, a.root, "test", None, a.gsd)
    cands = {"none": lambda p: p}
    g = min(np.arange(0.9, 1.8, 0.02), key=lambda g: score(val, lambda p: g * p))
    cands[f"gain {g:.2f}"] = lambda p, g=g: g * p
    gh, t = min(((g, t) for g in np.arange(0, 1.5, 0.05) for t in (1, 2, 3, 4, 6)),
                key=lambda c: score(val, lambda p: p + c[0] * np.maximum(p - c[1], 0)))
    cands[f"hinge {gh:.2f} above {t} m"] = lambda p, g=gh, t=t: p + g * np.maximum(p - t, 0)
    pa, pk = min(((a_, k) for a_ in np.arange(0.7, 1.5, 0.05) for k in np.arange(0.9, 1.4, 0.05)),
                 key=lambda c: score(val, lambda p: c[0] * np.power(p, c[1])))
    cands[f"power {pa:.2f}*h^{pk:.2f}"] = lambda p, a_=pa, k=pk: a_ * np.power(p, k)
    for name, f in cands.items():
        print(f"{name:26s} val RMSE {score(val, f):.3f} m   test RMSE {score(test, f):.3f} m")


if __name__ == "__main__":
    main()
