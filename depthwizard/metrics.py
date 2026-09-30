"""Validation against a reference DSM (LiDAR / GAMUS nDSM / any raster).

Reports RMSE, MAE, bias, NMAD, Pearson r and threshold accuracy, both on the
raw metric output and after a least-squares affine alignment (the fair score
for a relative rDSM). Also breaks results down by landscape class so
stability across urban / sparse / hilly / forested terrain can be shown.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage


def reference_on_grid(ref_path, image) -> np.ndarray:
    import rasterio
    from rasterio.warp import reproject, Resampling

    h, w = image.shape
    with rasterio.open(ref_path) as src:
        if image.georeferenced and src.crs is not None:
            out = np.full((h, w), np.nan, np.float32)
            reproject(source=rasterio.band(src, 1), destination=out,
                      src_transform=src.transform, src_crs=src.crs,
                      src_nodata=src.nodata, dst_transform=image.transform,
                      dst_crs=image.crs, dst_nodata=np.nan,
                      resampling=Resampling.bilinear)
            return out
        arr = src.read(1).astype(np.float32)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
    if arr.shape != (h, w):
        arr = np.asarray(Image.fromarray(arr).resize((w, h), Image.BILINEAR))
    return arr


def _core(pred, ref):
    m = np.isfinite(pred) & np.isfinite(ref)
    p, r = pred[m].astype(np.float64), ref[m].astype(np.float64)
    if p.size < 10:
        return None
    e = p - r
    corr = float(np.corrcoef(p, r)[0, 1]) if p.std() > 0 and r.std() > 0 else float("nan")
    return {
        "n": int(p.size),
        "rmse": float(np.sqrt(np.mean(e ** 2))),
        "mae": float(np.mean(np.abs(e))),
        "bias": float(np.mean(e)),
        "nmad": float(1.4826 * np.median(np.abs(e - np.median(e)))),
        "r": corr,
        "within_1m": float(np.mean(np.abs(e) <= 1.0)),
        "within_2m": float(np.mean(np.abs(e) <= 2.0)),
        "within_5m": float(np.mean(np.abs(e) <= 5.0)),
    }


def affine_align(pred, ref):
    m = np.isfinite(pred) & np.isfinite(ref)
    A = np.stack([pred[m], np.ones(m.sum())], 1)
    (a, b), *_ = np.linalg.lstsq(A, ref[m], rcond=None)
    return a * pred + b


def classify_landscape(rgb: np.ndarray, ref: np.ndarray, gsd: float, tile_m: float = 100):
    """Heuristic per-tile landscape label from the reference surface + colour.
    urban  – raised (>2.5 m above ground) non-vegetated cover > 8 %
    forest – raised vegetated cover (excess-green) > 20 %
    hilly  – median terrain slope > 6 deg
    sparse – everything else
    """
    t = max(8, int(tile_m / gsd))
    h, w = ref.shape
    rgbf = rgb.astype(np.float32)
    veg = ((2 * rgbf[..., 1] - rgbf[..., 0] - rgbf[..., 2]) / (rgbf.sum(-1) + 1e-6)) > 0.05
    ground = _ground(ref, gsd)
    raised = (np.nan_to_num(ref, nan=0) - ground) > 2.5
    gy, gx = np.gradient(ground, gsd)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    labels = np.empty((int(np.ceil(h / t)), int(np.ceil(w / t))), dtype=object)
    for i, y in enumerate(range(0, h, t)):
        for j, x in enumerate(range(0, w, t)):
            sl = np.s_[y:y + t, x:x + t]
            built = np.mean(raised[sl] & ~veg[sl])
            trees = np.mean(raised[sl] & veg[sl])
            if trees > 0.20:
                labels[i, j] = "forest"
            elif built > 0.08:
                labels[i, j] = "urban"
            elif np.median(slope[sl]) > 6:
                labels[i, j] = "hilly"
            else:
                labels[i, j] = "sparse"
    return labels, t


def _ground(a: np.ndarray, gsd: float) -> np.ndarray:
    """Approximate bare-earth surface: morphological opening + smoothing."""
    a = np.nan_to_num(a, nan=float(np.nanmin(a)))
    size = max(3, int(60 / gsd))
    g = ndimage.grey_opening(a, size=(size, size))
    return ndimage.gaussian_filter(g, max(1, 15 / gsd))


def evaluate(pred: np.ndarray, ref: np.ndarray, units: str, rgb=None, gsd: float = 1.0,
             baseline: np.ndarray | None = None) -> dict:
    out = {"units": units}
    if units == "metre":
        out["absolute"] = _core(pred, ref)
        # A3: 30 m aggregated score matching the official Copernicus / SRTM scoring resolution
        block_size = max(1, int(round(30.0 / max(gsd, 0.05))))
        if block_size > 1 and min(pred.shape) >= block_size:
            pred_30m = ndimage.uniform_filter(pred, size=block_size)[::block_size, ::block_size]
            ref_30m = ndimage.uniform_filter(ref, size=block_size)[::block_size, ::block_size]
            agg = _core(pred_30m, ref_30m)
            if agg:
                out["aggregated_30m"] = agg
    if baseline is not None:  # e.g. SRTM resampled to the grid: what we must beat
        out["baseline_dem"] = _core(baseline, ref)
    aligned = affine_align(pred, ref)
    out["affine_aligned"] = _core(aligned, ref)
    # normalised DSM (height above local ground): structure recovery, terrain removed
    base = pred if units == "metre" else aligned
    out["structure_ndsm"] = _core(base - _ground(base, gsd), ref - _ground(ref, gsd))

    # Edge-detail agreement: compare gradient magnitude on the same grid.
    # Exclude a one-pixel boundary around nodata so filled values do not create
    # artificial edges.
    valid = np.isfinite(base) & np.isfinite(ref)
    safe = ndimage.binary_erosion(valid, structure=np.ones((3, 3), bool), border_value=0)
    if safe.sum() >= 10:
        p = np.where(valid, base, 0).astype(np.float32)
        r = np.where(valid, ref, 0).astype(np.float32)
        pg = np.hypot(*np.gradient(p, gsd))
        rg = np.hypot(*np.gradient(r, gsd))
        out["edge_gradient_rmse"] = float(np.sqrt(np.mean((pg[safe] - rg[safe]) ** 2)))
        if baseline is not None:
            b = np.where(np.isfinite(baseline), baseline, 0).astype(np.float32)
            bg = np.hypot(*np.gradient(b, gsd))
            out["baseline_edge_gradient_rmse"] = float(np.sqrt(np.mean((bg[safe] - rg[safe]) ** 2)))

    # Report errors by reference above-ground height, not absolute elevation.
    ground_ref = _ground(ref, gsd)
    height_ref = ref - ground_ref
    bands = (("<2 m", -np.inf, 2.0), ("2–5 m", 2.0, 5.0),
             ("5–15 m", 5.0, 15.0), ("≥15 m", 15.0, np.inf))
    by_height = {}
    for label, lower, upper in bands:
        mask = valid & (height_ref >= lower) & (height_ref < upper)
        est = _core(np.where(mask, base, np.nan), np.where(mask, ref, np.nan))
        if est is None:
            continue
        row = {"estimate": est}
        if baseline is not None:
            row["baseline_dem"] = _core(np.where(mask, baseline, np.nan), np.where(mask, ref, np.nan))
        by_height[label] = row
    out["by_height_band"] = by_height

    if rgb is not None:
        labels, t = classify_landscape(rgb, ref, gsd)
        per = {}
        base = pred if units == "metre" else aligned
        for cls in ("urban", "sparse", "hilly", "forest"):
            mask = np.zeros(ref.shape, bool)
            for i, j in zip(*np.where(labels == cls)):
                mask[i * t:(i + 1) * t, j * t:(j + 1) * t] = True
            if mask.sum() > 100:
                res = _core(np.where(mask, base, np.nan), np.where(mask, ref, np.nan))
                if res:
                    res["tiles"] = int((labels == cls).sum())
                    per[cls] = res
        out["by_landscape"] = per
    return out


def error_map(pred, ref, units):
    base = pred if units == "metre" else affine_align(pred, ref)
    return (base - ref).astype(np.float32)


def building_level(labels: np.ndarray, est_ndsm: np.ndarray, ref: np.ndarray, gsd: float) -> dict | None:
    """Per-building height accuracy: our LoD1 roof height (70th percentile of
    the estimated above-ground height in each footprint) against the same
    statistic of the reference above-ground height (reference minus its own
    morphological ground). Labels/est are resampled to the reference grid."""
    from PIL import Image
    h, w = ref.shape
    lab = np.asarray(Image.fromarray(labels.astype(np.int32)).resize((w, h), Image.NEAREST))
    est = np.asarray(Image.fromarray(est_ndsm.astype(np.float32)).resize((w, h), Image.BILINEAR))
    rnd = ref - _ground(ref, gsd)
    ids = np.unique(lab[lab > 0])
    if ids.size < 5:
        return None
    ok = np.isfinite(rnd)
    e = ndimage.labeled_comprehension(np.where(ok, est, np.nan), lab, ids,
                                      lambda v: np.nanpercentile(v, 70) if np.isfinite(v).any() else np.nan, float, np.nan)
    r = ndimage.labeled_comprehension(np.where(ok, rnd, np.nan), lab, ids,
                                      lambda v: np.nanpercentile(v, 70) if np.isfinite(v).any() else np.nan, float, np.nan)
    m = np.isfinite(e) & np.isfinite(r)
    e, r = e[m], r[m]
    if e.size < 5:
        return None
    d = e - r
    return {"n": int(e.size), "rmse": float(np.sqrt(np.mean(d ** 2))), "mae": float(np.mean(np.abs(d))),
            "bias": float(np.mean(d)), "r": float(np.corrcoef(e, r)[0, 1]),
            "est_median": float(np.median(e)), "ref_median": float(np.median(r))}
