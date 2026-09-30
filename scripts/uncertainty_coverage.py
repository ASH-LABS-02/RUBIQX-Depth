"""Is the ensemble uncertainty (sigma) honest?

For a processed scene with a reference DSM, measure how often the true error
falls within 1 and 2 sigma. A well-calibrated Gaussian sigma covers ~68 % and
~95 %. Also reports the scale factor that would make 1-sigma coverage 68 %.

Usage:
  python scripts/uncertainty_coverage.py SCENE_DIR IMAGE REFERENCE [SCENE_DIR IMAGE REFERENCE ...]
  python scripts/uncertainty_coverage.py samples/dc_lidar/evaluation/001_rgb \
      samples/dc_lidar/glover_park/rgb.tif samples/dc_lidar/glover_park/lidar_dsm_2024.tif
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard.io import match_grid, read_image, read_raster  # noqa: E402
from depthwizard.metrics import reference_on_grid  # noqa: E402


def coverage(scene: Path, image: Path, reference: Path) -> dict:
    dsm_path = scene / ("dsm.tif" if (scene / "dsm.tif").exists() else "rdsm.tif")
    dsm = read_raster(dsm_path)[0]
    sigma = read_raster(scene / "uncertainty.tif")[0]
    img = match_grid(read_image(image), dsm.shape)
    ref = reference_on_grid(str(reference), img)
    err = dsm - ref
    ok = np.isfinite(err) & np.isfinite(sigma) & (sigma > 0)
    e, s = err[ok], sigma[ok]
    e_centred = e - np.median(e)                       # remove the scene-wide bias
    ratio = np.abs(e_centred) / s
    out = {
        "scene": scene.name, "pixels": int(ok.sum()),
        "median_sigma_m": float(np.median(s)), "rmse_m": float(np.sqrt(np.mean(e ** 2))),
        "bias_m": float(np.median(e)),
        "within_1_sigma": float(np.mean(ratio <= 1)), "within_2_sigma": float(np.mean(ratio <= 2)),
        "sigma_scale_for_68pct": float(np.percentile(ratio, 68.27)),
    }
    # does larger sigma mean larger error? (ranking skill)
    q = np.quantile(s, [0, .25, .5, .75, 1])
    out["error_by_sigma_quartile_m"] = [float(np.sqrt(np.mean(e_centred[(s >= a) & (s <= b)] ** 2)))
                                        for a, b in zip(q[:-1], q[1:])]
    return out


def main(argv: list[str]) -> None:
    if len(argv) < 3 or len(argv) % 3:
        print(__doc__)
        raise SystemExit(1)
    results = [coverage(Path(argv[i]), Path(argv[i + 1]), Path(argv[i + 2])) for i in range(0, len(argv), 3)]
    for r in results:
        print(f"{r['scene']}: 1σ covers {r['within_1_sigma']:.0%} (ideal 68 %), 2σ covers "
              f"{r['within_2_sigma']:.0%} (ideal 95 %); median σ {r['median_sigma_m']:.2f} m; "
              f"σ would need ×{r['sigma_scale_for_68pct']:.1f} for 68 %; "
              f"RMSE by σ quartile {[round(x, 2) for x in r['error_by_sigma_quartile_m']]}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
