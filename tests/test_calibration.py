"""Calibration unit tests on small synthetic rasters (no model download needed)."""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from depthwizard import io as dio
from depthwizard.calibrate import calibrate, extract_structure, huber_affine


def _scene(tmp_path, n=720, gsd=0.5):
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:n, 0:n]
    terrain = 100 + 0.05 * xx
    bld = np.zeros((n, n))
    for _ in range(90):
        x, y = rng.integers(10, n - 30, 2)
        bld[y:y + 24, x:x + 24] = rng.uniform(6, 25)
    dsm = terrain + bld
    t = from_origin(500000, 1200000, gsd, gsd)
    img = tmp_path / "img.tif"
    with rasterio.open(img, "w", driver="GTiff", width=n, height=n, count=3, dtype="uint8",
                       crs="EPSG:32643", transform=t) as d:
        for i in range(3):
            d.write((np.clip(bld * 8 + 60, 0, 255)).astype(np.uint8), i + 1)
    def write(name, arr, res):
        k = int(res / gsd); m = n // k
        a = arr[:m * k, :m * k].reshape(m, k, m, k).mean((1, 3)).astype(np.float32)
        p = tmp_path / name
        with rasterio.open(p, "w", driver="GTiff", width=m, height=m, count=1, dtype="float32",
                           crs="EPSG:32643", transform=from_origin(500000, 1200000, res, res)) as d:
            d.write(a, 1)
        return p
    return img, write("dtm.tif", terrain, 30.0), write("surface.tif", dsm, 30.0), bld, terrain, dsm


def test_huber_affine_recovers_line_with_outliers():
    x = np.linspace(0, 1, 200); y = 3 * x + 2
    y[::17] += 50
    a, b = huber_affine(x, y)
    assert abs(a - 3) < 0.2 and abs(b - 2) < 0.2


def test_agl_structure_is_zero_on_ground():
    rel = np.zeros((100, 100), np.float32); rel[40:60, 40:60] = 1.0
    s, name = extract_structure(rel, 0.5, agl=True)
    assert name == "agl-lower-tail" and s[:30].max() == 0 and s[50, 50] == 1.0


def test_terrain_dem_detected_and_structure_added(tmp_path):
    img_p, dtm_p, _, bld, terrain, dsm = _scene(tmp_path)
    img = dio.read_image(img_p)
    rel = (bld / bld.max()).astype(np.float32)
    out, units, cal = calibrate(rel, img, dem_path=dtm_p, agl=True, learned_scale=float(bld.max()))
    assert units == "metre" and cal.dem_kind == "terrain"
    assert cal.dtm is not None and cal.ndsm is not None
    assert np.sqrt(np.mean((out - dsm) ** 2)) < np.sqrt(np.mean((cal.extras["dem"] - dsm) ** 2))


def test_surface_dem_fit_and_30m_consistency(tmp_path):
    img_p, _, surf_p, bld, terrain, dsm = _scene(tmp_path)
    img = dio.read_image(img_p)
    rel = (bld / bld.max()).astype(np.float32)
    out, units, cal = calibrate(rel, img, dem_path=surf_p, agl=True, reference_consistent=True)
    assert cal.dem_kind == "surface" and cal.scale_source == "surface DEM"
    assert abs(cal.scale_k - bld.max()) / bld.max() < 0.5
    assert cal.consistency_rmse_m < 2.0


def test_relative_mode_without_dem(tmp_path):
    img_p, *_ = _scene(tmp_path)
    img = dio.read_image(img_p)
    img.georeferenced = False
    rel = np.random.default_rng(1).random(img.shape).astype(np.float32)
    out, units, cal = calibrate(rel, img)
    assert units == "relative" and cal.method == "relative"
