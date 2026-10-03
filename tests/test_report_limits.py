"""Reports must carry the same measurement limits as the viewer."""
import json

import pytest

from depthwizard.report import generate_html_report


@pytest.mark.parametrize("units", ["metre", "relative"])
def test_report_explains_uncertainty_and_screening_limits(tmp_path, units):
    (tmp_path / "meta.json").write_text(json.dumps({
        "units": units, "tta": 4, "backbone": "test-model",
        "calibration": {"method": "relative"}, "input": "example.png"}))
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    (viewer / "buildings.json").write_text(json.dumps({
        "total_footprint_m2": 123456, "buildings": []}))
    html = generate_html_report(tmp_path)
    assert "not accuracy probabilities" in html
    assert "routes and shelters require field checks" in html
    assert "full raster grid" in html
    if units == "relative":
        assert "123456.0 m²" not in html
        assert "area uncalibrated" in html


def test_report_flags_heuristic_prototype_and_no_error_estimate(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({
        "units": "metre", "tta": 0, "backbone": "heuristic-fallback"}))
    html = generate_html_report(tmp_path)
    assert "Prototype only" in html
    assert "No validated error estimate available" in html
