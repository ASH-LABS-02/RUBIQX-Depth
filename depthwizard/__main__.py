"""Command line: python -m depthwizard IMAGE [options]"""
import argparse
import json
import os
from pathlib import Path

from .pipeline import run

ROOT = Path(__file__).resolve().parents[1]
TRAINING_ROOT = Path(os.environ.get("DEPTHWIZARD_TRAINING_ROOT", "D:/DepthWizard"))
if not TRAINING_ROOT.is_dir():
    TRAINING_ROOT = ROOT / "models"
CHECKPOINT_ERROR = "Model checkpoint not found. Put it in models/da2-gamus-full or set DEPTHWIZARD_CHECKPOINT."


def _checkpoint_ready(path):
    path = Path(path)
    return (path / "config.json").is_file() and any(
        (path / name).is_file() for name in ("model.safetensors", "pytorch_model.bin")
    )


def main(argv=None):
    p = argparse.ArgumentParser(prog="depthwizard",
                                description="Single-view DSM estimation from RGB satellite imagery")
    p.add_argument("image", help="PNG / JPG / TIFF / GeoTIFF")
    p.add_argument("-o", "--out", default="output", help="output folder")
    p.add_argument("--dem", help="low-res DEM GeoTIFF (SRTM 30 m, CartoDEM) for absolute scale")
    p.add_argument("--fetch-dem", dest="fetch_dem", action="store_true", default=True,
                   help="download Copernicus GLO-30 for georeferenced images (default)")
    p.add_argument("--no-fetch-dem", dest="fetch_dem", action="store_false",
                   help="skip automatic DEM download")
    p.add_argument("--cop-scale", dest="cop_scale", action="store_true", default=False,
                   help="fit above-ground height scale to Copernicus, fetching it even with a terrain DEM")
    p.add_argument("--no-cop-scale", dest="cop_scale", action="store_false",
                   help="skip Copernicus above-ground height-scale correction")
    p.add_argument("--no-auto-anchors", dest="auto_anchors", action="store_false", default=True,
                   help="disable automatic OSM/shadow anchors for a controlled benchmark")
    p.add_argument("--dem-source", default=None, choices=["COP30", "SRTMGL1"],
                   help="identify a supplied DEM; SRTM also enables OpenTopography fallback")
    p.add_argument("--gcp", help="CSV of ground control points (x,y,z or easting,northing,z)")
    p.add_argument("--gcp-height-type", default="orthometric",
                   choices=["orthometric", "ellipsoidal"],
                   help="GCP z datum; raw WGS84 GNSS heights convert to EGM2008")
    p.add_argument("--vertical-datum", help="declare the supplied height datum; does not transform raster heights")
    p.add_argument("--ref", help="reference DSM/LiDAR raster for validation")
    p.add_argument("--model", default=None,
                   help="pretrained | small | base | large | HF id | local fine-tuned checkpoint")
    p.add_argument("--scene", default="auto", choices=["auto", "urban", "sparse", "forest", "hilly"])
    p.add_argument("--gsd", type=float, default=1.0, help="assumed m/pixel for non-georeferenced input")
    p.add_argument("--device", help="cuda | mps | cpu")
    p.add_argument("--semantic-model", help="optional experimental overhead SegFormer checkpoint; refines object masks only")
    p.add_argument("--tta", type=int, default=4, choices=[1, 2, 4, 8],
                   help="rotation/flip test-time augmentation passes (uncertainty map needs >1)")
    p.add_argument("--dem-kind", default="auto", choices=["auto", "surface", "terrain"],
                   help="surface = DEM includes buildings/canopy (Copernicus, SRTM); terrain = bare earth")
    p.add_argument("--no-consistency", action="store_true",
                   help="do not force agreement with a surface DEM at its own resolution")
    p.add_argument("--sun-elevation", type=float, help="sun elevation (deg) for shadow calibration")
    p.add_argument("--sun-azimuth", type=float, help="sun azimuth (deg from north); estimated if omitted")
    p.add_argument("--anchor", action="append", help="ID:HEIGHT (e.g. 12:18.0) to set a known building height in metres")
    p.add_argument("--anchors", help="CSV with lon,lat,height_m to set known building heights")
    fallback = p.add_mutually_exclusive_group()
    fallback.add_argument("--allow-fallback", action="store_true",
                          help="allow heuristic output for prototype use only (never for evaluation)")
    fallback.add_argument("--no-fallback", action="store_true",
                          help="fail when the model is unavailable (the default)")
    a = p.parse_args(argv)
    
    anchors = []
    if a.anchor:
        for anc in a.anchor:
            b_id, h = anc.split(":")
            anchors.append({"building_id": int(b_id), "height_m": float(h)})
    if a.anchors:
        import csv
        with open(a.anchors, newline='') as f:
            for row in csv.DictReader(f):
                anchors.append({"lon": float(row["lon"]), "lat": float(row["lat"]), "height_m": float(row["height_m"])})
    if not anchors:
        anchors = None

    model = a.model
    if model is None:
        configured = os.environ.get("DEPTHWIZARD_CHECKPOINT")
        candidates = [configured] if configured else [
            ROOT / "models" / "da2-gamus-full",
            TRAINING_ROOT / "checkpoints" / "da2-gamus-full"]
        for path in (c for c in candidates if c):
            if _checkpoint_ready(path):
                model = str(path)
                print(f"using GAMUS checkpoint from {path}")
                break
        if model is None:
            p.error(CHECKPOINT_ERROR)
    elif model == "pretrained":
        model = "small"
    elif (Path(model).is_absolute() or Path(model).exists()
          or model.startswith(("models/", "models\\", "./", "../"))
          or (len(model) > 1 and model[1] == ":")) and not _checkpoint_ready(model):
        p.error(CHECKPOINT_ERROR)

    meta = run(a.image, a.out, dem=a.dem, gcp=a.gcp, reference=a.ref, model=model,
               scene=a.scene, fetch_dem=a.fetch_dem, dem_source=a.dem_source, assumed_gsd_m=a.gsd,
               allow_fallback=a.allow_fallback, device=a.device, tta=a.tta,
               dem_kind=a.dem_kind, match_dem_30m=not a.no_consistency,
               sun_elevation=a.sun_elevation, sun_azimuth=a.sun_azimuth, anchors=anchors,
               gcp_height_type=a.gcp_height_type, vertical_datum=a.vertical_datum,
               cop_scale=a.cop_scale, semantic_model=a.semantic_model, auto_anchors=a.auto_anchors)
    if "metrics" in meta:
        print(json.dumps(meta["metrics"], indent=2))


if __name__ == "__main__":
    main()
