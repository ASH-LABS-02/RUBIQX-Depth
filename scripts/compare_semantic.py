"""Audit segmentation on a cached DSM, without reading or fitting reference heights."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depthwizard import io as dio
from depthwizard.buildings import extract_buildings
from depthwizard.semantic import SemanticSegmenter, PALETTE, CLASSES


def classification_metrics(pred, truth):
    if pred.shape != truth.shape:
        raise ValueError("Reference labels must share the image grid; no automatic registration or resampling")
    valid = np.isin(truth, (1, 2, 3, 4, 5))
    if not valid.any():
        raise ValueError("Reference needs canonical labels 1..5; 0/255 are ignored")
    scores = {}
    for label in range(1, 6):
        p, t = (pred == label) & valid, (truth == label) & valid
        tp, fp, fn = int((p & t).sum()), int((p & ~t).sum()), int((~p & t).sum())
        scores[CLASSES[label]] = {"truth_pixels": int(t.sum()),
            "iou": tp / (tp + fp + fn) if tp + fp + fn else None,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}
    return {"valid_pixels": int(valid.sum()), "classified_fraction": float((pred[valid] != 0).mean()),
            "pixel_accuracy": float((pred[valid] == truth[valid]).mean()), "classes": scores}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-dir", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-labels", type=Path, help="independent canonical class PNG/TIFF; optional")
    args = parser.parse_args()
    if args.out.resolve() == args.scene_dir.resolve():
        parser.error("Output must be separate from the original scene")
    args.out.mkdir(parents=True, exist_ok=True)
    with rasterio.open(args.scene_dir / "dsm.tif") as src:
        dsm, profile = src.read(1), src.profile.copy()
    grid_source = "full-resolution cached DSM/DTM"
    if (args.scene_dir / "dtm.tif").is_file():
        with rasterio.open(args.scene_dir / "dtm.tif") as src:
            dtm = src.read(1)
    else:
        vm = json.loads((args.scene_dir / "viewer" / "meta.json").read_text())
        shape = (vm["grid_h"], vm["grid_w"])
        dsm = np.fromfile(args.scene_dir / "viewer" / "height.bin", "<f4").reshape(shape)
        dtm = np.fromfile(args.scene_dir / "viewer" / "dtm.bin", "<f4").reshape(shape)
        grid_source = "cached display DSM/DTM; full-resolution DTM unavailable"
    if dtm.shape != dsm.shape:
        parser.error("Cached DSM and DTM grids must match")
    img = dio.match_grid(dio.read_image(args.image), dsm.shape)
    profile.update(height=dsm.shape[0], width=dsm.shape[1], transform=img.transform, crs=img.crs)
    gsd = img.pixel_size_m or 1.0
    segmenter = SemanticSegmenter(args.model)
    labels, info = segmenter.predict(img.rgb, log=print)
    kwargs = dict(dtm=dtm, gsd=gsd, rgb=img.rgb, return_labels=True,
                  edge_refine_for_footprints=True, world_w=dsm.shape[1] * gsd,
                  world_h=dsm.shape[0] * gsd)
    baseline = extract_buildings(dsm, **kwargs)
    refined = extract_buildings(dsm, semantic_labels=labels, **kwargs)
    base_mask, new_mask = baseline.pop("_labels") > 0, refined.pop("_labels") > 0
    stats = {"scene": args.scene_dir.name, "segmentation": info,
             "baseline_buildings": baseline["count"], "semantic_buildings": refined["count"],
             "baseline_footprint_m2": baseline["total_footprint_m2"],
             "semantic_footprint_m2": refined["total_footprint_m2"],
             "height_changes": False, "grid_source": grid_source,
             "accuracy_status": "not measured: no independent class labels",
             "reference_height_access": "none"}
    if args.reference_labels:
        truth = np.array(Image.open(args.reference_labels))
        stats["classification_metrics"] = classification_metrics(labels, truth)
        # Baseline has only a building mask. Score its building class on the same valid pixels.
        stats["baseline_building_metrics"] = classification_metrics(base_mask.astype(np.uint8), truth)["classes"]["building"]
        stats["accuracy_status"] = "classification scored; model training overlap must be checked separately"
    (args.out / "comparison.json").write_text(json.dumps(stats, indent=2))
    profile.update(count=1, dtype="uint8", nodata=0)
    with rasterio.open(args.out / "semantic.tif", "w", **profile) as dst:
        dst.write(labels, 1)
        dst.update_tags(CLASSES=json.dumps(CLASSES), SOURCE="experimental classification; not height truth")
    profile.update(dtype="float32", nodata=np.nan)
    for name, values in (("dsm", dsm), ("dtm", dtm)):
        with rasterio.open(args.out / f"{name}.tif", "w", **profile) as dst:
            dst.write(values.astype(np.float32), 1)
            dst.update_tags(SOURCE=grid_source, HEIGHT_EFFECT="unchanged cached values")
    Image.fromarray(labels).save(args.out / "semantic-labels.png")
    panels = [img.rgb.copy(), img.rgb.copy(), img.rgb.copy()]
    for image, mask in zip(panels[:2], (base_mask, new_mask)):
        image[mask] = (0.55 * image[mask] + 0.45 * np.array([255, 173, 52])).astype(np.uint8)
    known = labels > 0
    panels[2][known] = (0.45 * panels[2][known] + 0.55 * PALETTE[labels[known]]).astype(np.uint8)
    sheet = Image.new("RGB", (1536, 558), "#0c1420")
    draw = ImageDraw.Draw(sheet)
    for i, (panel, title) in enumerate(zip(panels, (
            f"Height + RGB: {baseline['count']} candidates", f"Semantic + height: {refined['count']} candidates",
            "Classes: roofs amber / forest green / water blue"))):
        sheet.paste(Image.fromarray(panel).resize((512, 512)), (i * 512, 34))
        draw.text((i * 512 + 10, 10), title, fill="white")
    sheet.save(args.out / "comparison.png")
    meta = json.loads((args.scene_dir / "meta.json").read_text())
    meta.update(semantic_segmentation=info, buildings_count=refined["count"],
                total_footprint_m2=refined["total_footprint_m2"],
                input_paths={"image": str(args.image.resolve())}, input=args.image.name,
                experiment={"type": "semantic audit", "source_scene": args.scene_dir.name,
                            "grid_source": grid_source, "height_values": "unchanged"})
    # These cached records contain old building IDs, scores or unavailable layers.
    for key in ("metrics", "vs_copernicus", "auto_anchors", "evidence_bundle",
                "analytics", "uncertainty_calibration", "confidence_definition", "timing_s"):
        meta.pop(key, None)
    dio.export_viewer_assets(args.out / "viewer", img, dsm, meta, dtm=dtm,
                            buildings=refined, semantic_labels=labels)
    (args.out / "meta.json").write_text(json.dumps(meta))
    print(json.dumps({k: v for k, v in stats.items() if k != "segmentation"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
