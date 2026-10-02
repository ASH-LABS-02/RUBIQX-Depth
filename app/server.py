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

import rasterio
import numpy as np
from PIL import Image
from pydantic import BaseModel

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from depthwizard.pipeline import run
from depthwizard.mesh_export import ALLOWED_RESOLUTIONS, export_glb, export_obj_zip
from depthwizard.report import generate_html_report
from app.mission_api import create_mission_router

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
JOBS = ROOT / "data" / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
TRAINING_ROOT = Path(os.environ.get("DEPTHWIZARD_TRAINING_ROOT", "D:/DepthWizard"))

app = FastAPI(title="DepthWizard")


@app.middleware("http")
async def _revalidate_scene_files(request, call_next):
    """Scene files change in place (rescale, GCP apply, change detection). Let the
    browser cache them but always revalidate (cheap 304s), so it never shows stale
    heights or buildings after an edit."""
    response = await call_next(request)
    if request.url.path.startswith("/jobs/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
_status: dict[str, dict] = {}
_comparison_status: dict[str, dict] = {}
_lock = threading.Lock()   # one model inference at a time (GPU memory)
_state_lock = threading.Lock()   # job/comparison status bookkeeping only (never held during work)
_files_lock = threading.Lock()   # rewrites of scene files (rescale, missions, auto-anchors); never waits on the GPU
_export_lock = threading.Lock()


def _save(upload: UploadFile | None, folder: Path, role: str) -> str | None:
    if upload is None or not upload.filename:
        return None
    # Keep the caller's basename for provenance, but isolate each input role.
    # An RGB, DEM and reference can all be named image.tif without colliding.
    role_folder = folder / role
    role_folder.mkdir(parents=True, exist_ok=True)
    dest = role_folder / Path(upload.filename).name
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return str(dest)


import queue as _queue

_jobs_q: "_queue.Queue[tuple[str, dict]]" = _queue.Queue()
_queue_order: list[str] = []          # job ids waiting, oldest first
_LOG_LIMIT = 500


def _save_status(job_id: str) -> None:
    """Persist a job's state so a server restart doesn't lose it."""
    st = _status.get(job_id)
    folder = JOBS / job_id
    if st is None or not folder.is_dir():
        return
    try:
        (folder / "status.json").write_text(json.dumps(
            {"state": st["state"], "error": st.get("error"), "log": st["log"][-_LOG_LIMIT:]}), encoding="utf-8")
    except OSError:
        pass


def _run_job(job_id: str, kwargs: dict):
    st = _status[job_id]

    def log(msg):
        st["log"].append(msg)
        if len(st["log"]) > _LOG_LIMIT:
            del st["log"][: len(st["log"]) - _LOG_LIMIT]

    try:
        with _lock:
            with _state_lock:
                st["state"] = "running"
            _save_status(job_id)
            run(log=log, **kwargs)
        st["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        st["state"] = "error"
        st["error"] = f"{exc.__class__.__name__}: {exc}"
        log(traceback.format_exc(limit=3))
    finally:
        _save_status(job_id)


def _queue_worker():
    """One worker processes uploads strictly in the order they arrived."""
    while True:
        job_id, kwargs = _jobs_q.get()
        with _state_lock:
            if job_id in _queue_order:
                _queue_order.remove(job_id)
        try:
            if job_id in _status:            # deleted while waiting → skip
                _run_job(job_id, kwargs)
        finally:
            _jobs_q.task_done()


def _mark_interrupted_jobs():
    """Jobs that were queued/running when the server stopped cannot resume."""
    for status_file in JOBS.glob("*/status.json"):
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("state") in ("queued", "running"):
            data["state"] = "error"
            data["error"] = "The server restarted before this job finished; please process it again."
            status_file.write_text(json.dumps(data), encoding="utf-8")


_mark_interrupted_jobs()
threading.Thread(target=_queue_worker, daemon=True, name="depthwizard-jobs").start()


@app.post("/api/process")
async def process(image: UploadFile = File(...),
                  dem: UploadFile | None = File(None),
                  reference: UploadFile | None = File(None),
                  gcp: UploadFile | None = File(None),
                  gcp_height_type: str = Form("orthometric"),
                  model: str = Form("small"),
                  scene: str = Form("auto"),
                  gsd: float = Form(1.0),
                  fetch_dem: bool = Form(True),
                  dem_source: str = Form(""),
                  tta: int = Form(4),
                  dem_kind: str = Form("auto"),
                  match_dem_30m: bool = Form(False),
                  sun_elevation: str = Form(""),
                  sun_azimuth: str = Form(""),
                  name: str = Form("")):
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    folder = JOBS / job_id
    inputs = folder / "inputs"
    inputs.mkdir(parents=True)
    kwargs = dict(image_path=_save(image, inputs, "image"), out_dir=str(folder),
                  dem=_save(dem, inputs, "dem"), reference=_save(reference, inputs, "reference"),
                  gcp=_save(gcp, inputs, "gcp"), model=model, scene=scene, assumed_gsd_m=gsd,
                  gcp_height_type=gcp_height_type if gcp_height_type in ("orthometric", "ellipsoidal") else "orthometric",
                  fetch_dem=fetch_dem, dem_source=dem_source,
                  tta=max(1, min(8, int(tta))), dem_kind=dem_kind if dem_kind in ("auto", "surface", "terrain") else "auto",
                  match_dem_30m=match_dem_30m,
                  sun_elevation=float(sun_elevation) if sun_elevation.strip() else None,
                  sun_azimuth=float(sun_azimuth) if sun_azimuth.strip() else None)
    (folder / "job.json").write_text(json.dumps({"name": name or image.filename,
                                                 "created": time.time(),
                                                 "input_names": {"image": image.filename,
                                                                 "dem": dem.filename if dem else None,
                                                                 "reference": reference.filename if reference else None,
                                                                 "gcp": gcp.filename if gcp else None}}))
    with _state_lock:
        _status[job_id] = {"state": "queued", "log": [], "error": None}
        _queue_order.append(job_id)
    _save_status(job_id)
    _jobs_q.put((job_id, kwargs))
    return {"id": job_id, "position": _queue_position(job_id)}


def _queue_position(job_id: str) -> int:
    """0 = running or next; n = n jobs ahead of it."""
    with _state_lock:
        if job_id not in _queue_order:
            return 0
        running = any(st["state"] == "running" for st in _status.values())
        return _queue_order.index(job_id) + (1 if running else 0)


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id in _status:
        st = _status[job_id]
        out = {"state": st["state"], "log": list(st["log"]), "error": st.get("error")}
        if st["state"] == "queued":
            out["position"] = _queue_position(job_id)
        return out
    status_file = JOBS / job_id / "status.json"
    if status_file.is_file():
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except ValueError:
            pass
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
    with _state_lock:
        _status.pop(job_id, None)
        if job_id in _queue_order:
            _queue_order.remove(job_id)
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


PRODUCTS = {"dsm": ("dsm.tif", "rdsm.tif"), "dtm": ("dtm.tif",), "ndsm": ("ndsm.tif",),
            "uncertainty": ("uncertainty.tif",)}


@app.get("/api/scenes/{job_id}/product/{kind}")
def download_product(job_id: str, kind: str):
    """GeoTIFF products: dsm, dtm, ndsm, uncertainty."""
    folder = _completed_scene(job_id)
    for name in PRODUCTS.get(kind, ()):
        if (folder / name).exists():
            return FileResponse(folder / name, filename=f"{job_id}_{name}", media_type="image/tiff")
    raise HTTPException(404, f"{kind} is not available for this scene")


@app.get("/api/scenes/{job_id}/heightmap.png")
def heightmap_png(job_id: str, bits: int = 16):
    """Greyscale heightmap for game engines / Blender (16-bit default, or 8-bit).
    The height range is written in the PNG text chunk and the filename."""
    from PIL import PngImagePlugin
    from depthwizard.io import read_raster
    folder = _completed_scene(job_id)
    z = read_raster(_surface_tif(folder))[0]
    z = np.where(np.isfinite(z), z, np.nanmin(z))
    lo, hi = float(np.percentile(z, 0.1)), float(np.percentile(z, 99.9))
    t = np.clip((z - lo) / max(hi - lo, 1e-9), 0, 1)
    out = folder / "exports"
    out.mkdir(exist_ok=True)
    info = PngImagePlugin.PngInfo()
    info.add_text("height_min", f"{lo:.3f}"); info.add_text("height_max", f"{hi:.3f}")
    info.add_text("units", "metre" if (folder / "dsm.tif").is_file() else "relative")
    if int(bits) == 8:
        img = Image.fromarray((t * 255 + 0.5).astype(np.uint8), "L")
    else:
        img = Image.fromarray((t * 65535 + 0.5).astype(np.uint16))
    path = out / f"heightmap_{int(bits)}bit.png"
    img.save(path, pnginfo=info)
    name = f"{job_id}_heightmap_{int(bits)}bit_{lo:.1f}_to_{hi:.1f}.png"
    return FileResponse(path, filename=name, media_type="image/png")


@app.get("/api/scenes/{job_id}/export-all.zip")
def download_all(job_id: str):
    """Every product for a scene in one ZIP: GeoTIFFs, CityJSON, PLY, GLB,
    HTML report and the evidence metadata."""
    import zipfile
    from depthwizard.exports import export_cityjson, export_ply
    folder = _completed_scene(job_id)
    out_dir = folder / "exports"
    out_dir.mkdir(exist_ok=True)
    archive = out_dir / f"{job_id}_all_products.zip"
    notes = []
    with _export_lock:
        members: list[tuple[Path, str]] = []
        for name in ("dsm.tif", "rdsm.tif", "dtm.tif", "ndsm.tif", "uncertainty.tif", "preview.png",
                     "meta.json", "metrics.json"):
            if (folder / name).is_file():
                members.append((folder / name, name))
        if (folder / "viewer" / "buildings.json").is_file():
            try:
                members.append((export_cityjson(folder, out_dir / "buildings.city.json"), "buildings.city.json"))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"CityJSON skipped: {exc}")
        try:
            members.append((export_ply(folder, out_dir / "points.ply"), "points.ply"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"PLY skipped: {exc}")
        glb = out_dir / "terrain-256.glb"
        try:
            export_glb(folder, glb, 256)
            members.append((glb, "terrain.glb"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"GLB skipped: {exc}")
        try:
            heightmap_png(job_id, 16)
            members.append((out_dir / "heightmap_16bit.png", "heightmap_16bit.png"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"heightmap skipped: {exc}")
        report = out_dir / "report.html"
        try:
            report.write_text(generate_html_report(folder), encoding="utf-8")
            members.append((report, "report.html"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"report skipped: {exc}")
        readme = ("DepthWizard / RUBIQX-Depth export for scene " + job_id + "\n\n"
                  "dsm/rdsm: surface heights (rdsm = relative units)\n"
                  "dtm: bare earth · ndsm: height above ground · uncertainty: 1-sigma ensemble spread\n"
                  "buildings.city.json: LoD1 buildings (CityJSON 1.1) · points.ply: coloured point cloud\n"
                  "terrain.glb: textured mesh · report.html: open in a browser, print to PDF\n"
                  "meta.json: calibration method, evidence level, datum and file hashes\n")
        if notes:
            readme += "\nNotes:\n" + "\n".join(notes) + "\n"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for src, arc in members:
                z.write(src, arc)
            z.writestr("README.txt", readme)
    return FileResponse(archive, filename=f"{job_id}_all_products.zip", media_type="application/zip")


@app.get("/api/scenes/{job_id}/buildings.city.json")
def download_cityjson(job_id: str):
    """LoD1 buildings as CityJSON 1.1 (convertible to CityGML)."""
    from depthwizard.exports import export_cityjson
    folder = _completed_scene(job_id)
    if not (folder / "viewer" / "buildings.json").exists():
        raise HTTPException(404, "no buildings in this scene")
    (folder / "exports").mkdir(exist_ok=True)
    out = export_cityjson(folder, folder / "exports" / "buildings.city.json")
    return FileResponse(out, filename=f"{job_id}_buildings.city.json", media_type="application/json")


@app.get("/api/scenes/{job_id}/points.ply")
def download_ply(job_id: str):
    """Coloured DSM point cloud (binary PLY) for CloudCompare / MeshLab."""
    from depthwizard.exports import export_ply
    folder = _completed_scene(job_id)
    (folder / "exports").mkdir(exist_ok=True)
    out = export_ply(folder, folder / "exports" / "points.ply")
    return FileResponse(out, filename=f"{job_id}_points.ply", media_type="application/octet-stream")


@app.get("/api/scenes/{job_id}/change/{other_id}")
def change_detection(job_id: str, other_id: str, drop_m: float = 3.0):
    """Pre/post comparison: `job_id` = before, `other_id` = after. Writes a
    change layer into the 'after' scene's viewer and returns statistics."""
    import numpy as np
    from depthwizard.analysis import change_detection as cd, load_scene_layer
    pre_dir, post_dir = _completed_scene(job_id), _completed_scene(other_id)
    pre, post = load_scene_layer(pre_dir, "height.bin"), load_scene_layer(post_dir, "height.bin")
    if pre.shape != post.shape:
        raise HTTPException(422, "scenes must cover the same footprint and grid (process both with the same crop)")
    vm = json.loads((post_dir / "viewer" / "meta.json").read_text())
    labels = None
    lab_path = pre_dir / "building_labels.npy"
    if lab_path.exists():
        from PIL import Image
        lab = np.load(lab_path)
        labels = np.asarray(Image.fromarray(lab.astype(np.int32)).resize((vm["grid_w"], vm["grid_h"]), Image.NEAREST))
    d, stats = cd(pre, post, vm["gsd_m"] * vm["src_w"] / vm["grid_w"], labels, drop_m)
    d.astype("<f4").tofile(post_dir / "viewer" / "change.bin")
    vm.setdefault("layers", {})["change"] = True
    vm["change_against"] = job_id
    vm["change_stats"] = stats
    (post_dir / "viewer" / "meta.json").write_text(json.dumps(vm, indent=2, default=float))
    return JSONResponse({"before": job_id, "after": other_id, **stats})


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True})


@app.get("/api/local-model")
def local_model():
    """Expose the optional GAMUS checkpoint and its local training stage."""
    checkpoint = TRAINING_ROOT / "checkpoints" / "da2-gamus-full"
    bundled = ROOT / "models" / "da2-gamus-full"      # portable / packaged build
    if not (checkpoint / "config.json").exists() and (bundled / "config.json").exists():
        checkpoint = bundled
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


_HASH_CACHE: dict[str, tuple[float, str]] = {}


def _hash16_cached(path: Path) -> str:
    key = str(path.resolve())
    mtime = path.stat().st_mtime
    hit = _HASH_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    value = _file_hash16(path)
    _HASH_CACHE[key] = (mtime, value)
    return value


_LEGACY_HINTS = {"dem": ("dem_30m", "dtm_2018_32m", "_32m", "_30m", "cop30", "srtm", "dem", "dtm"),
                 "reference": ("lidar", "reference", "ref_dsm", "dsm_2024")}


def _find_legacy_original(meta: dict, kind: str, wanted: str | None, near: Path | None) -> Path | None:
    """Scenes made before the evidence bundle existed carry no file hashes.
    The image is matched by name plus identical size and georeference; DEM and
    reference are then taken from the same sample folder by filename."""
    if kind == "image":
        if not wanted:
            return None
        src_w, src_h = meta.get("src_w"), meta.get("src_h")
        tr = meta.get("transform")
        for cand in sorted((ROOT / "samples").rglob(Path(wanted).name)):
            try:
                if cand.suffix.lower() in (".tif", ".tiff"):
                    with rasterio.open(cand) as src:
                        if src_w and (src.width, src.height) != (src_w, src_h):
                            continue
                        if tr and not np.allclose(list(src.transform)[:6], tr[:6], rtol=0, atol=1e-6):
                            continue
                else:
                    with Image.open(cand) as im:
                        if src_w and im.size != (src_w, src_h):
                            continue
                return cand
            except Exception:  # noqa: BLE001
                continue
        return None
    if near is None or kind not in _LEGACY_HINTS:
        return None
    files = [f for f in sorted(near.iterdir()) if f.is_file() and f.suffix.lower() in (".tif", ".tiff")]
    image_name = str(meta.get("input") or "")
    stem_hint = Path(image_name).stem.lower().split("_rgb")[0] if "_rgb" in image_name.lower() else ""
    if stem_hint and any(f.name.lower().startswith(stem_hint) for f in files):
        files = [f for f in files if f.name.lower().startswith(stem_hint)]   # e.g. forest_south_* only
    for hint in _LEGACY_HINTS[kind]:
        for f in files:
            name = f.name.lower()
            if hint in name and "rgb" not in name:
                if kind == "dem" and any(k in name for k in ("lidar", "reference", "1m")):
                    continue
                return f
    return None


def _find_original(folder: Path, meta: dict, kind: str, near: Path | None = None) -> Path | None:
    """Locate an original input (image, dem, gcp, reference) for a scene.

    Web uploads live in <scene>/inputs/<role>/, with the older flat layout
    still readable. Scenes made from the command line (the bundled demos) have
    no copy, so fall back to the recorded path and then to a SHA-256 match in
    the sample folders, checked against the evidence bundle."""
    fingerprint = (meta.get("evidence_bundle") or {}).get(f"{kind}_sha256")
    names = {}
    if (folder / "job.json").is_file():
        try:
            names = json.loads((folder / "job.json").read_text(encoding="utf-8")).get("input_names", {}) or {}
        except ValueError:
            names = {}
    wanted = names.get(kind) or (meta.get("input") if kind == "image" else None)
    inputs = folder / "inputs"
    if wanted:
        # New uploads are separated by role; the flat path keeps older jobs
        # readable. Check the fingerprint so a same-named file cannot be
        # mistaken for another input when revisiting a scene.
        for candidate in (inputs / kind / Path(wanted).name, inputs / Path(wanted).name):
            if candidate.is_file() and (not fingerprint or _hash16_cached(candidate) == fingerprint):
                return candidate
    if kind == "dem" and (folder / "dem.tif").is_file():
        return folder / "dem.tif"
    recorded = (meta.get("input_paths") or {}).get(kind)
    if recorded and Path(recorded).is_file() and (not fingerprint or _hash16_cached(Path(recorded)) == fingerprint):
        return Path(recorded)
    if not fingerprint:
        return _find_legacy_original(meta, kind, wanted, near)
    suffixes = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".csv"}
    search = [inputs / kind, inputs] + ([near] if near else []) + [ROOT / "samples", ROOT / "data" / "inputs"]
    seen = set()
    candidates_by_source = []
    for base in search:
        if not base or not base.is_dir():
            continue
        candidates = sorted(base.iterdir()) if base in (inputs / kind, inputs, near) else sorted(base.rglob("*"))
        for cand in candidates:
            if cand in seen or not cand.is_file() or cand.suffix.lower() not in suffixes:
                continue
            seen.add(cand)
            if cand.stat().st_size > 2_000_000_000:
                continue
            candidates_by_source.append(cand)
    # Prefer the recorded basename across *all* roots before accepting a
    # hash-identical copy with a different name in an earlier root. The hash
    # remains mandatory, so this only chooses which identical source to use.
    expected_name = Path(wanted).name if wanted else None
    if expected_name:
        candidates_by_source.sort(key=lambda cand: cand.name != expected_name)
    for cand in candidates_by_source:
        if _hash16_cached(cand) == fingerprint:
            return cand
    return None


def _comparison_worker(job_id: str):
    """Rerun the saved optical image with the actual off-the-shelf DA2 weights."""
    st = _comparison_status[job_id]
    folder = JOBS / job_id
    try:
        meta = json.loads((folder / "viewer" / "meta.json").read_text(encoding="utf-8"))
        image_path = _find_original(folder, meta, "image")
        if image_path is None:
            raise FileNotFoundError("the original optical image for this scene could not be found "
                                    "(looked in the scene's inputs folder and in samples/)")
        near = image_path.parent

        def find_original(kind):
            found = _find_original(folder, meta, kind, near=near)
            return str(found) if found else None
        st["state"] = "running"
        output = folder / "model_comparison" / "pretrained"
        with _lock:
            baseline = run(image_path=str(image_path), out_dir=str(output),
                           dem=find_original("dem"), gcp=find_original("gcp"),
                           reference=find_original("reference"),
                           model="depth-anything/Depth-Anything-V2-Small-hf",
                           allow_fallback=False, fetch_dem=False, tta=1,
                           scene=meta.get("scene", "auto"),
                           assumed_gsd_m=float(meta.get("assumed_gsd_m", 1.0)),
                           match_dem_30m=bool(meta.get("calibration", {}).get("match_dem_30m", False)),
                           log=lambda line: st["log"].append(line))
        base_vm = json.loads((output / "viewer" / "meta.json").read_text(encoding="utf-8"))
        if base_vm["units"] != meta["units"] or (base_vm["grid_w"], base_vm["grid_h"]) != (meta["grid_w"], meta["grid_h"]):
            raise ValueError("the pretrained output uses different units or grid; supply the same DEM/GCP evidence for both models")
        shutil.copy2(output / "viewer" / "height.bin", folder / "viewer" / "pretrained_height.bin")
        summary = {"backbone": baseline["backbone"], "units": base_vm["units"],
                   "calibration": base_vm.get("calibration"), "metrics": baseline.get("metrics", {}),
                   "note": "Each model was processed from the same optical upload; calibration provenance is shown separately."}
        (folder / "viewer" / "pretrained_comparison.json").write_text(json.dumps(summary, indent=2, default=float))
        st["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        st["state"] = "error"
        st["error"] = f"{exc.__class__.__name__}: {exc}"
        st["log"].append(traceback.format_exc(limit=2))


def _file_hash16(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as src:
        for part in iter(lambda: src.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()[:16]


@app.post("/api/scenes/{job_id}/model-comparison")
def request_model_comparison(job_id: str):
    folder = _completed_scene(job_id)
    vm = json.loads((folder / "viewer" / "meta.json").read_text(encoding="utf-8"))
    if "gamus" not in str(vm.get("backbone", "")).lower():
        raise HTTPException(400, "model comparison requires a scene made with the GAMUS fine-tuned checkpoint")
    if (folder / "viewer" / "pretrained_height.bin").is_file():
        return {"state": "done"}
    with _state_lock:
        old = _comparison_status.get(job_id)
        if old and old["state"] in ("queued", "running"):
            return old
        _comparison_status[job_id] = {"state": "queued", "log": [], "error": None}
    threading.Thread(target=_comparison_worker, args=(job_id,), daemon=True).start()
    return _comparison_status[job_id]


@app.get("/api/scenes/{job_id}/model-comparison")
def model_comparison_status(job_id: str):
    folder = _completed_scene(job_id)
    if (folder / "viewer" / "pretrained_height.bin").is_file():
        path = folder / "viewer" / "pretrained_comparison.json"
        return {"state": "done", "comparison": json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}}
    return _comparison_status.get(job_id, {"state": "idle", "log": [], "error": None})


@app.post("/api/scenes/{job_id}/auto-anchors")
def scan_auto_anchors(job_id: str):
    """Refresh provisional OSM/shadow candidates for an existing metric scene."""
    from depthwizard.auto_anchors import automatic_height_anchors
    from depthwizard.analysis import water_mask
    from depthwizard.io import read_image, read_raster
    folder = _completed_scene(job_id)
    vm_path = folder / "viewer" / "meta.json"
    vm = json.loads(vm_path.read_text(encoding="utf-8"))
    if vm.get("units") != "metre" or not (folder / "building_labels.npy").is_file():
        raise HTTPException(400, "automatic anchors require a metric scene with building footprints")
    top_meta = json.loads((folder / "meta.json").read_text(encoding="utf-8")) if (folder / "meta.json").is_file() else vm
    image_path = _find_original(folder, {**vm, **top_meta}, "image")
    if image_path is None:
        raise HTTPException(404, "the original optical image for this scene could not be found")
    from depthwizard.io import match_grid
    labels = np.load(folder / "building_labels.npy")
    img = match_grid(read_image(image_path), labels.shape)
    building_data = json.loads((folder / "viewer" / "buildings.json").read_text(encoding="utf-8"))
    ndsm_path = folder / "ndsm_raw.tif" if (folder / "ndsm_raw.tif").is_file() else folder / "ndsm.tif"
    ndsm = read_raster(ndsm_path)[0]
    cal = vm.get("calibration", {})
    sun_input = vm.get("sun_input") or {}
    anchors, diagnostics = automatic_height_anchors(
        img, labels, building_data.get("buildings", []),
        gsd_m=float(vm["gsd_m"]), ndsm=ndsm,
        dtm=read_raster(folder / "dtm.tif")[0] if (folder / "dtm.tif").is_file() else None,
        water=water_mask(img.rgb, float(vm["gsd_m"])),
        sun_elevation_deg=sun_input.get("elevation_deg") or cal.get("sun_elevation_deg"),
        sun_azimuth_deg=sun_input.get("azimuth_deg") or cal.get("sun_azimuth_deg"),
        cache_dir=folder.parent / "osm_cache")
    already_applied = (vm.get("height_anchor") or {}).get("source") == "automatic"
    record = {"anchors": anchors, "diagnostics": diagnostics, "applied": already_applied}
    with _files_lock:
        for path in (folder / "meta.json", vm_path):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["auto_anchors"] = record
            path.write_text(json.dumps(data, indent=2, default=float), encoding="utf-8")
    return record


class RescaleBody(BaseModel):
    anchors: list[dict] = []
    reset: bool = False
    automatic: bool = False

def _rewrite_tif(path: Path, arr: np.ndarray):
    """Overwrite a GeoTIFF keeping its CRS/transform/tags."""
    with rasterio.open(path) as src:
        profile, tags = src.profile, src.tags()
    out = np.where(np.isfinite(arr), arr, -9999.0).astype(np.float32)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(out, 1)
        dst.update_tags(**tags)

def _viewer_bin(folder: Path, name: str, arr: np.ndarray, vm: dict):
    """Same downsampling as io.export_viewer_assets.down()."""
    from depthwizard.io import _resize
    a = np.where(np.isfinite(arr), arr, np.nanmin(arr)).astype(np.float32)
    _resize(a, (vm["grid_w"], vm["grid_h"]), Image.BILINEAR).astype("<f4").tofile(folder / "viewer" / name)

# ---------------------------------------------------------------- v3: hi-res grids, normal map, GCP pins
def _surface_tif(folder: Path) -> Path:
    for name in ("dsm.tif", "rdsm.tif"):
        if (folder / name).is_file():
            return folder / name
    raise HTTPException(404, "scene has no surface raster")


def _view_scale(vm: dict) -> float:
    """height.bin is in display units: metres, or relative x display_height_m."""
    return 1.0 if vm.get("units") == "metre" else float(vm.get("display_height_m") or 1.0)


@app.get("/api/scenes/{job_id}/grid/{layer}.bin")
def grid_layer(job_id: str, layer: str, size: int = 1024):
    """A viewer layer at a higher mesh resolution (up to 2048), resampled from
    the full-resolution GeoTIFF exactly like the 512 viewer export."""
    from depthwizard.io import _resize, read_raster
    folder = _completed_scene(job_id)
    vm = json.loads((folder / "viewer" / "meta.json").read_text(encoding="utf-8"))
    size = int(max(64, min(2048, size)))
    src_name = {"height": None, "dtm": "dtm.tif"}.get(layer, "missing")
    if src_name == "missing":
        raise HTTPException(404, "layer must be height or dtm")
    path = _surface_tif(folder) if layer == "height" else folder / src_name
    if not path.is_file():
        raise HTTPException(404, f"{layer} not available")
    arr = read_raster(path)[0]
    h, w = arr.shape
    f = min(1.0, size / max(h, w))
    gw, gh = max(2, int(round(w * f))), max(2, int(round(h * f)))
    a = np.where(np.isfinite(arr), arr, np.nanmin(arr)).astype(np.float32)
    if path.name == "rdsm.tif" or (layer == "height" and vm.get("units") != "metre"):
        a = a * _view_scale(vm)
    out = _resize(a, (gw, gh), Image.BILINEAR).astype("<f4")
    return Response(out.tobytes(), media_type="application/octet-stream",
                    headers={"X-Grid-W": str(gw), "X-Grid-H": str(gh), "Cache-Control": "no-store"})


@app.get("/api/scenes/{job_id}/normal.png")
def normal_map(job_id: str):
    """Tangent-space normal map of fine surface detail (high-pass of the DSM),
    so hillshade shows roof edges and ridges beyond the mesh resolution."""
    from scipy import ndimage as ndi
    from depthwizard.io import read_raster
    folder = _completed_scene(job_id)
    out = folder / "viewer" / "normal.png"
    surf = _surface_tif(folder)
    if not out.is_file() or out.stat().st_mtime < surf.stat().st_mtime:
        vm = json.loads((folder / "viewer" / "meta.json").read_text(encoding="utf-8"))
        z = read_raster(surf)[0].astype(np.float32) * _view_scale(vm)
        z = np.where(np.isfinite(z), z, np.nanmedian(z))
        h, w = z.shape
        scale = min(1.0, 4096 / max(h, w))
        if scale < 1:
            z = np.asarray(Image.fromarray(z).resize((int(w * scale), int(h * scale)), Image.BILINEAR))
        gsd = float(vm.get("gsd_m") or 1.0) / scale
        mesh_cell = max(vm["ground_w_m"] / max(vm["grid_w"] - 1, 1), gsd)
        detail = z - ndi.gaussian_filter(z, sigma=max(1.0, mesh_cell / gsd))   # only what the mesh cannot show
        gy, gx = np.gradient(detail, gsd)              # gy: +south (image rows), gx: +east
        nx, ny, nz = -gx, gy, np.ones_like(gx)         # tangent space: +x east, +y north
        n = np.sqrt(nx * nx + ny * ny + nz * nz)
        rgb = np.stack([nx / n, ny / n, nz / n], -1) * 0.5 + 0.5
        Image.fromarray((rgb * 255).clip(0, 255).astype(np.uint8)).save(out)
    return FileResponse(out, media_type="image/png", headers={"Cache-Control": "no-store"})


class GcpBody(BaseModel):
    points: list[dict] = []          # {u, v, height_m} in normalised image coordinates
    apply: bool = False
    reset: bool = False


_GCP_BACKUP = ("dsm.tif", "rdsm.tif", "dtm.tif", "ndsm.tif", "uncertainty.tif", "ensemble_spread.tif", "meta.json",
               "viewer/meta.json", "viewer/height.bin", "viewer/dtm.bin", "viewer/buildings.json", "viewer/unc.bin")


@app.post("/api/scenes/{job_id}/gcp")
def gcp_fit(job_id: str, body: GcpBody):
    """Interactive ground-control points: robust affine fit of known heights
    against the current surface, with R², RMSE and leave-one-out error; optional
    apply (turns a relative scene metric) and exact reset."""
    from depthwizard.calibrate import huber_affine
    from depthwizard.io import read_raster
    folder = _completed_scene(job_id)
    backup = folder / "gcp_backup"
    with _files_lock:
        if body.reset:
            if not backup.is_dir():
                return {"reset": False, "note": "no GCP calibration to undo"}
            created = json.loads((backup / "created.json").read_text()) if (backup / "created.json").is_file() else []
            for rel in created:
                (folder / rel).unlink(missing_ok=True)
            for rel in _GCP_BACKUP:
                src = backup / rel
                if src.is_file():
                    (folder / rel).parent.mkdir(exist_ok=True)
                    shutil.copy2(src, folder / rel)
            shutil.rmtree(backup)
            for stale in (folder / "exports").glob("*") if (folder / "exports").is_dir() else ():
                stale.unlink(missing_ok=True)
            return {"reset": True}
        pts = [p for p in body.points if all(k in p for k in ("u", "v", "height_m"))]
        if len(pts) < 2:
            raise HTTPException(422, "place at least 2 points (3 or more gives an error estimate)")
        # always fit against the ORIGINAL surface so repeated applies never compound
        surf_path = (backup / "dsm.tif") if (backup / "dsm.tif").is_file() else (backup / "rdsm.tif") if (backup / "rdsm.tif").is_file() else _surface_tif(folder)
        surf = read_raster(surf_path)[0]
        relative = surf_path.name == "rdsm.tif"
        h, w = surf.shape
        xs, ys = [], []
        for p in pts:
            u, v, hm = float(p["u"]), float(p["v"]), float(p["height_m"])
            if not (0 <= u <= 1 and 0 <= v <= 1 and np.isfinite(hm)):
                raise HTTPException(422, "points need u, v in [0, 1] and a finite height_m")
            c, r = min(w - 1, int(u * (w - 1) + 0.5)), min(h - 1, int(v * (h - 1) + 0.5))
            win = surf[max(0, r - 1):r + 2, max(0, c - 1):c + 2]
            xs.append(float(np.nanmedian(win))); ys.append(hm)
        x, y = np.array(xs), np.array(ys)
        if np.ptp(x) < 1e-6:
            raise HTTPException(422, "points sit at the same surface height; spread them over low and high ground")
        a, b = huber_affine(x, y)
        pred = a * x + b
        ss_res = float(np.sum((y - pred) ** 2)); ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
        loo = []
        if len(x) >= 3:
            for i in range(len(x)):
                m = np.arange(len(x)) != i
                if np.ptp(x[m]) > 1e-6:
                    ai, bi = huber_affine(x[m], y[m]); loo.append(float(y[i] - (ai * x[i] + bi)))
        result = {"a": a, "b": b, "n": len(x), "r2": 1 - ss_res / ss_tot,
                  "rmse_m": float(np.sqrt(ss_res / len(x))),
                  "loo_rmse_m": float(np.sqrt(np.mean(np.square(loo)))) if loo else None,
                  "residuals_m": [float(v) for v in (y - pred)], "relative_input": relative,
                  "warning": ("a is negative – check the points" if a <= 0 else
                              "only 2 points: no error estimate" if len(x) < 3 else None)}
        if not body.apply:
            return result
        if a <= 0:
            raise HTTPException(422, "refusing to apply a negative scale; check the points")
        # ---- apply: back up once, then rewrite from the originals
        created = []
        if not backup.is_dir():
            (backup / "viewer").mkdir(parents=True)
            for rel in _GCP_BACKUP:
                if (folder / rel).is_file():
                    shutil.copy2(folder / rel, backup / rel)
            if not (folder / "dsm.tif").is_file():
                created.append("dsm.tif")
            (backup / "created.json").write_text(json.dumps(created))
        vm = json.loads((backup / "viewer" / "meta.json").read_text(encoding="utf-8"))
        top = json.loads((backup / "meta.json").read_text(encoding="utf-8")) if (backup / "meta.json").is_file() else {}
        new = a * surf + b
        target = folder / "dsm.tif"
        shutil.copy2(surf_path, target) if not target.is_file() or relative else None
        _rewrite_tif(target, new)
        with rasterio.open(target, "r+") as dst:
            dst.update_tags(UNITS="metre", DESCRIPTION="DepthWizard DSM calibrated with interactive GCPs")
        for name, fn in (("dtm.tif", lambda z: a * z + b), ("ndsm.tif", lambda z: a * z), ("uncertainty.tif", lambda z: abs(a) * z),
                         ("ensemble_spread.tif", lambda z: abs(a) * z)):
            if (backup / name).is_file():
                _rewrite_tif(folder / name, fn(read_raster(backup / name)[0]))
        if (backup / "ensemble_spread.tif").is_file():
            # exported sigma = calibrated error model applied to the rescaled spread
            from depthwizard.uncertainty import calibrated_sigma
            _rewrite_tif(folder / "uncertainty.tif", calibrated_sigma(read_raster(folder / "ensemble_spread.tif")[0]))
        # viewer layers (height.bin is in display units)
        scale_in = _view_scale(vm)
        hb = np.fromfile(backup / "viewer" / "height.bin", dtype="<f4") / scale_in
        (a * hb + b).astype("<f4").tofile(folder / "viewer" / "height.bin")
        if (backup / "viewer" / "dtm.bin").is_file():
            (a * np.fromfile(backup / "viewer" / "dtm.bin", dtype="<f4") + b).astype("<f4").tofile(folder / "viewer" / "dtm.bin")
        if (backup / "viewer" / "unc.bin").is_file():
            (abs(a) * np.fromfile(backup / "viewer" / "unc.bin", dtype="<f4") / scale_in).astype("<f4").tofile(folder / "viewer" / "unc.bin")
        if (backup / "viewer" / "buildings.json").is_file():
            bj = json.loads((backup / "viewer" / "buildings.json").read_text(encoding="utf-8"))
            for bd in bj.get("buildings", []):
                for k in ("height_m", "height_raw_m"):
                    if bd.get(k) is not None:
                        bd[k] = round(float(bd[k]) * a, 2)
                for k in ("ground_elevation_m", "roof_elevation_m"):
                    if bd.get(k) is not None:
                        bd[k] = round(float(bd[k]) * a + b, 2)
                if bd.get("volume_m3") is not None:
                    bd["volume_m3"] = round(float(bd["volume_m3"]) * a * (1 if not relative else 1), 1)
                bd["storeys"] = max(1, round(bd.get("height_m", 0) / 3.0))
            (folder / "viewer" / "buildings.json").write_text(json.dumps(bj, indent=2), encoding="utf-8")
        record = {"method": "gcp-interactive", "scale_source": f"{len(x)} interactive ground-control points",
                  "evidence_level": "measured" if len(x) >= 3 else "provisional", "scale_k": a, "offset_m": b,
                  "fit_r2": result["r2"], "rmse_m": result["rmse_m"], "loo_rmse_m": result["loo_rmse_m"],
                  "points": pts, "base": vm.get("calibration", {})}
        hv = a * hb + b
        for m, path in ((vm, folder / "viewer" / "meta.json"), (top, folder / "meta.json")):
            if not m:
                continue
            m = dict(m)
            m["units"] = "metre"; m["dsm_file"] = "dsm.tif"
            m["calibration"] = {**(m.get("calibration") or {}), **record}
            m.setdefault("evidence_bundle", {})["calibration_method"] = "gcp-interactive"
            if path.parent.name == "viewer":
                m["h_min"], m["h_max"] = float(hv.min()), float(hv.max())
                m.pop("display_height_m", None)
            path.write_text(json.dumps(m, indent=2, default=float), encoding="utf-8")
        for stale in (folder / "exports").glob("*") if (folder / "exports").is_dir() else ():
            stale.unlink(missing_ok=True)
        result["applied"] = True
        return result


@app.post("/api/scenes/{job_id}/rescale")
def rescale(job_id: str, body: RescaleBody):
    from depthwizard.calibrate import apply_height_anchors
    from depthwizard.io import read_raster
    folder = _completed_scene(job_id)
    if not (folder / "ndsm.tif").exists() or not (folder / "dtm.tif").exists():
        raise HTTPException(400, "height anchors need a metric scene (dsm + dtm + ndsm)")
    with _files_lock:
        vm_prior = json.loads((folder / "viewer" / "meta.json").read_text(encoding="utf-8"))
        normalized_anchors = []
        seen_ids = set()
        if not body.reset:
            for anchor in body.anchors:
                try:
                    building_id = int(anchor["building_id"])
                    height_m = float(anchor["height_m"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    raise HTTPException(422, "each anchor needs a building_id and positive height_m")
                if building_id <= 0 or not np.isfinite(height_m) or height_m <= 0 or building_id in seen_ids:
                    raise HTTPException(422, "anchor IDs must be unique and heights must be positive finite metres")
                seen_ids.add(building_id)
                normalized_anchors.append({**anchor, "building_id": building_id, "height_m": height_m})
            if not normalized_anchors:
                raise HTTPException(422, "provide at least one building height anchor or request reset")
            if body.automatic:
                proposals = (vm_prior.get("auto_anchors") or {}).get("anchors") or []
                approved = {(int(a["building_id"]), round(float(a["height_m"]), 3))
                            for a in proposals if a.get("building_id") is not None and a.get("height_m") is not None
                            and float(a.get("confidence", 0)) >= 0.65
                            and (a.get("source") == "shadow geometry" or a.get("osm_tag") == "height")}
                if len(normalized_anchors) < 3 or any((a["building_id"], round(a["height_m"], 3)) not in approved
                                                      for a in normalized_anchors):
                    raise HTTPException(422, "automatic rescale needs three stored reliable shadow or explicit OSM-height candidates")
        # Older initially anchored scenes did not save an unscaled DSM. Recover
        # it from the recorded scale and unscaled nDSM before taking snapshots.
        old_anchor = vm_prior.get("height_anchor") or {}
        old_scale = float(old_anchor.get("s") or 1.0)
        if not np.isfinite(old_scale) or old_scale <= 0:
            raise HTTPException(409, "recorded previous height-anchor scale is invalid")
        if old_anchor and not (folder / "ndsm_raw.tif").exists():
            shutil.copy2(folder / "ndsm.tif", folder / "ndsm_raw.tif")
            _rewrite_tif(folder / "ndsm_raw.tif", read_raster(folder / "ndsm.tif")[0] / old_scale)
        if old_anchor and not (folder / "dsm_raw.tif").exists():
            raw_ndsm = read_raster(folder / "ndsm_raw.tif")[0]
            current_dsm = read_raster(folder / "dsm.tif")[0]
            shutil.copy2(folder / "dsm.tif", folder / "dsm_raw.tif")
            _rewrite_tif(folder / "dsm_raw.tif", current_dsm - (old_scale - 1.0) * raw_ndsm)
        # 1. snapshot originals once – every rescale starts from these
        for n in ("ndsm", "dsm", "uncertainty", "ensemble_spread"):
            src, raw = folder / f"{n}.tif", folder / f"{n}_raw.tif"
            if src.exists() and not raw.exists():
                shutil.copy2(src, raw)
        bpath = folder / "viewer" / "buildings.json"
        bj = json.loads(bpath.read_text())
        for b in bj["buildings"]:
            b.setdefault("height_raw_m", b["height_m"])
            b.setdefault("volume_raw_m3", b.get("volume_m3"))

        ndsm_raw, _ = read_raster(folder / "ndsm_raw.tif")
        dsm_raw, _ = read_raster(folder / "dsm_raw.tif")
        labels = np.load(folder / "building_labels.npy")
        est = {b["id"]: b["height_raw_m"] for b in bj["buildings"]}

        if body.reset:
            s, stats = 1.0, {"reset": True}
        else:
            s, stats = apply_height_anchors(ndsm_raw, labels, normalized_anchors, est)
            if stats["n_used"] != len(normalized_anchors):
                raise HTTPException(422, "one or more anchor IDs have no usable modelled building height")
            if body.automatic and (stats["cv"] > 0.20 or not 0.67 <= s <= 1.5):
                raise HTTPException(422, "automatic height anchors disagree or imply an unsafe scale change")

        # 2. rasters (DTM never touched)
        ndsm = ndsm_raw * s
        dsm = dsm_raw + (s - 1) * ndsm_raw
        _rewrite_tif(folder / "ndsm.tif", ndsm)
        _rewrite_tif(folder / "dsm.tif", dsm)
        unc = None
        if (folder / "ensemble_spread_raw.tif").exists():
            # scenes with a calibrated error bar: rescale the raw spread, keep it
            # for the viewer's reliability layer, and derive the exported sigma
            from depthwizard.uncertainty import calibrated_sigma
            unc = read_raster(folder / "ensemble_spread_raw.tif")[0] * s
            _rewrite_tif(folder / "ensemble_spread.tif", unc)
            _rewrite_tif(folder / "uncertainty.tif", calibrated_sigma(unc))
        elif (folder / "uncertainty_raw.tif").exists():
            unc = read_raster(folder / "uncertainty_raw.tif")[0] * s
            _rewrite_tif(folder / "uncertainty.tif", unc)

        # 3. buildings – same ids/footprints, heights scaled
        from depthwizard.roof_fit import rescale_roof_fit
        for b in bj["buildings"]:
            b["height_m"] = round(b["height_raw_m"] * s, 2)
            b["roof_elevation_m"] = round(b["ground_elevation_m"] + b["height_m"], 2)
            b["storeys"] = max(1, round(b["height_m"] / 3.0))
            if b.get("volume_raw_m3") is not None:
                b["volume_m3"] = round(b["volume_raw_m3"] * s, 1)
            if b.get("roof_fit"):
                rescale_roof_fit(b["roof_fit"], s)

        # 4. viewer layers + meta
        vm = json.loads((folder / "viewer" / "meta.json").read_text())
        _viewer_bin(folder, "height.bin", dsm, vm)
        if unc is not None and vm.get("layers", {}).get("unc"):
            _viewer_bin(folder, "unc.bin", unc, vm)
        if unc is not None and vm.get("has_confidence"):
            _viewer_bin(folder, "confidence.bin", np.exp(-np.maximum(unc, 0) / 2.0), vm)
        vm["h_min"], vm["h_max"] = float(np.nanmin(dsm)), float(np.nanmax(dsm))
        from depthwizard.io import save_preview
        save_preview(folder / "preview.png", dsm, gsd=float(vm["gsd_m"]))

        # A height rescale changes validation. Recompute against the same
        # independent reference when it is still available, or remove stale
        # scores rather than displaying the previous surface's accuracy.
        validation = None
        image_path = _find_original(folder, vm, "image")
        reference_path = _find_original(folder, vm, "reference", near=image_path.parent if image_path else None)
        if reference_path:
            from depthwizard.io import match_grid, read_image
            from depthwizard.metrics import evaluate, reference_on_grid, building_level
            if image_path is not None and image_path.is_file():
                image = match_grid(read_image(image_path), dsm.shape)
                reference_grid = reference_on_grid(reference_path, image)
                validation = evaluate(dsm, reference_grid, "metre", rgb=image.rgb, gsd=float(vm["gsd_m"]))
                prior_path = folder / "metrics.json"
                if prior_path.is_file():
                    prior = json.loads(prior_path.read_text(encoding="utf-8"))
                    for key in ("baseline_dem", "baseline_edge_gradient_rmse"):
                        if key in prior:
                            validation[key] = prior[key]
                if bj["count"] >= 5:
                    bm = building_level(labels, ndsm, reference_grid, float(vm["gsd_m"]))
                    if bm:
                        validation["buildings"] = bm
                prior_path.write_text(json.dumps(validation, indent=2, default=float), encoding="utf-8")
        if validation is None:
            (folder / "metrics.json").unlink(missing_ok=True)
            vm["has_reference"] = False
            vm["validation_plot"] = None
        else:
            vm["has_reference"] = True
            if (folder / "viewer" / "ref.bin").is_file():
                rv = np.fromfile(folder / "viewer" / "ref.bin", dtype="<f4")
                pv = np.fromfile(folder / "viewer" / "height.bin", dtype="<f4")
                valid = np.flatnonzero(np.isfinite(rv) & np.isfinite(pv))
                if valid.size:
                    pick = np.random.default_rng(0).choice(valid, size=min(2500, valid.size), replace=False)
                    err = pv[valid] - rv[valid]
                    limit = float(np.percentile(np.abs(err), 99)) or 1.0
                    hist, edges = np.histogram(np.clip(err, -limit, limit), bins=41, range=(-limit, limit))
                    vm["validation_plot"] = {"pred": np.round(pv[pick], 2).tolist(),
                                             "ref": np.round(rv[pick], 2).tolist(),
                                             "hist": hist.tolist(), "edges": np.round(edges, 3).tolist()}

        # 5. solar per roof (optional, cheap)
        try:
            from depthwizard.analysis import roof_solar
            corners = vm.get("corners_lonlat")
            lat = float(np.mean([c[1] for c in corners])) if corners else 22.0
            sol = roof_solar(dsm, labels, vm["gsd_m"], lat_deg=lat)
            for b in bj["buildings"]:
                b.update(sol.get(b["id"], {}))
        except Exception:
            pass
        bpath.write_text(json.dumps(bj, indent=2), encoding="utf-8")

        # 6. calibration record in both meta files
        anchor_rec = None if body.reset else {"s": s, "anchors": normalized_anchors, "stats": stats,
                                              "source": "automatic" if body.automatic else "supplied"}
        for mp in (folder / "meta.json", folder / "viewer" / "meta.json"):
            m = vm if mp.parent.name == "viewer" else json.loads(mp.read_text())
            if validation is None:
                m.pop("metrics", None)
                m["validation_note"] = "Reference scores unavailable after rescale; upload a reference to validate this result."
            else:
                m["metrics"] = validation
                m.pop("validation_note", None)
            cal = m.setdefault("calibration", {})
            if "base" not in cal:
                base_cal = {k: v for k, v in cal.items() if k != "base"}
                if old_anchor:
                    previous_k = base_cal.get("scale_k")
                    if previous_k is not None:
                        base_cal["scale_k"] = float(previous_k) / old_scale
                    base_cal["method"] = "pre-anchor baseline (legacy)"
                    base_cal["scale_source"] = "earlier calibration details unavailable"
                    base_cal["evidence_level"] = "provisional"
                cal["base"] = base_cal
            if body.reset:
                base = cal["base"]
                cal.clear()
                cal.update(base)
                m.pop("height_anchor", None)
                if "auto_anchors" in m:
                    m["auto_anchors"]["applied"] = False
            else:
                cal["method"] = "height-anchor"
                cal["scale_source"] = (f"{stats.get('n_used', 0)} automatic shadow/OSM estimate(s)" if body.automatic
                                       else f"{stats.get('n_used', 0)} supplied building height(s)")
                cal["evidence_level"] = "provisional" if body.automatic else ("measured" if stats.get("n_used", 0) >= 2 else "provisional")
                base_k = cal["base"].get("scale_k")
                cal["scale_k"] = float(base_k) * s if base_k is not None else None
                m["height_anchor"] = anchor_rec
                if "auto_anchors" in m:
                    m["auto_anchors"]["applied"] = bool(body.automatic)
            if "evidence_bundle" in m:
                m["evidence_bundle"]["calibration_method"] = cal["method"]
                m["evidence_bundle"]["height_anchor_s"] = None if body.reset else s
                m["evidence_bundle"]["scale_k"] = cal.get("scale_k")
            mp.write_text(json.dumps(m, indent=2, default=float))

        # 7. re-export derived files
        # CityJSON, PLY, meshes and zips are rebuilt on demand by their download
        # endpoints; deleting the stale copies keeps rescale fast.
        for stale in (folder / "exports").glob("*") if (folder / "exports").is_dir() else ():
            if stale.is_file():
                stale.unlink(missing_ok=True)

        if body.reset:   # restore raws exactly, then drop them
            for n in ("ndsm", "dsm", "uncertainty", "ensemble_spread"):
                raw = folder / f"{n}_raw.tif"
                if raw.exists():
                    shutil.move(raw, folder / f"{n}.tif")

    return {"scale": s, **stats}


app.include_router(create_mission_router(_completed_scene, _files_lock))
app.mount("/jobs", StaticFiles(directory=JOBS), name="jobs")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
