"""Relative-height extraction with a pre-trained monocular depth backbone.

Default backbone: Depth Anything V2 (Hugging Face transformers). Any
checkpoint with the same interface works, including one fine-tuned on
GAMUS with scripts/finetune_gamus.py.

Satellite scenes are often much larger than the network input (518 px), so
we run a two-pass scheme:
  1. a global pass on the downsampled scene gives consistent low-frequency
     structure;
  2. overlapping full-resolution tiles give fine detail (rooftops, trees);
     each tile's arbitrary affine (scale/shift) is re-aligned to the global
     pass by least squares, then tiles are blended with feathered weights.

Depth Anything outputs relative inverse depth (larger = closer to camera).
For a near-nadir satellite view "closer" means "higher", so the output is
used directly as relative height.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

MODELS = {
    "small": "depth-anything/Depth-Anything-V2-Small-hf",
    "base": "depth-anything/Depth-Anything-V2-Base-hf",
    "large": "depth-anything/Depth-Anything-V2-Large-hf",
}


class DepthBackbone:
    def __init__(self, model: str = "small", device: str | None = None):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        name = MODELS.get(model, model)  # alias or HF id / local path
        cache = Path(os.environ.get("DEPTHWIZARD_MODEL_CACHE",
                                    Path(__file__).resolve().parents[1] / "models" / "cache"))
        cache.mkdir(parents=True, exist_ok=True)
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available()
                                 else "mps" if torch.backends.mps.is_available() else "cpu")
        self.processor = AutoImageProcessor.from_pretrained(name, cache_dir=cache)
        self.model = AutoModelForDepthEstimation.from_pretrained(name, cache_dir=cache).to(self.device).eval()
        self.name = name

    def _infer(self, rgb: np.ndarray) -> np.ndarray:
        torch = self.torch
        inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt").to(self.device)
        with torch.no_grad():
            pred = self.model(**inputs).predicted_depth  # 1 x h x w
        pred = torch.nn.functional.interpolate(pred[:, None], size=rgb.shape[:2],
                                               mode="bicubic", align_corners=False)
        return pred[0, 0].float().cpu().numpy()

    def _predict_single(self, rgb: np.ndarray, tile: int = 1024, overlap: int = 256,
                        global_size: int = 1024) -> np.ndarray:
        h, w = rgb.shape[:2]
        # Pass 1 – global context
        s = min(1.0, global_size / max(h, w))
        small = rgb if s == 1.0 else np.asarray(
            Image.fromarray(rgb).resize((int(w * s), int(h * s)), Image.LANCZOS))
        coarse = self._infer(small)
        if s == 1.0 or max(h, w) <= tile:
            return normalise(np.asarray(Image.fromarray(coarse).resize((w, h), Image.BICUBIC)))
        coarse = np.asarray(Image.fromarray(coarse).resize((w, h), Image.BICUBIC))

        # Pass 2 – aligned, feathered full-resolution tiles
        acc = np.zeros((h, w), np.float64)
        wsum = np.zeros((h, w), np.float64)
        step = tile - overlap
        ramp = _feather(tile, overlap)
        for y0 in range(0, max(h - overlap, 1), step):
            for x0 in range(0, max(w - overlap, 1), step):
                y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
                y0a, x0a = max(0, y1 - tile), max(0, x1 - tile)
                d = self._infer(rgb[y0a:y1, x0a:x1])
                a, b = _affine_fit(d, coarse[y0a:y1, x0a:x1])
                wt = ramp[: y1 - y0a, : x1 - x0a]
                acc[y0a:y1, x0a:x1] += (a * d + b) * wt
                wsum[y0a:y1, x0a:x1] += wt
        return normalise((acc / np.maximum(wsum, 1e-9)).astype(np.float32))

    def predict(self, rgb: np.ndarray, tile: int = 1024, overlap: int = 256,
                global_size: int = 1024, tta: bool = False) -> np.ndarray:
        pred, _ = self.predict_with_uncertainty(rgb, tile=tile, overlap=overlap,
                                                global_size=global_size, tta=tta)
        return pred

    def predict_with_uncertainty(self, rgb: np.ndarray, tile: int = 1024, overlap: int = 256,
                                 global_size: int = 1024, tta: bool = False) -> tuple[np.ndarray, np.ndarray]:
        base = self._predict_single(rgb, tile=tile, overlap=overlap, global_size=global_size)
        if not tta:
            # Fast proxy for uncertainty from local high-frequency gradients
            local_diff = np.abs(base - ndimage.gaussian_filter(base, 2.0))
            unc = normalise(local_diff)
            return base, unc

        # TTA: test-time augmentation (original, horizontal flip, vertical flip, rot180)
        variants = [
            (lambda x: x, lambda x: x),                              # identity
            (lambda x: np.fliplr(x), lambda x: np.fliplr(x)),        # hflip
            (lambda x: np.flipud(x), lambda x: np.flipud(x)),        # vflip
            (lambda x: np.rot90(x, 2), lambda x: np.rot90(x, -2)),  # rot180
        ]
        aligned_preds = [base]
        for fwd, inv in variants[1:]:
            aug_rgb = np.ascontiguousarray(fwd(rgb))
            pred_aug = self._predict_single(aug_rgb, tile=tile, overlap=overlap, global_size=global_size)
            pred_back = inv(pred_aug)
            a, b = _affine_fit(pred_back, base)
            aligned_preds.append(a * pred_back + b)

        stack = np.stack(aligned_preds, axis=0)
        mean_pred = normalise(np.mean(stack, axis=0).astype(np.float32))
        uncertainty = np.std(stack, axis=0).astype(np.float32)
        unc_norm = normalise(uncertainty)
        return mean_pred, unc_norm


def _feather(tile: int, overlap: int) -> np.ndarray:
    r = np.ones(tile, np.float32)
    if overlap > 0:
        ramp = np.linspace(0.05, 1, overlap, dtype=np.float32)
        r[:overlap] = ramp
        r[-overlap:] = ramp[::-1]
    return np.outer(r, r)


def _affine_fit(src: np.ndarray, dst: np.ndarray) -> tuple[float, float]:
    x, y = src.ravel(), dst.ravel()
    A = np.stack([x, np.ones_like(x)], 1)
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    return float(a), float(b)


def normalise(d: np.ndarray) -> np.ndarray:
    lo, hi = np.nanpercentile(d, (0.5, 99.5))
    return np.clip((d - lo) / max(hi - lo, 1e-9), 0, 1).astype(np.float32)


def heuristic_relative_height(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """OFFLINE FALLBACK ONLY – used when torch/model weights are unavailable
    so the rest of the pipeline and viewer can still be exercised.
    Crude cue: bright, locally-contrasting surfaces tend to be roofs; dark
    regions adjacent to them tend to be shadow/ground. Not for evaluation."""
    g = rgb.astype(np.float32).mean(-1) / 255.0
    local = g - ndimage.gaussian_filter(g, 25)
    broad = ndimage.gaussian_filter(g, 60)
    pred = normalise(ndimage.median_filter(0.7 * local + 0.3 * broad, 5))
    unc = np.full_like(pred, 0.5, dtype=np.float32)
    return pred, unc


def relative_height(rgb: np.ndarray, model: str = "small", allow_fallback: bool = True,
                    device: str | None = None, tta: bool = False,
                    return_uncertainty: bool = False) -> tuple[np.ndarray, str] | tuple[np.ndarray, str, np.ndarray]:
    try:
        bb = DepthBackbone(model, device)
        pred, unc = bb.predict_with_uncertainty(rgb, tta=tta)
        if return_uncertainty:
            return pred, bb.name, unc
        return pred, bb.name
    except Exception as exc:  # noqa: BLE001
        if not allow_fallback:
            raise
        print(f"[depthwizard] depth backbone unavailable ({exc.__class__.__name__}: {exc}); "
              "using heuristic fallback – NOT for evaluation")
        pred, unc = heuristic_relative_height(rgb)
        if return_uncertainty:
            return pred, "heuristic-fallback", unc
        return pred, "heuristic-fallback"
