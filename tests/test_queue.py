"""Uploads are processed one at a time, in order, with saved status."""
import io
import json
import threading
import time

import numpy as np
from PIL import Image


def _png():
    buf = io.BytesIO()
    Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def test_jobs_run_in_order_with_positions(monkeypatch, tmp_path):
    import app.server as srv
    from fastapi.testclient import TestClient
    monkeypatch.setattr(srv, "JOBS", tmp_path)
    order, gate = [], threading.Event()

    def fake_run(log, out_dir, **kw):
        order.append(out_dir)
        log("working")
        gate.wait(5)

    monkeypatch.setattr(srv, "run", fake_run)
    client = TestClient(srv.app)
    ids = [client.post("/api/process", files={"image": (f"img{i}.png", _png(), "image/png")}).json()["id"]
           for i in range(3)]
    time.sleep(0.3)
    states = [client.get(f"/api/jobs/{i}").json() for i in ids]
    assert states[0]["state"] == "running"
    assert [s.get("position") for s in states[1:]] == [1, 2]
    assert json.loads((tmp_path / ids[1] / "status.json").read_text())["state"] == "queued"
    gate.set()
    for _ in range(50):
        if all(client.get(f"/api/jobs/{i}").json()["state"] == "done" for i in ids):
            break
        time.sleep(0.1)
    assert [str(tmp_path / i) for i in ids] == order
    assert json.loads((tmp_path / ids[2] / "status.json").read_text())["state"] == "done"
