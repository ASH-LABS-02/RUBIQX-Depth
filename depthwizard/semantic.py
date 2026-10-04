"""Optional overhead-image segmentation. Classification never changes DSM heights.

Unknown pixels retain the existing detector. Scores are model softmax scores,
not calibrated accuracy probabilities. No reference elevation enters inference.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

UNKNOWN, BUILDING, TREE, GROUND, WATER, ROAD = range(6)
CLASSES = {0: "unknown", 1: "building", 2: "tree/forest candidate",
           3: "ground/low vegetation", 4: "water", 5: "road"}
PALETTE = np.array([[100, 110, 120], [245, 173, 52], [28, 108, 60],
                    [162, 161, 122], [49, 127, 205], [202, 207, 215]], np.uint8)


def label_mapping(id2label):
    aliases = {"building": BUILDING, "buildings": BUILDING,
               "tree": TREE, "trees": TREE, "forest": TREE,
               "barren": GROUND, "agricultural": GROUND, "agriculture": GROUND,
               "ground": GROUND, "low vegetation": GROUND,
               "water": WATER, "road": ROAD, "roads": ROAD}
    mapping = {int(k): aliases.get(str(v).strip().lower(), UNKNOWN)
               for k, v in id2label.items()}
    if not any(v in (BUILDING, TREE) for v in mapping.values()):
        raise ValueError("Semantic checkpoint needs named building or tree/forest labels; generic LABEL_n IDs are unsupported.")
    return mapping


class SemanticSegmenter:
    def __init__(self, checkpoint, *, device=None, threshold=0.6, tile_size=512, halo=64):
        if os.environ.get('DEPTHWIZARD_PRODUCTION') == '1':
            provenance_path = Path(checkpoint) / 'provenance.json'
            provenance = json.loads(provenance_path.read_text()) if provenance_path.is_file() else {}
            if not (provenance.get('license_verified') is True and provenance.get('license_url')
                    and provenance.get('training_overlap_verified') is True):
                raise ValueError('Semantic checkpoint licence and training provenance are unresolved; keep it disabled in production')
        import torch
        from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation

        if not 0.5 <= threshold <= 1 or tile_size < 128 or not 0 <= halo < tile_size // 2:
            raise ValueError("Invalid segmentation threshold or tile/halo size")
        self.checkpoint = str(checkpoint)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.threshold, self.tile_size, self.halo = threshold, tile_size, halo
        self.processor = SegformerImageProcessor.from_pretrained(checkpoint)
        self.model = SegformerForSemanticSegmentation.from_pretrained(
            checkpoint, trust_remote_code=False, weights_only=True).to(self.device).eval()
        self.mapping = label_mapping(self.model.config.id2label)

    def predict(self, rgb, *, log=lambda *_: None):
        import torch
        import torch.nn.functional as functional

        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8 or not all(rgb.shape[:2]):
            raise ValueError("Semantic input must be uint8 RGB, H x W x 3")
        height, width = rgb.shape[:2]
        result = np.zeros((height, width), np.uint8)
        core = self.tile_size - 2 * self.halo
        padded = np.pad(rgb, ((self.halo, self.halo + self.tile_size),
                             (self.halo, self.halo + self.tile_size), (0, 0)), mode="edge")
        count = 0
        score_sum = 0.0
        for row in range(0, height, core):
            for col in range(0, width, core):
                tile = padded[row:row + self.tile_size, col:col + self.tile_size]
                th, tw = tile.shape[:2]
                inputs = self.processor(images=tile, return_tensors="pt").to(self.device)
                with torch.inference_mode():
                    logits = self.model(**inputs).logits
                    logits = functional.interpolate(logits, size=(th, tw), mode="bilinear", align_corners=False)
                    scores, indices = logits.softmax(dim=1).max(dim=1)
                scores = scores[0].cpu().numpy()
                indices = indices[0].cpu().numpy()
                lookup = np.array([self.mapping.get(i, UNKNOWN) for i in range(logits.shape[1])], np.uint8)
                labels = lookup[indices]
                labels[scores < self.threshold] = UNKNOWN
                rh, cw = min(core, height - row), min(core, width - col)
                crop = np.s_[self.halo:self.halo + rh, self.halo:self.halo + cw]
                result[row:row + rh, col:col + cw] = labels[crop]
                score_sum += float(scores[crop].sum())
                count += 1
                log(f"  semantic tile {count}")
        provenance_path = Path(self.checkpoint) / "provenance.json"
        provenance = json.loads(provenance_path.read_text()) if provenance_path.is_file() else {}
        info = {"status": "experimental", "checkpoint": self.checkpoint,
                "publisher": provenance, "threshold": self.threshold, "tile_size": self.tile_size,
                "halo": self.halo, "classes": CLASSES, "tiles": count,
                "source_labels": self.model.config.id2label,
                "mean_softmax_score": round(score_sum / (height * width), 4),
                "class_pixels": {CLASSES[k]: int((result == k).sum()) for k in CLASSES},
                "height_effect": "none; masks refine object extraction and display only",
                "score_definition": "uncalibrated class softmax; not height reliability"}
        return result, info
