"""Disaster screening tools and non-blocking scene endpoints."""
import threading
import time

import numpy as np
import pytest

from depthwizard.disaster import connected_flood_mask, evacuation_route, population_exposure


def _valley(n=80):
    # a valley running north-south: low centre column, high sides
    x = np.abs(np.arange(n) - n // 2).astype(np.float32)
    return np.tile(x, (n, 1)) * 0.5 + 10.0


def test_connected_flood_respects_ridges():
    z = _valley()
    z[18:32, 8:16] = 30.0                # a ring of high ground ...
    z[20:30, 10:14] = 5.0                # ... around an interior pocket below the water level
    mask = connected_flood_mask(z, 12.0)
    assert mask[:, z.shape[1] // 2].all()          # the valley floor floods from the edges
    assert not mask[20:30, 10:14].any()            # the enclosed pocket stays dry


def test_evacuation_route_reaches_high_ground():
    z = _valley()
    level = 12.0
    flood = connected_flood_mask(z, level)
    dry_low = np.argwhere(~flood & (z < level + 1.0))
    start = tuple(int(v) for v in dry_low[len(dry_low) // 2])
    out = evacuation_route(z, start, level, 1.0, clearance_m=2.0)
    assert out["status"] == "route_found"
    assert out["destination_elevation_m"] >= level + 2.0
    assert out["length_m"] > 0


def test_evacuation_from_flooded_cell_is_blocked():
    z = _valley()
    out = evacuation_route(z, (40, 40), 12.0, 1.0)
    assert out["status"] == "blocked_start"


def test_population_uses_shared_default_density():
    b = [{"id": 1, "ground_elevation_m": 10.0, "area_m2": 300.0, "storeys": 2},
         {"id": 2, "ground_elevation_m": 20.0, "area_m2": 300.0, "storeys": 2}]
    out = population_exposure(b, 15.0)
    assert out["assumptions"]["floor_area_per_person_m2"] == 30.0
    assert out["affected_buildings"] == 1
    assert out["estimated_people_at_risk"] == 20        # 300 m2 x 2 floors / 30 m2
    assert population_exposure(b, 15.0, units="relative")["status"] == "unavailable"


def test_scene_file_endpoints_do_not_wait_for_gpu(monkeypatch, tmp_path):
    """Rescale / mission must not queue behind a running model inference."""
    import shutil
    import app.server as srv
    from fastapi.testclient import TestClient
    demo = srv.ROOT / "data" / "jobs" / "dc-glover-park"
    if not (demo / "building_labels.npy").is_file():
        pytest.skip("demo scene not available")
    shutil.copytree(demo, tmp_path / "g")
    monkeypatch.setattr(srv, "JOBS", tmp_path)
    client = TestClient(srv.app)
    srv._lock.acquire()
    try:
        res = {}
        t = threading.Thread(target=lambda: res.setdefault(
            "r", client.post("/api/scenes/g/mission", json={"action": "shelters", "water_level_m": 45.0})),
            daemon=True)
        t0 = time.time(); t.start(); t.join(5)
        assert "r" in res, "mission endpoint blocked on the GPU lock"
        assert res["r"].status_code == 200
        assert time.time() - t0 < 5
    finally:
        srv._lock.release()
