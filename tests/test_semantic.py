import json

import numpy as np
import pytest
from PIL import Image
from rasterio.transform import Affine

from depthwizard.buildings import extract_buildings
from depthwizard.semantic import BUILDING, TREE, label_mapping


def scene():
    dsm = np.zeros((64, 64), np.float32)
    dsm[8:24, 8:24] = 10
    dsm[36:54, 36:54] = 15
    rgb = np.full((64, 64, 3), 100, np.uint8)
    return dsm, rgb


def test_named_labels_and_unknown_classes():
    assert label_mapping({0: "Ignore", 2: "Building", 6: "Forest"}) == {0: 0, 2: 1, 6: 2}
    with pytest.raises(ValueError, match="named"):
        label_mapping({0: "LABEL_0", 1: "LABEL_1"})


def test_semantics_remove_brown_tree_and_preserve_heights():
    dsm, rgb = scene()
    labels = np.zeros(dsm.shape, np.uint8)
    labels[8:24, 8:24] = BUILDING
    labels[36:54, 36:54] = TREE
    original = dsm.copy()
    baseline = extract_buildings(dsm, dtm=np.zeros_like(dsm), rgb=rgb, roof_fitting=False)
    refined = extract_buildings(dsm, dtm=np.zeros_like(dsm), rgb=rgb,
                                semantic_labels=labels, roof_fitting=False)
    assert baseline["count"] == 2 and refined["count"] == 1
    assert refined["buildings"][0]["height_m"] == 10
    assert np.array_equal(dsm, original)


def test_cleaning_does_not_fill_known_road_through_roof():
    dsm, rgb = scene()
    labels = np.zeros(dsm.shape, np.uint8)
    labels[8:24, 15] = 5  # road gap which binary closing would otherwise fill
    refined = extract_buildings(dsm, dtm=np.zeros_like(dsm), rgb=rgb,
                                semantic_labels=labels, return_labels=True, roof_fitting=False)
    assert not refined['_labels'][8:24, 15].any()


def test_green_roof_is_not_rejected_and_unknown_keeps_baseline():
    dsm, rgb = scene()
    rgb[8:24, 8:24] = (80, 160, 80)
    unknown = np.zeros(dsm.shape, np.uint8)
    baseline = extract_buildings(dsm, dtm=np.zeros_like(dsm), rgb=rgb, roof_fitting=False)
    unchanged = extract_buildings(dsm, dtm=np.zeros_like(dsm), rgb=rgb,
                                  semantic_labels=unknown, roof_fitting=False)
    assert baseline["count"] == unchanged["count"] == 1
    unknown[8:24, 8:24] = BUILDING
    rescued = extract_buildings(dsm, dtm=np.zeros_like(dsm), rgb=rgb,
                                semantic_labels=unknown, roof_fitting=False)
    assert rescued["count"] == 2
    with pytest.raises(ValueError, match="grid"):
        extract_buildings(dsm, semantic_labels=unknown[:20])


def test_viewer_labels_are_categorical_and_stale_file_removed(tmp_path):
    from depthwizard.io import InputImage, export_viewer_assets
    dsm, rgb = scene()
    image = InputImage(rgb=rgb, path=tmp_path / "rgb.png", georeferenced=False,
                       crs=None, transform=Affine.identity(), pixel_size_m=1)
    labels = np.zeros(dsm.shape, np.uint8)
    labels[:, :32] = TREE
    labels[:, 32:] = BUILDING
    export_viewer_assets(tmp_path, image, dsm, {}, mesh_max=16, semantic_labels=labels)
    raw = np.fromfile(tmp_path / "semantic.bin", np.uint8)
    assert raw.size == 256 and set(raw) == {BUILDING, TREE}
    assert json.loads((tmp_path / "meta.json").read_text())["layers"]["semantic"]
    export_viewer_assets(tmp_path, image, dsm, {}, mesh_max=16)
    assert not (tmp_path / "semantic.bin").exists()


def test_classifier_metrics_count_unknown_predictions_as_misses():
    from scripts.compare_semantic import classification_metrics
    truth = np.array([[1, 1, 2], [2, 255, 0]], np.uint8)
    pred = np.array([[1, 0, 2], [2, 1, 1]], np.uint8)
    metrics = classification_metrics(pred, truth)
    assert metrics["valid_pixels"] == 4
    assert metrics["classes"]["building"]["recall"] == 0.5
    assert metrics["classified_fraction"] == 0.75
    assert metrics["pixel_accuracy"] == 0.75


def test_tiled_inference_preserves_shape_and_boundary_classes():
    import torch
    from types import SimpleNamespace
    from depthwizard.semantic import SemanticSegmenter

    class Batch(dict):
        def to(self, _device):
            return self

    def processor(images, return_tensors):
        return Batch(pixel_values=torch.from_numpy(images.copy()).permute(2, 0, 1)[None].float())

    class Model:
        config = SimpleNamespace(id2label={0: "Ignore", 1: "Building", 2: "Forest"})
        def __call__(self, pixel_values):
            red = pixel_values[:, 0]
            logits = torch.stack([(red == 0).float(), (red == 10).float(), (red == 20).float()], dim=1) * 20
            return SimpleNamespace(logits=logits)

    segmenter = SemanticSegmenter.__new__(SemanticSegmenter)
    segmenter.model, segmenter.processor = Model(), processor
    segmenter.checkpoint, segmenter.device = "missing-local-prototype", "cpu"
    segmenter.mapping = label_mapping(segmenter.model.config.id2label)
    segmenter.threshold, segmenter.tile_size, segmenter.halo = 0.6, 128, 16
    rgb = np.zeros((133, 207, 3), np.uint8)
    rgb[:100, :120, 0] = 10
    rgb[100:, 120:, 0] = 20
    labels, _ = segmenter.predict(rgb)
    assert labels.shape == rgb.shape[:2]
    assert (labels[:100, :120] == BUILDING).all()
    assert (labels[100:, 120:] == TREE).all()
    assert labels[120, 5] == 0
