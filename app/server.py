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
    kwargs = dict(image_path=_save(image, inputs), out_dir=str(folder),
                  dem=_save(dem, inputs), reference=_save(reference, inputs),
                  gcp=_save(gcp, inputs), model=model, scene=scene, assumed_gsd_m=gsd,
                  fetch_dem=fetch_dem, dem_source=dem_source,
                  tta=max(1, min(8, int(tta))), dem_kind=dem_kind if dem_kind in ("auto", "surface", "terrain") else "auto",
                  match_dem_30m=match_dem_30m,
                  sun_elevation=float(sun_elevation) if sun_elevation.strip() else None,
                  sun_azimuth=float(sun_azimuth) if sun_azimuth.strip() else None)
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


class RescaleBody(BaseModel):
    anchors: list[dict] = []
    reset: bool = False

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

@app.post("/api/scenes/{job_id}/rescale")
def rescale(job_id: str, body: RescaleBody):
    from depthwizard.calibrate import apply_height_anchors
    from depthwizard.io import read_raster
    folder = _completed_scene(job_id)
    if not (folder / "ndsm.tif").exists() or not (folder / "dtm.tif").exists():
        raise HTTPException(400, "height anchors need a metric scene (dsm + dtm + ndsm)")
    with _lock:
        # 1. snapshot originals once – every rescale starts from these
        for n in ("ndsm", "dsm", "uncertainty"):
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
            s, stats = apply_height_anchors(ndsm_raw, labels, body.anchors, est)

        # 2. rasters (DTM never touched)
        ndsm = ndsm_raw * s
        dsm = dsm_raw + (s - 1) * ndsm_raw
        _rewrite_tif(folder / "ndsm.tif", ndsm)
        _rewrite_tif(folder / "dsm.tif", dsm)
        unc = None
        if (folder / "uncertainty_raw.tif").exists():
            unc = read_raster(folder / "uncertainty_raw.tif")[0] * s
            _rewrite_tif(folder / "uncertainty.tif", unc)

        # 3. buildings – same ids/footprints, heights scaled
        for b in bj["buildings"]:
            b["height_m"] = round(b["height_raw_m"] * s, 2)
            b["roof_elevation_m"] = round(b["ground_elevation_m"] + b["height_m"], 2)
            b["storeys"] = max(1, round(b["height_m"] / 3.0))
            if b.get("volume_raw_m3") is not None:
                b["volume_m3"] = round(b["volume_raw_m3"] * s, 1)

        # 4. viewer layers + meta
        vm = json.loads((folder / "viewer" / "meta.json").read_text())
        _viewer_bin(folder, "height.bin", dsm, vm)
        if unc is not None and vm.get("layers", {}).get("unc"):
            _viewer_bin(folder, "unc.bin", unc, vm)
        vm["h_min"], vm["h_max"] = float(np.nanmin(dsm)), float(np.nanmax(dsm))

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
        anchor_rec = None if body.reset else {"s": s, "anchors": body.anchors, "stats": stats}
        for mp in (folder / "meta.json", folder / "viewer" / "meta.json"):
            m = vm if mp.parent.name == "viewer" else json.loads(mp.read_text())
            cal = m.setdefault("calibration", {})
            cal.setdefault("base", {k: v for k, v in cal.items() if k != "base"})
            if body.reset:
                base = cal["base"]
                cal.clear()
                cal.update(base)
                m.pop("height_anchor", None)
            else:
                cal["method"] = "height-anchor"
                cal["scale_source"] = f"{stats.get('n_used', 0)} known building height(s)"
                cal["evidence_level"] = "measured" if stats.get("n_used", 0) >= 2 else "provisional"
                m["height_anchor"] = anchor_rec
            if "evidence_bundle" in m:
                m["evidence_bundle"]["calibration_method"] = cal["method"]
                m["evidence_bundle"]["height_anchor_s"] = None if body.reset else s
            mp.write_text(json.dumps(m, indent=2, default=float))

        # 7. re-export derived files
        from depthwizard.exports import export_cityjson, export_ply
        (folder / "exports").mkdir(exist_ok=True)
        export_cityjson(folder, folder / "exports" / "buildings.city.json")
        export_ply(folder, folder / "exports" / "points.ply")

        if body.reset:   # restore raws exactly, then drop them
            for n in ("ndsm", "dsm", "uncertainty"):
                raw = folder / f"{n}_raw.tif"
                if raw.exists():
                    shutil.move(raw, folder / f"{n}.tif")

    return {"scale": s, **stats}


app.mount("/jobs", StaticFiles(directory=JOBS), name="jobs")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
