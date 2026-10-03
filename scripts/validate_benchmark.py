"""Preflight a scene-disjoint benchmark and optionally score frozen GeoTIFFs.

No model inference, fitting, registration, resampling, or reference-derived
selection is performed. Paths are relative to the JSON manifest. Metadata-only
preflight uses the Python standard library; --score needs project dependencies.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
SPLITS = {"development", "blind_test", "diagnostic"}
INPUT_ROLES = {"image", "calibration_dem", "gcp", "anchors"}
REFERENCE_ROLES = {"reference", "reference_labels", "reference_ndsm"}
ASSET_ROLES = INPUT_ROLES | REFERENCE_ROLES | {"prediction", "semantic_prediction", "uncertainty"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protected_hashes():
    provenance = ROOT / "samples" / "dc_lidar" / "provenance.json"
    if not provenance.is_file():
        return set()
    data = json.loads(provenance.read_text(encoding="utf-8"))
    return {scene["files"]["lidar_dsm_2024.tif"]["sha256"]
            for scene in data.get("scenes", {}).values()
            if "lidar_dsm_2024.tif" in scene.get("files", {})}


def is_protected(path, digest, protected):
    parts = [part.lower() for part in path.resolve().parts]
    named = path.name.lower() == "lidar_dsm_2024.tif" and "dc_lidar" in parts
    return named or str(digest).lower() in protected


def concrete(value):
    return (isinstance(value, str) and bool(value.strip())
            and not any(token in value.upper() for token in ("REPLACE", "TODO", "UNKNOWN", "<", ">")))


def box(value):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("bbox_wgs84 must be [west,south,east,north]")
    west, south, east, north = map(float, value)
    if not all(math.isfinite(v) for v in (west, south, east, north)):
        raise ValueError("bounds must be finite")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("invalid WGS84 bounds; split antimeridian scenes first")
    return west, south, east, north


def overlaps(a, b, buffer_m):
    # Conservative local buffer; reject polar scenes rather than claiming an audit.
    latitude = max(abs(a[1]), abs(a[3]), abs(b[1]), abs(b[3]))
    if latitude >= 85:
        raise ValueError("spatial audit above 85 degrees requires a projected audit")
    dy = buffer_m / 110000.0
    dx = dy / math.cos(math.radians(latitude))
    return not (a[2] + dx < b[0] - dx or b[2] + dx < a[0] - dx
                or a[3] + dy < b[1] - dy or b[3] + dy < a[1] - dy)


def validate(data, base, check_files=False):
    errors, warnings = [], []
    protected = protected_hashes()
    assets_seen = {}
    diagnostic_only = data.get("benchmark_kind", "independent") == "diagnostic"

    def require(condition, message):
        if not condition:
            errors.append(message)

    def asset(item, label, role=None, split=None):
        if not isinstance(item, dict):
            errors.append(f"{label}: asset must be an object")
            return
        require(concrete(item.get("path")), f"{label}: concrete path required")
        require(bool(SHA256.fullmatch(str(item.get("sha256", "")))), f"{label}: SHA-256 required")
        path = (base / str(item.get("path", ""))).resolve()
        digest = str(item.get("sha256", "")).lower()
        reserved = is_protected(path, digest, protected)
        if reserved:
            require(role in REFERENCE_ROLES, f"{label}: DC 2024 DSM is scoring-only; cannot be an input")
            require(split == "diagnostic", f"{label}: DC references were historically exposed; use diagnostic split only")
        if role in REFERENCE_ROLES:
            require(item.get("usage") == "scoring_only", f"{label}: usage must be scoring_only")
        if role in INPUT_ROLES or role in REFERENCE_ROLES:
            for key in ("source_url", "license_id", "license_url", "acquisition_evidence"):
                require(concrete(item.get(key)), f"{label}: {key} required")
            acquisition = item.get("acquisition", {})
            try:
                require(date.fromisoformat(acquisition["start"]) <= date.fromisoformat(acquisition["end"]),
                        f"{label}: acquisition range is reversed")
            except (KeyError, TypeError, ValueError):
                if diagnostic_only and split == "diagnostic" and acquisition.get("status") == "unresolved":
                    warnings.append(f"{label}: acquisition dates unresolved; diagnostic only")
                else:
                    errors.append(f"{label}: acquisition start/end must be ISO dates; ranges permit uncertain dates")
        if role in INPUT_ROLES | REFERENCE_ROLES:
            assets_seen.setdefault(digest, []).append((label, role, split))
        if check_files:
            require(path.is_file(), f"{label}: file missing: {path}")
            if path.is_file() and SHA256.fullmatch(digest):
                require(sha256(path) == digest, f"{label}: SHA-256 mismatch")

    require(data.get("schema_version") == 1, "schema_version must be 1")
    require(data.get("benchmark_kind", "independent") in {"independent", "diagnostic"},
            "benchmark_kind must be independent or diagnostic")
    require(concrete(data.get("benchmark_id")), "benchmark_id required")
    protocol = data.get("protocol", {})
    target = protocol.get("height_target")
    require(target in {"DSM", "nDSM"}, "height_target must be DSM or nDSM (metres)")
    require(protocol.get("selection_split") == "development", "selection_split must be development")
    require(protocol.get("reference_policy") == "scoring_only", "reference_policy must be scoring_only")
    try:
        buffer_m = float(protocol["scene_buffer_m"])
        require(math.isfinite(buffer_m) and buffer_m >= 0, "scene_buffer_m must be finite and >=0")
        threshold = float(protocol["tall_threshold_m"])
        require(math.isfinite(threshold) and threshold > 0, "tall_threshold_m must be finite and >0")
    except (KeyError, TypeError, ValueError):
        buffer_m = 0
        errors.append("protocol requires numeric scene_buffer_m and tall_threshold_m")
    freeze = data.get("freeze", {})
    try:
        frozen_at = datetime.fromisoformat(freeze["at"].replace("Z", "+00:00"))
        require(frozen_at.tzinfo is not None, "freeze.at must include a timezone")
    except (KeyError, TypeError, ValueError):
        errors.append("freeze.at must be an ISO timestamp with timezone")
    for key in ("model_artifact", "configuration"):
        asset(freeze.get(key), f"freeze.{key}")
    model = freeze.get("model_artifact", {})
    for key in ("revision", "source_url", "license_id", "license_url"):
        require(concrete(model.get(key)), f"freeze.model_artifact: {key} required")

    audit = data.get("training_audit", {})
    if not diagnostic_only:
        require(audit.get("status") == "complete", "training_audit must be complete; unknown overlap cannot establish independence")
    elif audit.get("status") != "complete":
        warnings.append("Training audit incomplete; diagnostic does not establish independent validation")
    require(concrete(audit.get("evidence")), "training_audit.evidence required (include base and semantic checkpoints)")
    require(isinstance(audit.get("datasets"), list) and bool(audit.get("datasets")), "training_audit.datasets inventory required")
    for index, dataset in enumerate(audit.get("datasets", [])):
        if not isinstance(dataset, dict):
            errors.append(f"training dataset {index}: requires id, source_url, splits, checkpoint")
            continue
        for key in ("id", "source_url", "checkpoint"):
            require(concrete(dataset.get(key)), f"training dataset {index}: {key} required")
        require(isinstance(dataset.get("splits"), list) and bool(dataset.get("splits")),
                f"training dataset {index}: splits inventory required")
        if isinstance(dataset.get("splits"), list):
            require(all(concrete(item) for item in dataset["splits"]),
                    f"training dataset {index}: split inventory has unresolved placeholders")
    excluded = audit.get("excluded_scene_groups", [])
    require(isinstance(excluded, list), "training_audit.excluded_scene_groups must be a list")
    train_boxes = []
    for index, region in enumerate(audit.get("known_training_regions", [])):
        try:
            train_boxes.append(box(region["bbox_wgs84"]))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"training region {index}: {exc}")
    require("known_training_regions" in audit, "training_audit.known_training_regions required; empty needs justification in evidence")

    scenes = data.get("scenes", [])
    require(isinstance(scenes, list) and bool(scenes), "scenes list required")
    groups, ids, spatial = {}, set(), []
    for scene in scenes:
        sid, split, group = scene.get("id"), scene.get("split"), scene.get("scene_group")
        label = f"scene {sid}"
        require(concrete(sid) and sid not in ids, f"{label}: unique concrete id required")
        ids.add(sid)
        require(split in SPLITS, f"{label}: invalid split")
        if diagnostic_only:
            require(split == "diagnostic", f"{label}: diagnostic benchmark may contain only diagnostic scenes")
        require(concrete(group), f"{label}: scene_group required (all crops from one survey/AOI share it)")
        groups.setdefault(str(group), set()).add(split)
        try:
            bounds = box(scene.get("bbox_wgs84"))
            spatial.append((str(sid), split, bounds))
            for bounds_train in train_boxes:
                require(split == "diagnostic" or not overlaps(bounds, bounds_train, buffer_m),
                        f"{label}: overlaps a known training region")
        except (TypeError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
        require(scene.get("training_overlap") == "audited_none" or split == "diagnostic",
                f"{label}: training_overlap must be audited_none for development/blind_test")
        require(concrete(scene.get("overlap_evidence")), f"{label}: overlap_evidence required")
        require(isinstance(scene.get("prior_reference_use"), list), f"{label}: prior_reference_use list required")
        if split == "blind_test":
            require(not scene.get("prior_reference_use"), f"{label}: prior reference use invalidates blind_test")
            require(group in excluded, f"{label}: scene group absent from training exclusion inventory")
        if split == "diagnostic":
            warnings.append(f"{label}: diagnostic only; exclude from independent benchmark aggregate")
        require(concrete(scene.get("change_review")), f"{label}: image/reference acquisition change review required")
        assets = scene.get("assets", {})
        for role in ("image", "prediction", "reference"):
            require(role in assets, f"{label}: {role} required")
        for role, item in assets.items():
            require(role in ASSET_ROLES, f"{label}: unknown asset role {role}; use explicit supported roles")
            asset(item, f"{label}.{role}", role, split)
        for role in ("prediction", "reference"):
            item = assets.get(role, {})
            require(item.get("height_type") == target and item.get("units") == "metre",
                    f"{label}.{role}: target must be {target} in metre")
            require(concrete(item.get("vertical_datum")), f"{label}.{role}: explicit vertical_datum required")
            require("same as" not in str(item.get("vertical_datum", "")).lower()
                    and str(item.get("vertical_datum", "")).lower() != "relative",
                    f"{label}.{role}: inherited/relative datum does not establish absolute compatibility")
            require(concrete(item.get("datum_evidence")), f"{label}.{role}: datum_evidence required")
        pred, ref = assets.get("prediction", {}), assets.get("reference", {})
        require(pred.get("vertical_datum") == ref.get("vertical_datum"),
                f"{label}: prediction/reference datum differs; transform independently before scoring")
        if target == "nDSM":
            require(ref.get("vertical_datum") == "AGL", f"{label}: nDSM datum must be AGL")
        if "reference_labels" in assets:
            require(assets["reference_labels"].get("label_schema") == "depthwizard_canonical_v1",
                    f"{label}: labels must use depthwizard_canonical_v1")
        if "uncertainty" in assets:
            require(assets["uncertainty"].get("units") == "metre", f"{label}: uncertainty must be sigma in metres")
            require(concrete(assets["uncertainty"].get("calibration_provenance")),
                    f"{label}: uncertainty calibration provenance required; DC-fitted sigma is provisional")
    for group, splits in groups.items():
        require(len(splits) == 1, f"scene group {group}: appears in multiple splits")
    for index, (sid, split, bounds) in enumerate(spatial):
        for other, other_split, other_bounds in spatial[index + 1:]:
            if {split, other_split} == {"development", "blind_test"}:
                try:
                    require(not overlaps(bounds, other_bounds, buffer_m), f"{sid}/{other}: development and test footprints overlap or violate buffer")
                except ValueError as exc:
                    errors.append(str(exc))
    for digest, uses in assets_seen.items():
        roles = {role for _, role, _ in uses}
        splits = {split for _, _, split in uses}
        require(not (roles & INPUT_ROLES and roles & REFERENCE_ROLES), f"asset {digest}: scoring reference reused as calibration/inference input")
        require(not {"development", "blind_test"} <= splits, f"asset {digest}: reused across development and blind_test")
    if not diagnostic_only:
        require(any(s.get("split") == "development" for s in scenes), "at least one development scene required")
        require(any(s.get("split") == "blind_test" for s in scenes), "at least one blind_test scene required")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "scene_count": len(scenes), "files_checked": check_files,
            "independent_validation": not diagnostic_only and not errors,
            "audit_limit": "Checks declarations and supplied footprints; cannot prove unreported training provenance or past reference access."}


def read_grid(path, anchor=None, categorical=False):
    import numpy as np
    import rasterio
    with rasterio.open(path) as source:
        if source.count != 1 or source.crs is None:
            raise ValueError(f"{path}: expected single-band georeferenced GeoTIFF")
        grid = (source.shape, source.crs, source.transform)
        if anchor is not None and grid != anchor:
            raise ValueError(f"{path}: exact grid differs; register/resample independently before freeze")
        array = source.read(1, masked=True)
        if categorical:
            values = array.filled(255)
        else:
            values = array.astype(np.float64).filled(np.nan)
    return values, grid


def score(data, base, split):
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds
    from depthwizard.metrics import _core
    from scripts.compare_semantic import classification_metrics
    rows = []
    for scene in data["scenes"]:
        if scene["split"] != split:
            continue
        assets = scene["assets"]
        path = lambda role: base / assets[role]["path"]
        pred, grid = read_grid(path("prediction"))
        ref, _ = read_grid(path("reference"), grid)
        with rasterio.open(path("image")) as image:
            if image.crs is None:
                raise ValueError(f"{scene['id']}: input image must be georeferenced for spatial audit")
            actual = transform_bounds(image.crs, "EPSG:4326", *image.bounds, densify_pts=21)
            declared = box(scene["bbox_wgs84"])
            if not np.allclose(actual, declared, rtol=0, atol=1e-5):
                raise ValueError(f"{scene['id']}: declared bbox does not match image extent (tolerance 1e-5 degree)")
            with rasterio.open(path("prediction")) as prediction:
                output_bounds = transform_bounds(prediction.crs, "EPSG:4326", *prediction.bounds, densify_pts=21)
            if not np.allclose(actual, output_bounds, rtol=0, atol=1e-5):
                raise ValueError(f"{scene['id']}: prediction does not cover the declared image extent")
        valid = np.isfinite(pred) & np.isfinite(ref)
        metrics = _core(pred, ref)
        if metrics is None:
            raise ValueError(f"{scene['id']}: fewer than 10 valid scoring pixels")
        metrics["p95_abs_error_m"] = float(np.percentile(np.abs(pred[valid] - ref[valid]), 95))
        result = {"id": scene["id"], "split": split, "height_target": data["protocol"]["height_target"],
                  "metrics": metrics, "valid_fraction": float(valid.mean()), "reference_fit": False}
        labels = None
        if "reference_labels" in assets:
            labels, _ = read_grid(path("reference_labels"), grid, categorical=True)
            if not np.isin(labels, (0, 1, 2, 3, 4, 5, 255)).all():
                raise ValueError(f"{scene['id']}: invalid canonical label codes")
            if "semantic_prediction" in assets:
                predicted_labels, _ = read_grid(path("semantic_prediction"), grid, categorical=True)
                if not np.isin(predicted_labels, (0, 1, 2, 3, 4, 5, 255)).all():
                    raise ValueError(f"{scene['id']}: invalid predicted label codes")
                predicted_labels = np.where(predicted_labels == 255, 0, predicted_labels)
                result["semantic"] = classification_metrics(predicted_labels, labels)
            result["height_by_reference_class"] = {}
            for name, code in (("building", 1), ("woody_canopy", 2)):
                mask = valid & (labels == code)
                result["height_by_reference_class"][name] = _core(pred[mask], ref[mask])
        if data["protocol"]["height_target"] == "nDSM":
            ref_height = ref
        elif "reference_ndsm" in assets:
            item = assets["reference_ndsm"]
            if item.get("height_type") != "nDSM" or item.get("units") != "metre" or item.get("vertical_datum") != "AGL":
                raise ValueError("reference_ndsm must be independently prepared AGL nDSM in metres")
            ref_height, _ = read_grid(path("reference_ndsm"), grid)
        else:
            ref_height = None
        if ref_height is not None:
            mask = valid & np.isfinite(ref_height) & (ref_height >= float(data["protocol"]["tall_threshold_m"]))
            result["tall_objects"] = _core(pred[mask], ref[mask])
            if labels is not None:
                result["tall_buildings"] = _core(pred[mask & (labels == 1)], ref[mask & (labels == 1)])
                result["tall_canopy"] = _core(pred[mask & (labels == 2)], ref[mask & (labels == 2)])
        if "uncertainty" in assets:
            sigma, _ = read_grid(path("uncertainty"), grid)
            mask = valid & np.isfinite(sigma) & (sigma > 0)
            if mask.any():
                ratio = np.abs(pred[mask] - ref[mask]) / sigma[mask]
                result["uncertainty"] = {"pixels": int(mask.sum()), "includes_bias": True,
                                         "within_1_sigma": float((ratio <= 1).mean()),
                                         "within_2_sigma": float((ratio <= 2).mean()), "fit": False}
            else:
                result["uncertainty"] = {"pixels": 0, "status": "no valid positive sigma"}
        rows.append(result)
    if not rows:
        raise ValueError(f"No scenes in requested scoring split {split}")
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value
    return clean({"benchmark_id": data["benchmark_id"], "freeze": data["freeze"], "split": split,
                  "independent_validation": data.get("benchmark_kind", "independent") != "diagnostic" and split == "blind_test",
                  "scenes": rows, "aggregation": "Per-scene metrics; no pixel pooling or fitted alignment"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--check-files", action="store_true", help="verify SHA-256 of every declared file")
    parser.add_argument("--score", action="store_true", help="score frozen outputs; implies --check-files")
    parser.add_argument("--split", choices=sorted(SPLITS), default="blind_test")
    parser.add_argument("--out", type=Path, help="save validation report or scores as JSON")
    args = parser.parse_args()
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = validate(data, args.manifest.resolve().parent, args.check_files or args.score)
        if report["valid"] and args.score:
            report["scores"] = score(data, args.manifest.resolve().parent, args.split)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as exc:
        report = {"valid": False, "errors": [str(exc)]}
    output = json.dumps(report, indent=2, allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
