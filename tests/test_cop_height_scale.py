"""Copernicus structure scale uses native cells, not validation references."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from depthwizard.calibrate import copernicus_height_scale
from depthwizard.io import InputImage


def _scene(tmp_path, surface=13):
    ground = np.full((12, 12), 10, np.float32)
    image = InputImage(np.zeros((12, 12, 3), np.uint8), tmp_path / "rgb.tif", True,
                       rasterio.CRS.from_epsg(32643), from_origin(500000, 3000000, 15, 15), 15)
    dem = tmp_path / "cop.tif"
    with rasterio.open(dem, "w", driver="GTiff", width=6, height=6, count=1,
                       dtype="float32", crs=image.crs,
                       transform=from_origin(500000, 3000000, 30, 30), nodata=np.nan) as dst:
        dst.write(np.full((6, 6), surface, np.float32), 1)
    return image, ground, dem


def test_native_mean_ratio_and_common_nan_mask(tmp_path):
    image, ground, dem = _scene(tmp_path)
    ndsm = np.tile(np.array([[1, 3], [3, 1]], np.float32), (6, 6))
    assert copernicus_height_scale(ndsm, ground, image, dem) == (1.5, 36)
    ndsm[:2, :2] = np.nan
    s, n = copernicus_height_scale(ndsm, ground, image, dem)
    assert s == 1.5 and n == 35
    ground[2:4, :2] = np.nan
    s, n = copernicus_height_scale(ndsm, ground, image, dem)
    assert s == 1.5 and n == 34


@pytest.mark.parametrize("estimate, expected", [(1, 1.6), (10, 0.8)])
def test_scale_clamps(tmp_path, estimate, expected):
    image, ground, dem = _scene(tmp_path)
    assert copernicus_height_scale(np.full(ground.shape, estimate, np.float32),
                                   ground, image, dem) == (expected, 36)


def test_cell_count_and_two_metre_threshold(tmp_path):
    image, ground, dem = _scene(tmp_path)
    ndsm = np.full(ground.shape, 2, np.float32)
    ndsm[:2, :] = np.nan  # 30 cells still sufficient
    assert copernicus_height_scale(ndsm, ground, image, dem) == (1.5, 30)
    ndsm[2:4, :2] = np.nan
    assert copernicus_height_scale(ndsm, ground, image, dem) == (None, 29)
    with rasterio.open(dem, "r+") as dst:
        dst.write(np.full((6, 6), 12, np.float32), 1)  # cop_nd == 2 excluded
    assert copernicus_height_scale(np.full(ground.shape, 2, np.float32),
                                   ground, image, dem) == (None, 0)
