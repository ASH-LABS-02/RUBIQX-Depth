"""Score a frozen ensemble uncertainty raster without fitting its parameters.

For a processed scene with a reference DSM, measure how often the true error
falls within 1 and 2 sigma, including bias. Gaussian 68/95 percent coverage is
a diagnostic target, not a guaranteed interval. Bias-centred coverage is
reported separately. Use validate_benchmark.py for datum/split/hash preflight.
Never fit/select using samples/dc_lidar/*/lidar_dsm_2024.tif. The existing sigma
constants were historically fitted on these scenes, so DC coverage is diagnostic.

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
    metadata = json.loads((scene / "meta.json").read_text(encoding="utf-8"))
    if metadata.get("units") != "metre":
        raise ValueError("Metre uncertainty coverage requires a metric DSM, not a relative rDSM")
    if dsm.shape != sigma.shape:
        raise ValueError("DSM and sigma must share the same grid")
    img = match_grid(read_image(image), dsm.shape)
    ref = reference_on_grid(str(reference), img)
    err = dsm - ref
    ok = np.isfinite(err) & np.isfinite(sigma) & (sigma > 0)
    e, s = err[ok], sigma[ok]
    if not e.size:
        raise ValueError("No finite errors with positive finite sigma")
    e_centred = e - np.median(e)                       # remove the scene-wide bias
    ratio = np.abs(e) / s
    centred_ratio = np.abs(e_centred) / s
    out = {
        "scene": scene.name, "pixels": int(ok.sum()),
        "median_sigma_m": float(np.median(s)), "rmse_m": float(np.sqrt(np.mean(e ** 2))),
        "bias_m": float(np.median(e)),
        "within_1_sigma": float(np.mean(ratio <= 1)), "within_2_sigma": float(np.mean(ratio <= 2)),
        "coverage_includes_bias": True,
        "bias_centred_within_1_sigma": float(np.mean(centred_ratio <= 1)),
        "bias_centred_within_2_sigma": float(np.mean(centred_ratio <= 2)),
        "parameter_fit": False,
        "datum_status": "not verified by this legacy diagnostic; use validate_benchmark.py",
        "sigma_provenance": metadata.get("uncertainty_calibration", {}),
    }
    # does larger sigma mean larger error? (ranking skill)
    q = np.quantile(s, [.25, .5, .75])
    bins = np.searchsorted(q, s, side="right")
    out["error_by_sigma_quartile_m"] = [float(np.sqrt(np.mean(e[bins == i] ** 2)))
                                         if (bins == i).any() else None for i in range(4)]
    out["pixels_by_sigma_quartile"] = [int((bins == i).sum()) for i in range(4)]
    return out


def main(argv: list[str]) -> None:
    if len(argv) < 3 or len(argv) % 3:
        print(__doc__)
        raise SystemExit(1)
    results = [coverage(Path(argv[i]), Path(argv[i + 1]), Path(argv[i + 2])) for i in range(0, len(argv), 3)]
    for r in results:
        print(f"{r['scene']}: raw error 1σ coverage {r['within_1_sigma']:.0%}, 2σ coverage "
              f"{r['within_2_sigma']:.0%}; median σ {r['median_sigma_m']:.2f} m; "
              f"bias-centred 1σ coverage {r['bias_centred_within_1_sigma']:.0%}; "
              f"RMSE by σ quartile {[round(x, 2) if x is not None else None for x in r['error_by_sigma_quartile_m']]}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
