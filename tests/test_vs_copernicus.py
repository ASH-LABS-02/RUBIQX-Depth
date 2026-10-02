"""Native DEM cell scores retain signed bias and ignore invalid data."""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from depthwizard.io import InputImage
from depthwizard.metrics import vs_copernicus_30m


def test_area_mean_rmse_bias_and_nan(tmp_path):
    base = np.array([[10, 20], [30, 40]], dtype=np.float32)
    dem = tmp_path / "copernicus.tif"
    transform = from_origin(500000, 3000000, 30, 30)
    with rasterio.open(dem, "w", driver="GTiff", width=2, height=2,
                       count=1, dtype="float32", crs="EPSG:32643", transform=transform,
                       nodata=-9999) as dst:
        dst.write(base, 1)
    img = InputImage(np.zeros((60, 60, 3), dtype=np.uint8), tmp_path / "rgb.tif", True,
                     rasterio.CRS.from_epsg(32643), from_origin(500000, 3000000, 1, 1), 1)
    full = np.repeat(np.repeat(base, 30, axis=0), 30, axis=1) + 2
    result = vs_copernicus_30m(full, img, dem)
    assert result["n"] == 4
    assert result["rmse"] == result["mae"] == result["bias"] == 2
    assert abs(result["r"] - 1) < 1e-8
    full[:30, :30] = np.nan
    result = vs_copernicus_30m(full, img, dem)
    assert result["n"] == 3 and result["bias"] == 2
