import importlib.util
from pathlib import Path

import numpy as np

from depthwizard.uncertainty import SIGMA0_M, SPREAD_GAIN, calibrated_sigma


def test_calibrated_sigma_floor_and_growth():
    s = calibrated_sigma(np.array([0.0, 1.0, 2.0], np.float32))
    assert np.isclose(s[0], SIGMA0_M)                      # floor when the ensemble agrees
    assert np.isclose(s[1], np.hypot(SIGMA0_M, SPREAD_GAIN))
    assert s[0] < s[1] < s[2]                              # keeps the spread's ranking
    assert s.dtype == np.float32


def _load_eval_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_gamus_h5.py"
    spec = importlib.util.spec_from_file_location("evaluate_gamus_h5", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gamus_absolute_scoring_does_not_fit_to_the_reference():
    ev = _load_eval_script()
    rng = np.random.default_rng(0)
    truth = rng.uniform(0, 20, (64, 64)).astype(np.float32)
    pred = 0.5 * truth                                     # right shape, half the height
    aligned = ev.score(pred, truth, align=True)
    absolute = ev.score(pred, truth, align=False)
    assert aligned["rmse_m"] < 1e-3                         # alignment hides the scale error
    assert absolute["rmse_m"] > 3.0                         # absolute mode exposes it
    assert np.isclose(absolute["median_ratio_tall"], 0.5, atol=0.02)
