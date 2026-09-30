"""v3 endpoints: GCP pins, hi-res grid, normal map, heightmap, DEM visualise, input lookup."""
import hashlib
import io
import json
import shutil

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin


def _client(monkeypatch, tmp_path, scenes=("dc-glover-park",)):
    import app.server as srv
    from fastapi.testclient import TestClient
    for s in scenes:
        src = srv.ROOT / "data" / "jobs" / s
        if not (src / "viewer" / "meta.json").is_file():
            pytest.skip(f"demo scene {s} not available")
        shutil.copytree(src, tmp_path / s)
    monkeypatch.setattr(srv, "JOBS", tmp_path)
    return srv, TestClient(srv.app)


def _digest(folder):
    return {str(p.relative_to(folder)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()
            and "gcp_backup" not in p.parts and "exports" not in p.parts and p.name != "normal.png"}


def test_gcp_fit_apply_reset(monkeypatch, tmp_path):
    srv, c = _client(monkeypatch, tmp_path)
    before = _digest(tmp_path / "dc-glover-park")
    pts = [{"u": .2, "v": .3, "height_m": 60}, {"u": .7, "v": .6, "height_m": 48},
           {"u": .5, "v": .8, "height_m": 55}, {"u": .4, "v": .4, "height_m": 52}]
    fit = c.post("/api/scenes/dc-glover-park/gcp", json={"points": pts}).json()
    assert {"a", "b", "r2", "rmse_m", "loo_rmse_m"} <= fit.keys()
    a1 = c.post("/api/scenes/dc-glover-park/gcp", json={"points": pts, "apply": True}).json()
    a2 = c.post("/api/scenes/dc-glover-park/gcp", json={"points": pts, "apply": True}).json()
    assert a1["a"] == pytest.approx(a2["a"])                      # fits the original, never compounds
    vm = json.loads((tmp_path / "dc-glover-park" / "viewer" / "meta.json").read_text())
    assert vm["calibration"]["method"] == "gcp-interactive"
    assert c.post("/api/scenes/dc-glover-park/gcp", json={"reset": True}).json()["reset"] is True
    assert _digest(tmp_path / "dc-glover-park") == before       # byte-identical after undo


def test_gcp_rejects_too_few_points(monkeypatch, tmp_path):
    srv, c = _client(monkeypatch, tmp_path)
    r = c.post("/api/scenes/dc-glover-park/gcp", json={"points": [{"u": .5, "v": .5, "height_m": 3}]})
    assert r.status_code == 422


def test_hires_grid_normal_and_heightmap(monkeypatch, tmp_path):
    srv, c = _client(monkeypatch, tmp_path)
    r = c.get("/api/scenes/dc-glover-park/grid/height.bin?size=1024")
    gw, gh = int(r.headers["x-grid-w"]), int(r.headers["x-grid-h"])
    assert len(r.content) == gw * gh * 4 and max(gw, gh) <= 1024
    n = c.get("/api/scenes/dc-glover-park/normal.png")
    assert n.status_code == 200 and Image.open(io.BytesIO(n.content)).mode == "RGB"
    for bits, mode in ((16, ("I;16", "I")), (8, ("L",))):
        h = c.get(f"/api/scenes/dc-glover-park/heightmap.png?bits={bits}")
        assert h.status_code == 200 and Image.open(io.BytesIO(h.content)).mode in mode


def _write(path, arr, dtype):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype=dtype, crs="EPSG:32643", transform=from_origin(500000, 3300000, 2, 2)) as dst:
        dst.write(arr.astype(dtype), 1)


def test_dem_visualise_and_detection(tmp_path):
    from depthwizard.dem_view import is_elevation_raster
    from depthwizard.pipeline import run
    yy, xx = np.mgrid[0:120, 0:160]
    dem = (800 + 120 * np.exp(-((xx - 80) ** 2 + (yy - 60) ** 2) / 900.0)).astype(np.float32)
    _write(tmp_path / "hill.tif", dem, "float32")
    _write(tmp_path / "pan_image.tif", (dem * 3).astype(np.uint16), "uint16")     # panchromatic, not a DEM
    assert is_elevation_raster(tmp_path / "hill.tif")
    assert not is_elevation_raster(tmp_path / "pan_image.tif")
    meta = run(str(tmp_path / "hill.tif"), str(tmp_path / "out"), log=lambda *_: None)
    assert meta["calibration"]["method"] == "input-dem" and meta["units"] == "metre"
    with rasterio.open(tmp_path / "out" / "dsm.tif") as src:
        assert np.allclose(src.read(1), dem, atol=1e-3)                   # heights come straight from the file


def test_find_original_for_demo_scene(monkeypatch, tmp_path):
    srv, c = _client(monkeypatch, tmp_path)
    folder = tmp_path / "dc-glover-park"
    meta = json.loads((folder / "meta.json").read_text())
    img = srv._find_original(folder, meta, "image")
    if img is None:
        pytest.skip("sample imagery not bundled")
    assert img.name == "rgb.tif"
    ref = srv._find_original(folder, meta, "reference", near=img.parent)
    assert ref is not None and ref.name.startswith("lidar")
