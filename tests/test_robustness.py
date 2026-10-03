"""Portable checkpoint resolution and bounded, validated uploads."""
import io

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient


def test_missing_configured_checkpoint_is_explicit(monkeypatch, tmp_path, capsys):
    from depthwizard import __main__ as cli
    import app.server as server

    monkeypatch.setenv("DEPTHWIZARD_CHECKPOINT", str(tmp_path / "missing"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["image.png"])
    assert exc.value.code == 2
    assert cli.CHECKPOINT_ERROR in capsys.readouterr().err
    info = server.local_model()
    assert info["ready"] is False and info["error"] == server.CHECKPOINT_ERROR


def test_bundled_model_without_training_drive(monkeypatch, tmp_path):
    from depthwizard import __main__ as cli
    import app.server as server

    model = tmp_path / "models" / "da2-gamus-full"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"test-placeholder")
    monkeypatch.delenv("DEPTHWIZARD_CHECKPOINT", raising=False)
    for module in (cli, server):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "TRAINING_ROOT", tmp_path / "models")
    calls = []
    monkeypatch.setattr(cli, "run", lambda *a, **kw: calls.append(kw) or {})
    cli.main(["image.png"])
    assert calls[0]["model"] == str(model)
    assert server.local_model()["path"] == str(model)


@pytest.mark.parametrize("role", ["image", "dem", "reference"])
def test_invalid_upload_type_rejected_before_queue(monkeypatch, tmp_path, role):
    import app.server as server

    monkeypatch.setattr(server, "JOBS", tmp_path)
    files = {"image": ("image.png", b"test", "image/png")}
    files[role] = ("bad.exe", b"test", "application/octet-stream")
    result = TestClient(server.app).post("/api/process", files=files)
    assert result.status_code == 400
    assert "Unsupported" in result.json()["detail"]
    assert not list(tmp_path.iterdir())


def test_oversized_upload_rejected_before_queue(monkeypatch, tmp_path):
    import app.server as server

    monkeypatch.setattr(server, "JOBS", tmp_path)
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 16)
    result = TestClient(server.app).post("/api/process", files={"image": ("image.png", b"x" * 17)})
    assert result.status_code == 400
    assert "500 MB" in result.json()["detail"]
    assert not list(tmp_path.iterdir())


def test_unknown_size_stream_is_bounded_and_partial_file_removed(monkeypatch, tmp_path):
    import app.server as server

    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 16)
    upload = UploadFile(io.BytesIO(b"x" * 17), filename="image.PNG")
    with pytest.raises(HTTPException) as exc:
        server._save(upload, tmp_path, "image")
    assert exc.value.status_code == 400
    assert not (tmp_path / "image" / "image.PNG").exists()


def test_all_supported_image_extensions_and_gcp_csv(tmp_path):
    import app.server as server

    for suffix in server.IMAGE_EXTENSIONS:
        path = server._save(UploadFile(io.BytesIO(b"test"), filename="image" + suffix.upper()),
                            tmp_path, "image")
        assert path and (tmp_path / "image" / ("image" + suffix.upper())).read_bytes() == b"test"
    server._validate_upload(UploadFile(io.BytesIO(b"x,y,z\n"), filename="points.csv"), "gcp")


def test_missing_local_checkpoint_is_400(monkeypatch, tmp_path):
    import app.server as server

    monkeypatch.setattr(server, "JOBS", tmp_path)
    result = TestClient(server.app).post("/api/process", data={"model": str(tmp_path / "missing")},
                                        files={"image": ("image.png", b"test")})
    assert result.status_code == 400
    assert result.json()["detail"] == server.CHECKPOINT_ERROR
    assert not list(tmp_path.iterdir())
