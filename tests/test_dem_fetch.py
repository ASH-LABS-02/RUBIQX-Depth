"""Copernicus tile discovery and offline cache behavior (no HTTP required)."""
import hashlib
import urllib.error

import pytest

from depthwizard import dem_fetch


def test_tile_names_all_hemispheres_and_boundary():
    assert dem_fetch.tile_name(28, 77).endswith("N28_00_E077_00_DEM")
    assert dem_fetch.tile_name(-1, -78).endswith("S01_00_W078_00_DEM")
    assert dem_fetch.tile_name(0, 0).endswith("N00_00_E000_00_DEM")
    names = dem_fetch.tile_names_for_bounds(76.99, 27.99, 77.01, 28.01)
    assert len(names) == 4
    assert any("N27_00_E076_00" in name for name in names)
    assert any("N28_00_E077_00" in name for name in names)


def test_404_skips_missing_ocean_tile(monkeypatch, tmp_path):
    def missing(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://example.test", 404, "missing", {}, None)
    monkeypatch.setattr(dem_fetch.urllib.request, "urlopen", missing)
    name = dem_fetch.tile_name(-80, -170)
    assert dem_fetch._cached_window(name, (-170, -80, -169.9, -79.9), tmp_path) is None


def test_cache_hit_avoids_network_and_remote_raster(monkeypatch, tmp_path):
    name = dem_fetch.tile_name(28, 77)
    bounds = (77.1, 28.1, 77.2, 28.2)
    key = hashlib.sha256((name + ":" + ",".join(f"{v:.10f}" for v in bounds)).encode()).hexdigest()[:20]
    cached = tmp_path / f"{name}-{key}.tif"
    cached.write_bytes(b"cached-window")
    monkeypatch.setattr(dem_fetch, "_head_exists", lambda *_: pytest.fail("cache hit made HTTP request"))
    import rasterio
    monkeypatch.setattr(rasterio, "open", lambda *_a, **_k: pytest.fail("cache hit opened remote COG"))
    assert dem_fetch._cached_window(name, bounds, tmp_path) == cached


def test_network_failure_is_actionable(monkeypatch, tmp_path):
    from depthwizard.io import InputImage
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    img = InputImage(np.zeros((4, 4, 3), dtype=np.uint8), tmp_path / "rgb.tif", True,
                     rasterio.CRS.from_epsg(4326), from_origin(77.1, 28.2, .001, .001), 100)
    monkeypatch.setattr(dem_fetch, "_cached_window", lambda *_: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(RuntimeError, match="Could not download Copernicus DEM \\(offline\\?\\)"):
        dem_fetch.fetch_copernicus_glo30(img, tmp_path / "output.tif", tmp_path / "cache")
