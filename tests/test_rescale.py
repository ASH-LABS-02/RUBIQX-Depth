import json
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
import numpy as np
import pytest

from app.server import app, JOBS

client = TestClient(app)

@pytest.fixture
def test_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.server.JOBS", tmp_path)
    return tmp_path

def test_rescale_endpoint(test_jobs):
    # Setup dc-glover-park mock scene
    scene = test_jobs / "dc-glover-park"
    scene.mkdir(parents=True)
    viewer = scene / "viewer"
    viewer.mkdir()
    
    # Create dummy files
    dsm = np.ones((10, 10), dtype=np.float32)
    dtm = np.zeros((10, 10), dtype=np.float32)
    ndsm = np.ones((10, 10), dtype=np.float32) * 5.0
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[2:5, 2:5] = 1 # building 1
    
    # We need real valid tiffs so _rewrite_tif works...
    # But for a quick test, we can use rasterio to write them.
    import rasterio
    from rasterio.transform import from_origin
    transform = from_origin(0, 0, 1, 1)
    profile = {'driver': 'GTiff', 'height': 10, 'width': 10, 'count': 1, 'dtype': 'float32', 'transform': transform}
    
    with rasterio.open(scene / "dsm.tif", "w", **profile) as dst: dst.write(dsm, 1)
    with rasterio.open(scene / "dtm.tif", "w", **profile) as dst: dst.write(dtm, 1)
    with rasterio.open(scene / "ndsm.tif", "w", **profile) as dst: dst.write(ndsm, 1)
    
    # Create dummy texture.jpg
    from PIL import Image
    Image.new('RGB', (10, 10)).save(viewer / "texture.jpg")
    
    np.save(scene / "building_labels.npy", labels)
    
    meta = {"input": "test", "calibration": {"method": "dem"}, "units": "metre"}
    (scene / "meta.json").write_text(json.dumps(meta))
    
    vmeta = {"grid_w": 5, "grid_h": 5, "gsd_m": 2.0, "ground_w_m": 10.0, "ground_h_m": 10.0, "src_w": 10, "src_h": 10}
    (viewer / "meta.json").write_text(json.dumps(vmeta))
    
    buildings = {"buildings": [{"id": 1, "height_m": 5.0, "ground_elevation_m": 0.0, "roof_elevation_m": 5.0}]}
    (viewer / "buildings.json").write_text(json.dumps(buildings))
    
    # 1. apply anchor
    resp = client.post("/api/scenes/dc-glover-park/rescale", json={"anchors": [{"building_id": 1, "height_m": 10.0}], "reset": False})
    assert resp.status_code == 200
    assert resp.json()["scale"] == 2.0
    
    bj = json.loads((viewer / "buildings.json").read_text())
    assert bj["buildings"][0]["height_m"] == 10.0
    
    # 2. apply twice
    resp2 = client.post("/api/scenes/dc-glover-park/rescale", json={"anchors": [{"building_id": 1, "height_m": 10.0}], "reset": False})
    assert resp2.status_code == 200
    assert resp2.json()["scale"] == 2.0
    
    # 3. reset
    resp_reset = client.post("/api/scenes/dc-glover-park/rescale", json={"reset": True})
    assert resp_reset.status_code == 200
    assert not (scene / "ndsm_raw.tif").exists()
    assert not (scene / "dsm_raw.tif").exists()
    
    with rasterio.open(scene / "dsm.tif") as src:
        dsm_restored = src.read(1)
    np.testing.assert_array_equal(dsm_restored, np.ones((10, 10), dtype=np.float32))
    
    bj_reset = json.loads((viewer / "buildings.json").read_text())
    assert bj_reset["buildings"][0]["height_m"] == 5.0
    
    # Test relative scene
    rel_scene = test_jobs / "gamus-nyc"
    rel_scene.mkdir()
    rel_viewer = rel_scene / "viewer"
    rel_viewer.mkdir()
    (rel_viewer / "meta.json").write_text(json.dumps(vmeta))
    
    resp_rel = client.post("/api/scenes/gamus-nyc/rescale", json={"anchors": [{"building_id": 1, "height_m": 10.0}]})
    assert resp_rel.status_code == 400
