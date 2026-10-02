"""Empirical comparison of Copernicus calibration modes on DC LiDAR scenes.

Runs three modes on both Glover Park and Capitol Hill East scenes using the
cached Copernicus tiles (no live network required) and collects LiDAR RMSE,
MAE, r, bias, bias-corrected RMSE, building count and per-building RMSE.

Modes
-----
A  surface + 30 m consistency (current default)
B  surface, consistency off
C  ground-from-Copernicus terrain proxy (dem_kind=terrain, no consistency)

Reference baselines (DTM-calibrated v2 runs):
  Glover Park       LiDAR RMSE 4.30 m / 145 buildings
  Capitol Hill East LiDAR RMSE 2.88 m / 102 buildings

Usage
-----
  D:\\DepthWizard\\venv\\Scripts\\python.exe scripts/compare_cop_modes.py

Outputs
-------
  out/cop_modes/comparison_table.csv   -- machine-readable
  stdout                               -- formatted table
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import tempfile
from pathlib import Path

# Ensure repo root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from depthwizard.calibrate import copernicus_terrain_from_dsm
from depthwizard.pipeline import run as pipeline_run

# ---------------------------------------------------------------------------
# Scene definitions
# ---------------------------------------------------------------------------
SCENES = {
    "glover_park": {
        "rgb": "samples/dc_lidar/glover_park/rgb.tif",
        "ref": "samples/dc_lidar/glover_park/lidar_dsm_2024.tif",
        "baseline_rmse": 4.30,
        "baseline_buildings": 145,
    },
    "capitol_hill_east": {
        "rgb": "samples/dc_lidar/capitol_hill_east/rgb.tif",
        "ref": "samples/dc_lidar/capitol_hill_east/lidar_dsm_2024.tif",
        "baseline_rmse": 2.88,
        "baseline_buildings": 102,
    },
}

MODEL = os.environ.get(
    "DEPTHWIZARD_CHECKPOINT",
    "D:/DepthWizard/checkpoints/da2-gamus-full",
)
TTA = 4
ACCEPTANCE_MARGIN = 0.5   # m — how close to DTM baseline we need to be


def debiased_rmse(rmse: float, bias: float) -> float:
    v = rmse ** 2 - bias ** 2
    return math.sqrt(max(v, 0.0))


def _derive_terrain_proxy(dem_path: str, out_path: str) -> str:
    """Write a terrain-proxy GeoTIFF derived from the Copernicus DSM."""
    import rasterio

    with rasterio.open(dem_path) as src:
        arr = src.read(1).astype(np.float32)
        res = abs(src.transform.a)
        profile = src.profile.copy()
    terrain = copernicus_terrain_from_dsm(arr, res)
    profile.update(dtype="float32", nodata=np.nan)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(terrain, 1)
        dst.update_tags(
            SOURCE="Copernicus GLO-30 terrain proxy (morphological opening)",
            VERTICAL_DATUM="EGM2008 geoid (Copernicus GLO-30)",
        )
    return out_path


def run_mode(scene_name: str, scene: dict, mode: str, work_dir: Path) -> dict:
    """Run one mode on one scene; return metrics dict."""
    rgb = scene["rgb"]
    ref = scene["ref"]
    out_dir = work_dir / f"{scene_name}_{mode}"

    print(f"  [{mode}] {scene_name} ...", flush=True)

    if mode == "A":
        meta = pipeline_run(
            rgb, str(out_dir),
            reference=ref,
            model=MODEL, tta=TTA, device=None,
            fetch_dem=True, dem_kind="surface", match_dem_30m=True,
        )
    elif mode == "B":
        meta = pipeline_run(
            rgb, str(out_dir),
            reference=ref,
            model=MODEL, tta=TTA, device=None,
            fetch_dem=True, dem_kind="surface", match_dem_30m=False,
        )
    elif mode == "C":
        # Mode C is now the pipeline default: fetch_dem=True, dem_kind="auto"
        # triggers the copernicus_terrain_from_dsm branch internally in pipeline.py.
        # The pipeline fetches the Copernicus DSM, derives the terrain proxy, fits
        # scale against the terrain proxy (dem_kind="terrain"), and still computes
        # vs_copernicus_30m against the original fetched DSM.
        meta = pipeline_run(
            rgb, str(out_dir),
            reference=ref,
            model=MODEL, tta=TTA, device=None,
            fetch_dem=True, dem_kind="auto", match_dem_30m=True,
        )
    else:
        raise ValueError(f"Unknown mode {mode!r}")

    # Extract key metrics
    abs_m = meta.get("metrics", {}).get("absolute", {})
    bld_m = meta.get("metrics", {}).get("buildings", {})
    rmse = abs_m.get("rmse", float("nan"))
    bias = abs_m.get("bias", float("nan"))
    row = {
        "mode": mode,
        "scene": scene_name,
        "rmse": round(rmse, 3),
        "mae": round(abs_m.get("mae", float("nan")), 3),
        "r": round(abs_m.get("r", float("nan")), 3),
        "bias": round(bias, 3),
        "rmse_debiased": round(debiased_rmse(rmse, bias), 3),
        "buildings": meta.get("buildings_count", 0),
        "per_bldg_rmse": round(bld_m.get("rmse", float("nan")), 3) if bld_m else float("nan"),
        "baseline_rmse": scene["baseline_rmse"],
        "baseline_buildings": scene["baseline_buildings"],
        "passes_rmse": (rmse <= scene["baseline_rmse"] + ACCEPTANCE_MARGIN
                        if not math.isnan(rmse) else False),
        "passes_buildings": meta.get("buildings_count", 0) > (
            100 if "glover" in scene_name else 80),
    }
    return row


def print_table(rows: list[dict]) -> None:
    header = (
        f"{'mode':>4} | {'scene':<18} | {'RMSE':>6} | {'MAE':>6} | {'r':>5} | "
        f"{'bias':>6} | {'RMSE_deb':>8} | {'bldgs':>5} | {'pb_RMSE':>7}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        pb = f"{r['per_bldg_rmse']:.3f}" if not math.isnan(r["per_bldg_rmse"]) else "   N/A"
        flag = " ✓" if r["passes_rmse"] and r["passes_buildings"] else "  "
        print(
            f"{r['mode']:>4} | {r['scene']:<18} | {r['rmse']:>6.3f} | {r['mae']:>6.3f} | "
            f"{r['r']:>5.3f} | {r['bias']:>6.3f} | {r['rmse_debiased']:>8.3f} | "
            f"{r['buildings']:>5} | {pb:>7}{flag}"
        )
    print(sep)
    print(f"\nReference (DTM baseline): Glover {SCENES['glover_park']['baseline_rmse']} m / "
          f"{SCENES['glover_park']['baseline_buildings']} bldgs; "
          f"Capitol {SCENES['capitol_hill_east']['baseline_rmse']} m / "
          f"{SCENES['capitol_hill_east']['baseline_buildings']} bldgs")
    print(f"Acceptance threshold: RMSE within {ACCEPTANCE_MARGIN} m of baseline AND buildings "
          f"detected (>100 Glover / >80 Capitol)")
    print(f"✓ = passes both criteria")


def main():
    repo_root = Path(__file__).resolve().parent.parent
    os.chdir(repo_root)

    work_dir = repo_root / "out" / "cop_modes"
    work_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for mode in ("A", "B", "C"):
        for scene_name, scene in SCENES.items():
            row = run_mode(scene_name, scene, mode, work_dir)
            rows.append(row)
            print(f"    done: RMSE={row['rmse']:.3f} m, buildings={row['buildings']}")

    print("\n=== MODE COMPARISON TABLE ===\n")
    print_table(rows)

    # Write CSV
    csv_path = work_dir / "comparison_table.csv"
    fieldnames = ["mode", "scene", "rmse", "mae", "r", "bias", "rmse_debiased",
                  "buildings", "per_bldg_rmse", "baseline_rmse", "baseline_buildings",
                  "passes_rmse", "passes_buildings"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\nTable written to {csv_path}")

    # Acceptance decision
    print("\n=== ACCEPTANCE DECISION ===")
    c_rows = [r for r in rows if r["mode"] == "C"]
    b_rows = [r for r in rows if r["mode"] == "B"]

    c_passes = all(r["passes_rmse"] and r["passes_buildings"] for r in c_rows)
    b_passes = all(r["passes_rmse"] and r["passes_buildings"] for r in b_rows)

    if c_passes:
        print("→ Mode C passes on both scenes. Route auto-fetch Copernicus to Mode C (terrain proxy).")
    elif b_passes:
        print("→ Mode C did not pass. Mode B passes on both scenes. Route to Mode B (surface, no consistency).")
    else:
        print("→ No mode passes the acceptance thresholds.")
        print("  Recommendation: revert fetch_dem default to False in __main__.py; document table.")

    return rows


if __name__ == "__main__":
    main()
