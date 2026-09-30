"""Terrain and disaster analytics computed from a processed scene.

landslide_susceptibility  – index in [0, 1] from slope, concavity, local
                            relief and bare/sparse vegetation (heuristic
                            weighting of well-known conditioning factors;
                            a screening layer, not a hazard map).
roof_solar                – per-building sunlit fraction over representative
                            sun positions (self- and neighbour-shading from the
                            DSM) and an indicative annual PV yield.
change_detection          – pre/post DSM differencing: height change map,
                            volumes, and buildings whose roofs dropped.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .shadows import cast_shadows


def slope_deg(z: np.ndarray, gsd: float) -> np.ndarray:
    gy, gx = np.gradient(z, gsd)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def landslide_susceptibility(dtm: np.ndarray, rgb: np.ndarray | None, gsd: float) -> tuple[np.ndarray, dict]:
    """Screening index from bare-ground conditioning factors.

    slope      – main driver; ramps from 15° to 45°
    concavity  – concave slopes collect water (negative Laplacian of a 30 m-smoothed DTM)
    relief     – local relief within ~150 m (energy available for failure)
    vegetation – sparse vegetation raises susceptibility (excess-green index)
    """
    z = ndimage.gaussian_filter(dtm.astype(np.float64), max(1.0, 5.0 / gsd))
    s = slope_deg(z, gsd)
    f_slope = np.clip((s - 15.0) / 30.0, 0, 1)
    lap = ndimage.laplace(ndimage.gaussian_filter(z, max(1.0, 15.0 / gsd))) / (gsd * gsd)
    f_conc = np.clip(lap / (np.percentile(np.abs(lap), 95) + 1e-9), 0, 1)
    n = max(3, int(150 / gsd))
    relief = ndimage.maximum_filter(z, size=n) - ndimage.minimum_filter(z, size=n)
    f_relief = np.clip(relief / 100.0, 0, 1)
    if rgb is not None:
        f = np.asarray(Image.fromarray(rgb).resize((dtm.shape[1], dtm.shape[0]), Image.BILINEAR)).astype(np.float32)
        exg = (2 * f[..., 1] - f[..., 0] - f[..., 2]) / (f.sum(-1) + 1e-6)
        f_veg = np.clip(1 - (exg - 0.0) / 0.12, 0, 1)
    else:
        f_veg = np.full(dtm.shape, 0.5)
    idx = (0.5 * f_slope + 0.15 * f_conc + 0.2 * f_relief + 0.15 * f_veg) * (s > 8)
    idx = idx.astype(np.float32)
    classes = {"low": float((idx < 0.25).mean()), "moderate": float(((idx >= 0.25) & (idx < 0.5)).mean()),
               "high": float(((idx >= 0.5) & (idx < 0.7)).mean()), "very_high": float((idx >= 0.7).mean())}
    return idx, {"fractions": classes, "mean_slope_deg": float(s.mean()),
                 "max_slope_deg": float(np.percentile(s, 99.5)),
                 "weights": {"slope": 0.5, "concavity": 0.15, "relief": 0.2, "sparse_vegetation": 0.15}}


def solar_positions(lat_deg: float) -> list[tuple[float, float, float]]:
    """(azimuth, elevation, weight) for equinox and solstice days at 09, 12, 15 h
    solar time. Weights approximate each sample's share of daily insolation."""
    out = []
    for decl in (-23.44, 0.0, 23.44):
        for hour, wgt in ((9, 0.3), (12, 0.4), (15, 0.3)):
            ha = math.radians(15 * (hour - 12))
            lat, d = math.radians(lat_deg), math.radians(decl)
            el = math.asin(math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(ha))
            if el <= math.radians(5):
                continue
            az = math.atan2(-math.sin(ha), math.tan(d) * math.cos(lat) - math.sin(lat) * math.cos(ha))
            out.append(((math.degrees(az) + 360) % 360, math.degrees(el), wgt))
    return out


def roof_solar(dsm: np.ndarray, labels: np.ndarray, gsd: float, lat_deg: float = 22.0,
               ghi_kwh_m2_yr: float = 1900.0, pv_eff: float = 0.18, usable: float = 0.7,
               work_px: int = 512) -> dict[int, dict]:
    """Per-building sunlit fraction and indicative PV yield. India-average GHI
    ~1,700–2,100 kWh/m²/yr; results are screening estimates."""
    h, w = dsm.shape
    f = min(1.0, work_px / max(h, w))
    shape = (max(8, int(h * f)), max(8, int(w * f)))
    z = np.asarray(Image.fromarray(dsm.astype(np.float32)).resize((shape[1], shape[0]), Image.BILINEAR))
    lab = np.asarray(Image.fromarray(labels.astype(np.int32)).resize((shape[1], shape[0]), Image.NEAREST))
    ids = np.unique(lab[lab > 0])
    if ids.size == 0:
        return {}
    lit = np.zeros(shape, np.float32)
    total = 0.0
    for az, el, wgt in solar_positions(lat_deg):
        lit += wgt * (~cast_shadows(z, gsd / f, az, el))
        total += wgt
    lit /= max(total, 1e-9)
    frac = ndimage.mean(lit, lab, ids)
    area = ndimage.sum(np.ones_like(lit), lab, ids) * (gsd / f) ** 2
    return {int(i): {"sunlit_fraction": round(float(fr), 3),
                     "pv_kwh_yr": round(float(a * usable * ghi_kwh_m2_yr * pv_eff * fr), 0)}
            for i, fr, a in zip(ids, frac, area)}


def change_detection(pre: np.ndarray, post: np.ndarray, gsd: float, labels: np.ndarray | None = None,
                     drop_m: float = 3.0) -> tuple[np.ndarray, dict]:
    """post − pre height change; negative = loss (collapse, landslide scar)."""
    d = (post - pre).astype(np.float32)
    ok = np.isfinite(d)
    cell = gsd * gsd
    loss = np.where(ok & (d < -1.0), -d, 0).sum() * cell
    gain = np.where(ok & (d > 1.0), d, 0).sum() * cell
    stats = {"mean_change_m": float(np.nanmean(d)), "volume_loss_m3": float(loss),
             "volume_gain_m3": float(gain),
             "area_lowered_ha": float((ok & (d < -drop_m)).sum() * cell / 1e4),
             "area_raised_ha": float((ok & (d > drop_m)).sum() * cell / 1e4)}
    if labels is not None and labels.max() > 0:
        ids = np.unique(labels[labels > 0])
        med = ndimage.labeled_comprehension(np.where(ok, d, np.nan), labels, ids,
                                            lambda v: np.nanmedian(v) if np.isfinite(v).any() else np.nan,
                                            float, np.nan)
        # pre-event building height ≈ roof median minus the ground ring around it
        ground = ndimage.grey_opening(np.nan_to_num(pre, nan=float(np.nanmin(pre))), size=(15, 15))
        pre_h = ndimage.labeled_comprehension(pre - ground, labels, ids, np.median, float, np.nan)
        dropped = [int(i) for i, v, hgt in zip(ids, med, pre_h)
                   if np.isfinite(v) and v < -max(1.5, min(drop_m, 0.4 * max(hgt, 0)))]
        stats["buildings_checked"] = int(ids.size)
        stats["buildings_height_loss"] = len(dropped)
        stats["building_ids_height_loss"] = dropped[:500]
        stats["rule"] = "median roof drop > max(1.5 m, min(drop_m, 40 % of pre-event height))"
    return d, stats


def labels_from_buildings(buildings: dict, shape: tuple[int, int]) -> np.ndarray:
    """Rasterise viewer building polygons (normalised uv rings) to a label grid."""
    from rasterio.features import rasterize
    h, w = shape
    geoms = []
    for b in buildings.get("buildings", []):
        ring = [(u * (w - 1), v * (h - 1)) for u, v in b.get("polygon_uv", [])]
        if len(ring) >= 4:
            geoms.append(({"type": "Polygon", "coordinates": [ring]}, int(b["id"])))
    if not geoms:
        return np.zeros(shape, np.int32)
    return rasterize(geoms, out_shape=shape, fill=0, dtype="int32")


def load_scene_layer(job_dir: Path, name: str) -> np.ndarray:
    import json
    vm = json.loads((job_dir / "viewer" / "meta.json").read_text())
    return np.fromfile(job_dir / "viewer" / name, dtype="<f4").reshape(vm["grid_h"], vm["grid_w"])


def water_mask(rgb: np.ndarray, gsd: float, min_area_m2: float = 2000.0) -> np.ndarray:
    """Conservative open-water mask: very smooth texture, blue at least as strong
    as red, not bright, and a large connected area (rivers, tanks, lakes).
    Designed to miss small ponds rather than flag roads or shadows."""
    f = rgb.astype(np.float32)
    lum = f.mean(-1)
    mean = ndimage.uniform_filter(lum, 15)
    std = np.sqrt(np.maximum(ndimage.uniform_filter(lum * lum, 15) - mean * mean, 0))
    cand = (std < 4.0) & (f[..., 2] >= f[..., 0] + 4) & (lum < 150) & (lum > 15)
    cand = ndimage.binary_opening(cand, iterations=2)
    lab, n = ndimage.label(cand)
    if n == 0:
        return np.zeros(lum.shape, bool)
    sizes = ndimage.sum(np.ones_like(lum), lab, np.arange(1, n + 1)) * gsd * gsd
    keep = np.flatnonzero(sizes >= min_area_m2) + 1
    return np.isin(lab, keep)
