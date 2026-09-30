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


# Fine-tuned overhead (AGL) checkpoints output height in "network pixels":
# on held-out GAMUS validation tiles, metres ≈ C_PIXEL_HEIGHT × raw ×
# (ground metres per network-input pixel). Fitted on 23 GAMUS *validation*
# tiles (median 0.674, IQR 0.52–0.76); independent DC LiDAR check gives
# 0.86–1.13, so treat it as a ±40 % prior that DEM/GCP evidence overrides.
C_PIXEL_HEIGHT = 0.674
TRAIN_NET_GSD_M = 0.65          # GAMUS: 1024 px × 0.33 m seen at 518 px
LEARNED_SCALE_RANGE = (0.35, 2.0)  # network-pixel sizes where the prior is trusted


def is_overhead_agl(name: str) -> bool:
    """True for checkpoints fine-tuned to predict above-ground height."""
    import json
    p = Path(str(name))
    cfg = p / "config.json"
    if cfg.exists():
        try:
            if json.loads(cfg.read_text()).get("depthwizard_target") == "agl":
                return True
        except Exception:  # noqa: BLE001
            pass
    return "gamus" in str(name).lower()


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
        self.agl = is_overhead_agl(name)
        self.info: dict = {}
        # speed options: batching is numerically identical; fp16 is opt-in
        # (set DEPTHWIZARD_FP16=1) until it is re-benchmarked on the GPU.
        self.batch_tta = os.environ.get("DEPTHWIZARD_BATCH_TTA", "1") != "0"
        self.fp16 = os.environ.get("DEPTHWIZARD_FP16", "0") == "1"

    def _forward(self, images: list[np.ndarray]) -> tuple[list[np.ndarray], float]:
        """One batched forward pass for same-shape images. Returns per-image
        predictions resized to the input size, and the original-to-network
        pixel factor of the first image."""
        torch = self.torch
        inputs = self.processor(images=[Image.fromarray(x) for x in images], return_tensors="pt").to(self.device)
        net_h = int(inputs["pixel_values"].shape[-2])
        use_fp16 = self.fp16 and self.device == "cuda"
        with torch.inference_mode():
            if use_fp16:
                with torch.autocast("cuda", dtype=torch.float16):
                    pred = self.model(**inputs).predicted_depth
            else:
                pred = self.model(**inputs).predicted_depth      # B x h x w
        pred = torch.nn.functional.interpolate(pred.float()[:, None], size=images[0].shape[:2],
                                               mode="bicubic", align_corners=False)
        outs = [pred[i, 0].cpu().numpy() for i in range(pred.shape[0])]
        return outs, images[0].shape[0] / max(net_h, 1)

    def _infer_once(self, rgb: np.ndarray) -> tuple[np.ndarray, float]:
        outs, f = self._forward([rgb])
        return outs[0], f

    def _infer(self, rgb: np.ndarray, tta: int = 1, return_std: bool = False):
        """Test-time augmentation over rotations (and flips when tta=8).
        Nadir imagery has no preferred orientation, so disagreement between
        the passes is a direct per-pixel uncertainty estimate.

        Passes with the same image shape are run as one batch (all of them for
        square tiles); on a CUDA out-of-memory error it falls back to one pass
        at a time."""
        variants = [(k, False) for k in range(4)][:max(1, min(tta, 4))]
        if tta >= 8:
            variants += [(k, True) for k in range(4)]
        prepared = []
        for k, flip in variants:
            x = np.rot90(rgb, k)
            if flip:
                x = x[:, ::-1]
            prepared.append(np.ascontiguousarray(x))
        groups: dict[tuple, list[int]] = {}
        for i, x in enumerate(prepared):
            groups.setdefault(x.shape, []).append(i)
        raw_out: list = [None] * len(prepared)
        factor = 1.0
        for idx in groups.values():
            batch = [prepared[i] for i in idx]
            try:
                preds, f = self._forward(batch) if self.batch_tta else (None, None)
            except Exception as exc:  # noqa: BLE001 - CUDA OOM → sequential
                if "out of memory" not in str(exc).lower():
                    raise
                self.torch.cuda.empty_cache()
                preds = None
            if preds is None:
                preds, f = [], None
                for x in batch:
                    p, fx = self._forward([x])
                    preds.append(p[0]); f = f or fx
            for i, p in zip(idx, preds):
                raw_out[i] = p
            if 0 in idx:
                factor = f
        outs = []
        for (k, flip), d in zip(variants, raw_out):
            if flip:
                d = d[:, ::-1]
            outs.append(np.rot90(d, -k))
        stack = np.stack(outs)
        mean = stack.mean(0)
        self._last_factor = factor
        if return_std:
            return mean, (stack.std(0) if len(outs) > 1 else np.zeros_like(mean))
        return mean

    def predict_agl_metric(self, rgb: np.ndarray, gsd: float, tta: int = 1):
        """AGL checkpoints: tile the scene so the network sees roughly the
        ground resolution it was trained at, and convert each tile to metres
        with the learned pixel-footprint scale. No tile affine re-alignment is
        needed because every tile is already in (approximate) metres."""
        h, w = rgb.shape[:2]
        T = int(np.clip(round(TRAIN_NET_GSD_M * 518 / gsd), 256, 4096))
        if T >= max(h, w):
            raw, std = self._infer(rgb, tta=tta, return_std=True)
            net_gsd = gsd * self._last_factor
            return C_PIXEL_HEIGHT * net_gsd * raw, C_PIXEL_HEIGHT * net_gsd * std, net_gsd
        overlap = T // 4
        acc = np.zeros((h, w), np.float64)
        vacc = np.zeros((h, w), np.float64)
        wsum = np.zeros((h, w), np.float64)
        ramp = _feather(T, overlap)
        net_gsd = gsd
        for y0 in range(0, max(h - overlap, 1), T - overlap):
            for x0 in range(0, max(w - overlap, 1), T - overlap):
                y1, x1 = min(y0 + T, h), min(x0 + T, w)
                y0a, x0a = max(0, y1 - T), max(0, x1 - T)
                d, dstd = self._infer(rgb[y0a:y1, x0a:x1], tta=tta, return_std=True)
                net_gsd = gsd * self._last_factor
                ms = C_PIXEL_HEIGHT * net_gsd
                d = d - np.percentile(d, 1.0)      # each tile's ground level to zero
                wt = ramp[: y1 - y0a, : x1 - x0a]
                acc[y0a:y1, x0a:x1] += ms * d * wt
                vacc[y0a:y1, x0a:x1] += ms * dstd * wt
                wsum[y0a:y1, x0a:x1] += wt
        return (acc / np.maximum(wsum, 1e-9)).astype(np.float32), \
            (vacc / np.maximum(wsum, 1e-9)).astype(np.float32), net_gsd

    def predict(self, rgb: np.ndarray, tile: int = 1024, overlap: int = 256,
                global_size: int = 1024, tta: int = 1, gsd: float | None = None) -> np.ndarray:
        """Normalised relative height in [0, 1]. Raw-unit bookkeeping for
        metric scale and the TTA uncertainty are stored in ``self.info``."""
        self.info = {}
        h, w = rgb.shape[:2]
        if self.agl and gsd:
            metric, mstd, net_gsd = self.predict_agl_metric(rgb, gsd, tta=tta)
            lo, hi = np.nanpercentile(metric, (0.5, 99.5))
            span = float(max(hi - lo, 1e-9))
            trusted = LEARNED_SCALE_RANGE[0] <= net_gsd <= LEARNED_SCALE_RANGE[1]
            self.info = {"raw_span": span, "net_factor": None, "tta": int(tta), "agl": True,
                         "net_gsd_m": float(net_gsd), "metric_direct": True,
                         "learned_trusted": bool(trusted),
                         "std_rel": (mstd / span).astype(np.float32)}
            return np.clip((metric - lo) / span, 0, 1).astype(np.float32)
        s = min(1.0, global_size / max(h, w))
        small = rgb if s == 1.0 else np.asarray(
            Image.fromarray(rgb).resize((int(w * s), int(h * s)), Image.LANCZOS))
        coarse, cstd = self._infer(small, tta=tta, return_std=True)
        # original-image pixels per network-input pixel, for the global pass
        net_factor = self._last_factor / s
        up = lambda a: np.asarray(Image.fromarray(a.astype(np.float32)).resize((w, h), Image.BICUBIC))
        if s == 1.0 or max(h, w) <= tile:
            raw, std = (coarse, cstd) if s == 1.0 else (up(coarse), up(cstd))
        else:
            coarse, cstd = up(coarse), up(cstd)
            acc = np.zeros((h, w), np.float64)
            vacc = np.zeros((h, w), np.float64)
            wsum = np.zeros((h, w), np.float64)
            step = tile - overlap
            ramp = _feather(tile, overlap)
            for y0 in range(0, max(h - overlap, 1), step):
                for x0 in range(0, max(w - overlap, 1), step):
                    y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
                    y0a, x0a = max(0, y1 - tile), max(0, x1 - tile)
                    d, dstd = self._infer(rgb[y0a:y1, x0a:x1], tta=tta, return_std=True)
                    a, b = _affine_fit(d, coarse[y0a:y1, x0a:x1])
                    wt = ramp[: y1 - y0a, : x1 - x0a]
                    acc[y0a:y1, x0a:x1] += (a * d + b) * wt
                    vacc[y0a:y1, x0a:x1] += (abs(a) * dstd) * wt
                    wsum[y0a:y1, x0a:x1] += wt
            raw = (acc / np.maximum(wsum, 1e-9)).astype(np.float32)
            std = (vacc / np.maximum(wsum, 1e-9)).astype(np.float32)
        lo, hi = np.nanpercentile(raw, (0.5, 99.5))
        span = float(max(hi - lo, 1e-9))
        self.info = {"raw_span": span, "net_factor": float(net_factor), "tta": int(tta),
                     "agl": self.agl, "std_rel": (std / span).astype(np.float32)}
        return np.clip((raw - lo) / span, 0, 1).astype(np.float32)

    def metres_per_unit(self, gsd_m: float) -> float | None:
        """Learned metric scale for one normalised unit (AGL checkpoints)."""
        if not self.agl or not self.info:
            return None
        if self.info.get("metric_direct"):
            return self.info["raw_span"] if self.info.get("learned_trusted") else None
        net_gsd = gsd_m * self.info["net_factor"]
        if not (LEARNED_SCALE_RANGE[0] <= net_gsd <= LEARNED_SCALE_RANGE[1]):
            return None
        return C_PIXEL_HEIGHT * net_gsd * self.info["raw_span"]


# ---- model cache: loading weights takes seconds and VRAM churn, so keep up
# to two backbones (e.g. GAMUS + pretrained for comparisons) alive between jobs.
import threading as _threading
_BACKBONES: dict = {}
_BB_LOCK = _threading.RLock()
_MAX_CACHED = 2


def get_backbone(model: str = "small", device: str | None = None) -> "DepthBackbone":
    key = (str(model), device)
    with _BB_LOCK:
        bb = _BACKBONES.get(key)
        if bb is None:
            while len(_BACKBONES) >= _MAX_CACHED:
                old = _BACKBONES.pop(next(iter(_BACKBONES)))
                try:
                    if getattr(old, "device", "") == "cuda":
                        del old
                        import torch
                        torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass
            bb = DepthBackbone(model, device)
            _BACKBONES[key] = bb
        return bb


def clear_backbone_cache() -> None:
    with _BB_LOCK:
        _BACKBONES.clear()


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


def heuristic_relative_height(rgb: np.ndarray) -> np.ndarray:
    """OFFLINE FALLBACK ONLY – used when torch/model weights are unavailable
    so the rest of the pipeline and viewer can still be exercised.
    Crude cue: bright, locally-contrasting surfaces tend to be roofs; dark
    regions adjacent to them tend to be shadow/ground. Not for evaluation."""
    g = rgb.astype(np.float32).mean(-1) / 255.0
    local = g - ndimage.gaussian_filter(g, 25)
    broad = ndimage.gaussian_filter(g, 60)
    return normalise(ndimage.median_filter(0.7 * local + 0.3 * broad, 5))


def relative_height(rgb: np.ndarray, model: str = "small", allow_fallback: bool = True,
                    device: str | None = None, tta=1, return_uncertainty: bool = False,
                    return_info: bool = False, gsd: float | None = None):
    """Relative height in [0, 1].

    Returns (rel, name), plus a normalised uncertainty map when
    ``return_uncertainty`` is set, plus an info dict when ``return_info`` is
    set. ``tta`` is the number of rotation/flip passes (True means 4).
    info: agl flag, net_gsd_m, tta, std_rel (TTA spread in normalised units),
    learned_scale(gsd) -> metres per normalised unit or None.
    """
    passes = 4 if tta is True else (1 if not tta else int(tta))
    try:
        bb = get_backbone(model, device)
        with _BB_LOCK:          # one prediction at a time per cached model
            rel = bb.predict(rgb, tta=passes, gsd=gsd)
            info = dict(bb.info)
            snap = dict(bb.info)
            def learned_scale(g, _bb=bb, _snap=snap):
                keep = _bb.info
                _bb.info = _snap
                try:
                    return _bb.metres_per_unit(g)
                finally:
                    _bb.info = keep
            info["learned_scale"] = learned_scale
        name = bb.name
    except Exception as exc:  # noqa: BLE001
        if not allow_fallback:
            raise
        print(f"[depthwizard] depth backbone unavailable ({exc.__class__.__name__}: {exc}); "
              "using heuristic fallback – NOT for evaluation")
        rel = heuristic_relative_height(rgb)
        name = "heuristic-fallback"
        info = {"agl": False, "tta": 0, "std_rel": None, "learned_scale": lambda g: None}
    out = [rel, name]
    if return_uncertainty:
        std = info.get("std_rel")
        if std is None:  # proxy: local high-frequency energy
            std = np.abs(rel - ndimage.gaussian_filter(rel, 2.0))
        out.append(normalise(std))
    if return_info:
        out.append(info)
    return tuple(out)
