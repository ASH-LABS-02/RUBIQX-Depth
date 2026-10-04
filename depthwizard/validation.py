"""Unfitted, stratified scoring for development and frozen evaluation receipts."""
from __future__ import annotations

import numpy as np

HEIGHT_BANDS = ((0, 2.5), (2.5, 15), (15, 30), (30, float("inf")))


def errors(pred, truth, mask=None):
    valid = np.isfinite(pred) & np.isfinite(truth)
    if mask is not None:
        valid &= mask
    e = (np.asarray(pred) - np.asarray(truth))[valid].astype(np.float64)
    return {"n": int(e.size), "rmse": float(np.sqrt(np.mean(e * e))),
            "mae": float(np.mean(np.abs(e))), "bias": float(e.mean())} if e.size else {"n": 0}


def stratified(pred, truth, *, agl=None, labels=None, sigma=None, route="unknown"):
    if np.shape(pred) != np.shape(truth):
        raise ValueError("Prediction and truth must share the exact grid")
    result = {"all": errors(pred, truth), "route": route, "fitted_to_reference": False}
    if agl is not None:
        if np.shape(agl) != np.shape(truth):
            raise ValueError("AGL grid differs from truth")
        result["height_bands_m"] = {f"{lo:g}-{hi:g}": errors(pred, truth, (agl >= lo) & (agl < hi))
                                    for lo, hi in HEIGHT_BANDS}
    if labels is not None:
        if np.shape(labels) != np.shape(truth):
            raise ValueError("Class label grid differs from truth")
        result["classes"] = {str(int(k)): errors(pred, truth, labels == k)
                             for k in np.unique(labels) if k not in (0, 255)}
    if sigma is not None:
        if np.shape(sigma) != np.shape(truth):
            raise ValueError("Uncertainty grid differs from truth")
        valid = np.isfinite(pred) & np.isfinite(truth) & np.isfinite(sigma) & (sigma > 0)
        masks = {"all": valid}
        if labels is not None:
            masks.update({f"class-{int(k)}": valid & (labels == k) for k in np.unique(labels) if k not in (0, 255)})
        if agl is not None:
            masks.update({f"height-{lo:g}-{hi:g}": valid & (agl >= lo) & (agl < hi) for lo, hi in HEIGHT_BANDS})
        result["uncertainty_coverage"] = {key: {"n": int(mask.sum()),
            "one_sigma": float(np.mean(np.abs(pred[mask] - truth[mask]) <= sigma[mask])),
            "two_sigma": float(np.mean(np.abs(pred[mask] - truth[mask]) <= 2 * sigma[mask]))}
            for key, mask in masks.items() if mask.any()}
    return result


def categorical(pred, truth, classes=(1, 2, 3, 4, 5)):
    if np.shape(pred) != np.shape(truth):
        raise ValueError("Categorical grids differ")
    valid = (truth != 0) & (truth != 255)
    result = {}
    for k in classes:
        tp = int((valid & (pred == k) & (truth == k)).sum())
        fp = int((valid & (pred == k) & (truth != k)).sum())
        fn = int((valid & (pred != k) & (truth == k)).sum())
        result[str(k)] = {"tp": tp, "fp": fp, "fn": fn,
            "iou": tp / (tp + fp + fn) if tp + fp + fn else None,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}
    return result
