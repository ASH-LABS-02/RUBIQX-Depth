"""API smoke test: server starts, lists scenes and exposes OpenAPI docs."""
from fastapi.testclient import TestClient


def test_health_and_docs():
    from app.server import app
    c = TestClient(app)
    assert c.get("/api/health").json()["ok"] is True
    assert c.get("/openapi.json").status_code == 200
    assert isinstance(c.get("/api/scenes").json(), list)
