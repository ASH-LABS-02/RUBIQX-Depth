"""Explicit height type prevents silent ellipsoid/geoid mixing."""
import csv

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from depthwizard import calibrate
from depthwizard.io import InputImage, write_dsm


def _image(tmp_path):
    return InputImage(np.zeros((4, 4, 3), dtype=np.uint8), tmp_path / "rgb.tif", True,
                      rasterio.CRS.from_epsg(4326), from_origin(77, 28.01, .001, .001), 100)


def test_ellipsoidal_gcp_conversion_only_when_selected(tmp_path, monkeypatch):
    path = tmp_path / "points.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["x", "y", "z"])
        writer.writeheader(); writer.writerow({"x": 1, "y": 1, "z": 100})
    image = _image(tmp_path)
    monkeypatch.setenv("PROJ_NETWORK", "OFF")
    import pyproj
    class Transformer:
        def transform(self, lon, lat, height, **_kwargs):
            return lon, lat, [h - 50 for h in height]
    calls = []
    def fake_from_crs(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["allow_ballpark"] is False and kwargs["only_best"] is True
        return Transformer()
    monkeypatch.setattr(pyproj.Transformer, "from_crs", fake_from_crs)
    assert calibrate.load_gcps(path, image)[2].tolist() == [100]
    assert not calls
    assert calibrate.load_gcps(path, image, gcp_height_type="ellipsoidal")[2].tolist() == [50]
    assert len(calls) == 1


def test_missing_geoid_grid_refuses_instead_of_mixing(tmp_path, monkeypatch):
    path = tmp_path / "points.csv"
    path.write_text("x,y,z\n1,1,100\n")
    import pyproj
    monkeypatch.setenv("PROJ_NETWORK", "OFF")
    monkeypatch.setattr(pyproj.Transformer, "from_crs", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("grid missing")))
    with pytest.raises(RuntimeError, match="us_nga_egm08_25.tif"):
        calibrate.load_gcps(path, _image(tmp_path), gcp_height_type="ellipsoidal")


def test_output_has_vertical_tag_and_compound_crs(tmp_path):
    path = tmp_path / "dsm.tif"
    write_dsm(path, np.ones((4, 4), dtype=np.float32), _image(tmp_path),
              units="metre", description="test", vertical_datum=calibrate.DATUMS["COP30"])
    with rasterio.open(path) as src:
        assert src.tags()["VERTICAL_DATUM"] == calibrate.DATUMS["COP30"]
        assert "3855" in src.crs.to_wkt()
