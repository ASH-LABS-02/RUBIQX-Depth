"""Score saved stages against a reference, without modifying model or outputs."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import rasterio


def score(pred, truth, mask=None):
    valid = np.isfinite(pred) & np.isfinite(truth)
    if mask is not None:
        valid &= mask
    error = (pred - truth)[valid].astype(np.float64)
    return {"n": int(error.size), "rmse": float(np.sqrt(np.mean(error**2))),
            "mae": float(np.mean(np.abs(error))), "bias": float(error.mean())} if error.size else {"n": 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stages", type=Path)
    ap.add_argument("--reference", type=Path, required=True, help="DSM or AGL raster; scoring only")
    ap.add_argument("--reference-kind", choices=["dsm", "agl"], required=True)
    ap.add_argument("--ground", type=Path, help="reference DTM for DSM height bands")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    with rasterio.open(args.reference) as src:
        truth = src.read(1, masked=True).filled(np.nan)
        grid = (src.crs, src.transform, src.shape)
    agl = truth.copy() if args.reference_kind == "agl" else None
    if args.ground:
        with rasterio.open(args.ground) as src:
            if (src.crs, src.transform, src.shape) != grid:
                raise ValueError("Reference DTM must use the exact scoring grid")
            agl = truth - src.read(1, masked=True).filled(np.nan)
    receipt = json.loads((args.stages / "manifest.json").read_text())
    rows = []
    for stage in receipt["stages"]:
        if stage["units"] != "metre" or stage["stage"] == "water_mask":
            continue
        is_agl = stage["stage"].endswith("agl")
        target = agl if is_agl else (truth if args.reference_kind == "dsm" else None)
        if target is None:
            continue
        with rasterio.open(args.stages / stage["file"]) as src:
            if (src.crs, src.transform, src.shape) != grid:
                raise ValueError("Stage and truth grids differ; explicit alignment required")
            pred = src.read(1, masked=True).filled(np.nan)
        row = {"stage": stage["stage"], "all": score(pred, target)}
        if agl is not None:
            row["height_bands_m"] = {f"{lo}-{hi}": score(pred, target, (agl >= lo) & (agl < hi))
                for lo, hi in [(0, 2.5), (2.5, 15), (15, 30), (30, float("inf"))]}
        rows.append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"reference_kind": args.reference_kind,
        "scoring_only": True, "stages": rows}, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
