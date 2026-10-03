import importlib.util
from pathlib import Path

import numpy as np
import pytest

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


@pytest.mark.parametrize("passes, backbone, available", [
    (1, "test-model", False), (4, "heuristic-fallback", False), (4, "test-model", True)])
def test_pipeline_requires_real_ensemble_for_reliability(monkeypatch, tmp_path, passes, backbone, available):
    import json
    from PIL import Image
    from depthwizard import pipeline

    image = tmp_path / "input.png"
    Image.fromarray(np.full((32, 32, 3), 120, np.uint8)).save(image)
    rel = np.random.default_rng(7).random((32, 32)).astype(np.float32)
    info = {"agl": False, "tta": passes, "std_rel": np.zeros_like(rel),
            "learned_scale": lambda g: None}
    monkeypatch.setattr(pipeline, "relative_height", lambda *a, **kw: (rel, backbone, None, info))
    out = tmp_path / "out"
    # A stale uncertainty file from an earlier run must not become valid evidence.
    out.mkdir()
    (out / "uncertainty.tif").write_bytes(b"stale")
    meta = pipeline.run(image, out, fetch_dem=False, tta=passes, log=lambda message: None)
    viewer = json.loads((out / "viewer" / "meta.json").read_text())
    assert meta["has_uncertainty"] is available
    assert viewer["has_confidence"] is available
    assert (out / "viewer" / "confidence.bin").exists() is available
    if not available:
        import app.server as server
        from fastapi import HTTPException
        monkeypatch.setattr(server, "_completed_scene", lambda job: out)
        with pytest.raises(HTTPException) as exc:
            server.download_product("test", "uncertainty")
        assert exc.value.status_code == 404
