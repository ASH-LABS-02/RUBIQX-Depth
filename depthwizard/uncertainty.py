"""Turn the rotation-ensemble spread into a calibrated 1-sigma error (metres).

The raw spread between the four rotated predictions ranks reliability well
(pixels with larger spread have larger error) but is far too small to be read
as an error bar: on the held-out DC LiDAR scenes 1-sigma covered only 5-11 % of
errors instead of 68 %. The true error also has a floor that the ensemble cannot
see (calibration and DEM error shared by every pass).

We model the total 1-sigma error as

    sigma = sqrt(SIGMA0_M**2 + (SPREAD_GAIN * spread)**2)

fitted on the two DC LiDAR evaluation scenes (Glover Park, Capitol Hill East;
2018 DTM calibration, 2024 LiDAR reference, dem+learned-scale path).
Leave-one-scene-out check: fitted on one scene, 1-sigma covers 52 % and 88 % of
errors on the other (raw spread: 5 % and 9 %). Two scenes is a small sample;
treat these values as provisional and re-fit with scripts/uncertainty_coverage.py
when more reference scenes are available. The fit is for metric scenes on the
DEM + learned-scale route. Coverage on other calibration routes and landscapes
has not been established; it must not be treated as guaranteed there.
"""
from __future__ import annotations

import numpy as np

SIGMA0_M = 4.0
SPREAD_GAIN = 5.5
MODEL = f"sqrt({SIGMA0_M}^2 + ({SPREAD_GAIN} * ensemble_spread)^2)"
PROVENANCE = {
    "model": MODEL,
    "fitted_on": "2 DC LiDAR scenes (Glover Park, Capitol Hill East), DEM + learned-scale route",
    "held_out_1sigma_coverage": "52-88 % (ideal 68 %); raw ensemble spread 5-9 %",
    "status": "provisional - small calibration set",
    "calibration_scene_count": 2,
    "scope": "DC urban scenes on the DEM + learned-scale route; other domains unvalidated",
    "independent_validation": False,
}


def calibrated_sigma(spread_m: np.ndarray) -> np.ndarray:
    """Calibrated 1-sigma error in metres from the ensemble spread in metres."""
    s = np.asarray(spread_m, dtype=np.float32)
    return np.sqrt(SIGMA0_M ** 2 + (SPREAD_GAIN * s) ** 2).astype(np.float32)
