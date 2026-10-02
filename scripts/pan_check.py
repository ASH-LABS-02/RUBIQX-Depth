"""Does the height model still work on panchromatic (greyscale) imagery?

Cartosat-2S acquires 0.6 m imagery in a single panchromatic band (its colour
bands are 1.6 m), so SAC's test images may be greyscale. The model was trained
on colour aerial imagery. This script measures the gap - no training.

1. GAMUS test tiles, scored with no fitting, at 0.33 m and 0.6 m, three inputs:
     rgb   - original colour
     gray  - luminance copied into R, G, B
     pan   - simulated sensor: luminance as 11-bit DN with noise, then the
             app's own loader stretch (depthwizard.io._to_uint8)
2. Writes 1-band 16-bit panchromatic copies of the DC LiDAR scenes so the full
   pipeline can be checked with `python -m depthwizard <pan.tif> ...`.

  python scripts/pan_check.py --out D:/DepthWizard/evaluation/pan
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from depthwizard.depth import DepthBackbone  # noqa: E402
from depthwizard.io import _to_uint8  # noqa: E402
from evaluate_gamus_h5 import pairs, score  # noqa: E402
from resolution_sweep import area_resize  # noqa: E402

LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def to_gray(rgb):
    g = (rgb.astype(np.float32) @ LUMA).clip(0, 255).astype(np.uint8)
    return np.repeat(g[..., None], 3, axis=2)


def to_pan(rgb, rng):
    dn = (rgb.astype(np.float32) @ LUMA) * 8.0 + 40.0          # ~11-bit DN with dark offset
    dn = dn + rng.normal(0, 6.0, dn.shape)                        # sensor noise
    dn = np.clip(dn, 1, 2047).astype(np.uint16)
    g = _to_uint8(dn[..., None])[..., 0]
    return np.repeat(g[..., None], 3, axis=2)


def write_pan_geotiffs(out: Path):
    import rasterio
    for scene in ("glover_park", "capitol_hill_east"):
        src_p = ROOT / "samples" / "dc_lidar" / scene / "rgb.tif"
        with rasterio.open(src_p) as src:
            rgb = np.stack([src.read(i) for i in (1, 2, 3)], axis=-1)
            prof = src.profile.copy()
        dn = np.clip((rgb.astype(np.float32) @ LUMA) * 8.0 + 40.0, 1, 2047).astype(np.uint16)
        prof.update(count=1, dtype="uint16", nodata=0, photometric="minisblack")
        prof.pop("compress", None)
        dst = out / f"{scene}_pan.tif"
        with rasterio.open(dst, "w", **prof) as d:
            d.write(dn, 1)
        print("wrote", dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=os.environ.get("DEPTHWIZARD_GAMUS_ROOT", "D:/DepthWizard/GAMUS"))
    ap.add_argument("--model", default="D:/DepthWizard/checkpoints/da2-gamus-full")
    ap.add_argument("--gsds", type=float, nargs="+", default=[0.33, 0.6])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    write_pan_geotiffs(a.out)
    bb = DepthBackbone(a.model)
    rng = np.random.default_rng(0)
    rows = []
    for img_p, h_p in pairs(a.root):
        with h5py.File(img_p) as f:
            rgb0 = f["image"][()].astype(np.uint8)
        with h5py.File(h_p) as f:
            truth0 = f["image"][()].astype(np.float32)
        truth0[truth0 == -5.0] = np.nan
        H, W = truth0.shape
        for g in a.gsds:
            k = g / 0.33
            if k > 1.01:
                size = (round(W / k), round(H / k))
                rgb, truth = np.asarray(Image.fromarray(rgb0).resize(size, Image.BOX)), area_resize(truth0, size)
            else:
                rgb, truth = rgb0, truth0
            for mode, img in (("rgb", rgb), ("gray", to_gray(rgb)), ("pan", to_pan(rgb, rng))):
                pred = np.maximum(bb.predict_agl_metric(img, g, tta=1)[0], 0)
                m = score(pred, truth, align=False)
                if m:
                    rows.append(dict(tile=img_p.stem, gsd=g, mode=mode, **m))
        print("done", img_p.stem, flush=True)
    grp = defaultdict(list)
    for r in rows:
        grp[(r["gsd"], r["mode"])].append(r)
    summary = {}
    print(f"\n{'GSD':>5} {'input':<5} {'RMSE':>6} {'MAE':>6} {'r':>6} {'bias':>6} {'tall':>6}")
    for (g, mode), rs in sorted(grp.items()):
        s = {k: float(np.mean([x[k] for x in rs if x[k] is not None])) for k in ("rmse_m", "mae_m", "r", "bias_m")}
        s["tall"] = float(np.median([x["median_ratio_tall"] for x in rs if x["median_ratio_tall"] is not None]))
        summary[f"{g}/{mode}"] = s
        print(f"{g:>5} {mode:<5} {s['rmse_m']:>6.2f} {s['mae_m']:>6.2f} {s['r']:>6.2f} {s['bias_m']:>6.2f} {s['tall']:>6.2f}")
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
