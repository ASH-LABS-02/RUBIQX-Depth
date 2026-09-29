"""DepthWizard web app: upload -> process -> 3D flythrough.

Run:  python run.py          (opens http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from depthwizard.pipeline import run
from depthwizard.mesh_export import ALLOWED_RESOLUTIONS, export_glb, export_obj_zip
from depthwizard.report import generate_html_report

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
JOBS = ROOT / "data" / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
TRAINING_ROOT = Path(os.environ.get("DEPTHWIZARD_TRAINING_ROOT", "D:/DepthWizard"))

app = FastAPI(title="DepthWizard")
_status: dict[str, dict] = {}
_lock = threading.Lock()   # one model inference at a time (GPU memory)
_export_lock = threading.Lock()


def _save(upload: UploadFile | None, folder: Path) -> str | None:
    if upload is None or not upload.filename:
        return None
    dest = folder / Path(upload.filename).name
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return str(dest)


def _worker(job_id: str, kwargs: dict):
    st = _status[job_id]

    def log(msg):
        st["log"].append(msg)

    try:
        with _lock:
            st["state"] = "running"
            run(log=log, **kwargs)
        st["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        st["state"] = "error"
        st["error"] = f"{exc.__class__.__name__}: {exc}"
        log(traceback.format_exc(limit=3))


@app.post("/api/process")
async def process(image: UploadFile = File(...),
                  dem: UploadFile | None = File(None),
                  reference: UploadFile | None = File(None),
                  gcp: UploadFile | None = File(None),
                  model: str = Form("small"),
                  scene: str = Form("auto"),
                  gsd: float = Form(1.0),
                  fetch_dem: bool = Form(False),
                  dem_source: str = Form("COP30"),
                  name: str = Form("")):
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    folder = JOBS / job_id
    inputs = folder / "inputs"
    inputs.mkdir(parents=True)
    kwargs = dict(image_path=_save(image, inputs), out_dir=str(folder),
                  dem=_save(dem, inputs), reference=_save(reference, inputs),
                  gcp=_save(gcp, inputs), model=model, scene=scene, assumed_gsd_m=gsd,
                  fetch_dem=fetch_dem, dem_source=dem_source)
    (folder / "job.json").write_text(json.dumps({"name": name or image.filename,
                                                 "created": time.time()}))
    _status[job_id] = {"state": "queued", "log": [], "error": None}
    threading.Thread(target=_worker, args=(job_id, kwargs), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id in _status:
        return _status[job_id]
    if (JOBS / job_id / "viewer" / "meta.json").exists():
        return {"state": "done", "log": [], "error": None}
    raise HTTPException(404)


@app.get("/api/scenes")
def scenes():
    out = []
    for d in sorted(JOBS.iterdir(), reverse=True):
        meta = d / "viewer" / "meta.json"
        if not meta.exists():
            continue
        m = json.loads(meta.read_text())
        info = json.loads((d / "job.json").read_text()) if (d / "job.json").exists() else {}
        out.append({"id": d.name, "name": info.get("name", m.get("input", d.name)),
                    "units": m.get("units"), "method": m.get("calibration", {}).get("method"),
                    "has_reference": m.get("has_reference", False)})
    return out


@app.delete("/api/scenes/{job_id}")
def delete_scene(job_id: str):
    d = (JOBS / job_id).resolve()
    if d.parent != JOBS.resolve() or not d.exists():
        raise HTTPException(404)
    shutil.rmtree(d)
    _status.pop(job_id, None)
    return {"ok": True}


@app.get("/api/scenes/{job_id}/dsm")
def download_dsm(job_id: str):
    d = JOBS / job_id
    for name in ("dsm.tif", "rdsm.tif"):
        if (d / name).exists():
            return FileResponse(d / name, filename=f"{job_id}_{name}",
                                media_type="image/tiff")
    raise HTTPException(404)


def _completed_scene(job_id: str) -> Path:
    folder = (JOBS / job_id).resolve()
    if folder.parent != JOBS.resolve() or not (folder / "viewer" / "meta.json").is_file():
        raise HTTPException(404, "completed scene not found")
    return folder


def _mesh_download(job_id: str, resolution: int, kind: str):
    if resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(422, f"resolution must be one of {ALLOWED_RESOLUTIONS}")
    folder = _completed_scene(job_id)
    output = folder / "exports" / f"terrain-{resolution}.{kind}"
    inputs = [folder / "viewer" / "meta.json", folder / "viewer" / "texture.jpg",
              folder / "viewer" / "height.bin"]
    if (folder / "rdsm.tif").exists():
        inputs.append(folder / "rdsm.tif")
    with _export_lock:
        if not output.exists() or output.stat().st_mtime < max(p.stat().st_mtime for p in inputs):
            try:
                if kind == "glb":
                    export_glb(folder, output, resolution)
                else:
                    export_obj_zip(folder, output, resolution)
            except (OSError, ValueError, KeyError) as exc:
                raise HTTPException(422, f"terrain export failed: {exc}") from exc
    return FileResponse(output, filename=f"{job_id}_terrain_{resolution}.{kind}",
                        media_type="model/gltf-binary" if kind == "glb" else "application/zip")


@app.get("/api/scenes/{job_id}/mesh.glb")
def download_glb(job_id: str, resolution: int = 256):
    """Portable textured GLB. Resolution is the maximum mesh grid dimension."""
    return _mesh_download(job_id, resolution, "glb")


@app.get("/api/scenes/{job_id}/mesh.obj.zip")
def download_obj(job_id: str, resolution: int = 256):
    """OBJ, MTL, optical texture and coordinate metadata in a single ZIP."""
    return _mesh_download(job_id, resolution, "obj.zip")


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True})


@app.get("/api/local-model")
def local_model():
    """Expose the optional GAMUS checkpoint and its local training stage."""
    checkpoint = TRAINING_ROOT / "checkpoints" / "da2-gamus-full"
    ready = (checkpoint / "config.json").exists() and any(
        (checkpoint / name).exists()
        for name in ("model.safetensors", "pytorch_model.bin")
    )
    status_path = TRAINING_ROOT / "training-status.json"
    stage = None
    if status_path.exists():
        try:
            stage = json.loads(status_path.read_text(encoding="utf-8")).get("stage")
        except (OSError, ValueError):
            pass
    history = checkpoint / "history.jsonl"
    epochs_done = 0
    if history.exists():
        try:
            epochs_done = max(
                (int(json.loads(line)["epoch"]) for line in history.read_text().splitlines() if line),
                default=0,
            )
        except (OSError, ValueError, KeyError):
            pass
    return {"ready": ready, "path": str(checkpoint) if ready else None,
            "stage": stage, "epochs_done": epochs_done}


@app.get("/api/scenes/{job_id}/buildings")
def scene_buildings(job_id: str):
    folder = _completed_scene(job_id)
    path = folder / "viewer" / "buildings.json"
    if not path.is_file():
        return JSONResponse({"count": 0, "buildings": []})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/scenes/{job_id}/report")
def scene_report(job_id: str):
    folder = _completed_scene(job_id)
    return HTMLResponse(generate_html_report(folder))


@app.get("/api/scenes/{job_id}/evidence")
def scene_evidence(job_id: str):
    folder = _completed_scene(job_id)
    meta = json.loads((folder / "viewer" / "meta.json").read_text(encoding="utf-8"))
    metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8")) if (folder / "metrics.json").exists() else {}
    evidence = {
        "scene_id": job_id,
        "input": meta.get("input"),
        "crs": meta.get("crs"),
        "vertical_datum": meta.get("vertical_datum"),
        "units": meta.get("units"),
        "calibration": meta.get("calibration"),
        "metrics": metrics,
        "evidence_bundle": meta.get("evidence_bundle", {}),
    }
    return JSONResponse(evidence)


app.mount("/jobs", StaticFiles(directory=JOBS), name="jobs")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
