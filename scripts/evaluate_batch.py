"""Batch evaluation: run the pipeline over many scenes and summarise accuracy.

CSV manifest columns: image,reference[,dem][,gcp][,scene]
    python scripts/evaluate_batch.py manifest.csv --model small -o eval_out

Writes eval_out/results.csv (one row per scene) and prints the mean RMSE /
MAE / r overall and per landscape class – the numbers for the results slide.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard.pipeline import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--model", default="small")
    ap.add_argument("-o", "--out", default="eval_out")
    ap.add_argument("--gsd", type=float, default=1.0)
    a = ap.parse_args()
    out = Path(a.out)
    rows, per_class = [], defaultdict(list)
    base = Path(a.manifest).parent
    for i, r in enumerate(csv.DictReader(open(a.manifest))):
        p = lambda k: str(base / r[k]) if r.get(k) else None
        name = f"{i + 1:03d}_{Path(r['image']).stem}"
        print(f"[{i + 1}] {name}")
        try:
            meta = run(p("image"), out / name, dem=p("dem"), gcp=p("gcp"), reference=p("reference"),
                       model=a.model, scene=r.get("scene") or "auto", assumed_gsd_m=a.gsd,
                       allow_fallback=False, log=lambda *_: None)
        except Exception as e:  # noqa: BLE001
            print("   failed:", e)
            continue
        m = meta["metrics"]
        main_m = m.get("absolute") or m["affine_aligned"]
        base_m = m.get("baseline_dem") or {}
        row = {"scene": name, "method": meta["calibration"]["method"], "units": meta["units"],
               "rmse": main_m["rmse"], "mae": main_m["mae"], "r": main_m["r"], "nmad": main_m["nmad"],
               "ndsm_rmse": m["structure_ndsm"]["rmse"], "ndsm_r": m["structure_ndsm"]["r"],
               "dem_rmse": base_m.get("rmse"), "dem_r": base_m.get("r")}
        rows.append(row)
        for cls, v in m.get("by_landscape", {}).items():
            per_class[cls].append(v)
        print("   rmse {rmse:.2f}  mae {mae:.2f}  r {r:.3f}".format(**row))
    if not rows:
        return
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    mean = lambda xs: sum(xs) / len(xs)
    summary = {"scenes": len(rows),
               "mean_rmse": mean([r["rmse"] for r in rows]), "mean_mae": mean([r["mae"] for r in rows]),
               "mean_r": mean([r["r"] for r in rows]),
               "by_landscape": {c: {"n": len(v), "rmse": mean([x["rmse"] for x in v]),
                                    "mae": mean([x["mae"] for x in v]), "r": mean([x["r"] for x in v])}
                                for c, v in per_class.items()}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
