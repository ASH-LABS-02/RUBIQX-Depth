"""Shadow-consistency scale calibration.

Idea: a candidate DSM with structure scale k casts shadows for the known sun
position. The k whose rendered shadows best overlap the dark shadows visible in
the image is the physically consistent scale. Unlike per-building shadow-length
measurement, this uses every shadow in the scene at once and needs no building
footprints.

Only sun elevation (and ideally azimuth) are required. Azimuth can be
estimated when missing, but elevation cannot: shadow length is proportional to
height / tan(elevation), so scale and elevation are not separable.

Conventions
  azimuth   degrees clockwise from north, direction TO the sun
  elevation degrees above the horizon
  image rows increase southwards (north-up rasters)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy import ndimage


@dataclass
class ShadowFit:
    k: float
    iou: float
    azimuth_deg: float
    azimuth_estimated: bool
    shadow_fraction: float
    candidates: int


def _resize(a, shape, resample=Image.BILINEAR):
    return np.asarray(Image.fromarray(a.astype(np.float32)).resize((shape[1], shape[0]), resample))


def observed_shadow_mask(rgb: np.ndarray) -> np.ndarray:
    """Dark, low-chroma pixels relative to the scene (Otsu on luminance)."""
    x = rgb.astype(np.float32) / 255.0
    lum = 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]
    mx, mn = x.max(-1), x.min(-1)
    sat = (mx - mn) / (mx + 1e-6)
    # Otsu threshold on luminance
    hist, edges = np.histogram(lum, bins=128, range=(0, 1))
    p = hist / max(hist.sum(), 1)
    omega = np.cumsum(p)
    mu = np.cumsum(p * (edges[:-1] + edges[1:]) / 2)
    between = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega) + 1e-12)
    t = edges[int(np.nanargmax(between))]
    # shadows are darker than the Otsu split (and never brighter than the median)
    mask = lum < min(t, np.median(lum))
    # vegetation in sun is also dark-ish but saturated green; keep low-chroma darks
    green = (x[..., 1] > x[..., 0] * 1.05) & (x[..., 1] > x[..., 2] * 1.05) & (sat > 0.25)
    mask &= ~green
    mask = ndimage.binary_opening(mask, iterations=1)
    return mask


def cast_shadows(height_m: np.ndarray, gsd: float, azimuth_deg: float, elevation_deg: float,
                 max_dist_px: int | None = None) -> np.ndarray:
    """Boolean shadow mask by marching towards the sun."""
    az, el = np.radians(azimuth_deg), np.radians(elevation_deg)
    dx, dy = np.sin(az), -np.cos(az)          # pixel step towards the sun
    tan_el = np.tan(el)
    relief = float(np.nanmax(height_m) - np.nanmin(height_m))
    if max_dist_px is None:
        max_dist_px = int(min(400, relief / max(tan_el, 1e-3) / gsd + 2))
    h = np.nan_to_num(height_m, nan=float(np.nanmin(height_m)))
    H, W = h.shape
    ar, ac = np.arange(H), np.arange(W)
    shadow = np.zeros(h.shape, bool)
    step = max(1, max_dist_px // 60)
    seen = set()
    for t in range(1, max_dist_px + 1, step):
        # nearest-neighbour sampling at an integer offset is a clamped shift
        # (identical to map_coordinates(order=0, mode="nearest"), much faster)
        ky, kx = int(np.floor(dy * t + 0.5)), int(np.floor(dx * t + 0.5))
        if (ky, kx) in seen:          # a later t with the same offset is stricter: no new cells
            continue
        seen.add((ky, kx))
        sample = h[np.clip(ar + ky, 0, H - 1)][:, np.clip(ac + kx, 0, W - 1)]
        shadow |= (sample - h) > t * gsd * tan_el
    return shadow


def dominant_shadow_azimuth(structure: np.ndarray, mask: np.ndarray) -> float:
    """Estimate sun azimuth: shadows lie on the anti-sun side of raised areas.
    Returns azimuth (degrees) of the direction from shadow centroid mass to
    structure mass, averaged over the scene using a cross-correlation peak."""
    s = (structure > np.percentile(structure, 80)).astype(np.float32)
    m = mask.astype(np.float32)
    s -= s.mean()
    m -= m.mean()
    F = np.fft.rfft2(s) * np.conj(np.fft.rfft2(m))
    cc = np.fft.irfft2(F, s=s.shape)
    cc = np.fft.fftshift(cc)
    cy, cx = np.array(cc.shape) // 2
    r = 40
    win = cc[cy - r:cy + r + 1, cx - r:cx + r + 1]
    win[r, r] = win.min()
    py, px = np.unravel_index(np.argmax(win), win.shape)
    oy, ox = py - r, px - r               # offset: structure ≈ shadow shifted by (oy, ox)
    # structure lies towards the sun relative to its shadow
    return float((np.degrees(np.arctan2(ox, -oy)) + 360) % 360)


def fit_scale(structure: np.ndarray, base: np.ndarray, rgb: np.ndarray, gsd: float,
              sun_elevation_deg: float, sun_azimuth_deg: float | None = None,
              work_px: int = 384, k_range: tuple[float, float] = (0.5, 200.0)) -> ShadowFit | None:
    """Search the structure scale k (metres per unit) maximising IoU between
    rendered and observed shadows. `base` is terrain (m) on the same grid."""
    h, w = structure.shape
    f = min(1.0, work_px / max(h, w))
    shape = (max(8, int(h * f)), max(8, int(w * f)))
    g = gsd / f
    s = _resize(structure, shape)
    b = _resize(base, shape)
    obs = _resize(observed_shadow_mask(rgb).astype(np.float32), shape) > 0.5
    frac = float(obs.mean())
    if frac < 0.005 or s.max() <= 0:
        return None
    est = sun_azimuth_deg is None
    az = dominant_shadow_azimuth(s, obs) if est else float(sun_azimuth_deg)
    ks = np.geomspace(*k_range, 36)
    best = (-1.0, None)
    for k in ks:
        pred = cast_shadows(b + k * s, g, az, sun_elevation_deg)
        inter = np.logical_and(pred, obs).sum()
        union = np.logical_or(pred, obs).sum()
        iou = inter / max(union, 1)
        if iou > best[0]:
            best = (iou, k)
    # refine around the best coarse value
    k0 = best[1]
    for k in np.geomspace(k0 / 1.2, k0 * 1.2, 9):
        pred = cast_shadows(b + k * s, g, az, sun_elevation_deg)
        iou = np.logical_and(pred, obs).sum() / max(np.logical_or(pred, obs).sum(), 1)
        if iou > best[0]:
            best = (iou, k)
    return ShadowFit(k=float(best[1]), iou=float(best[0]), azimuth_deg=az,
                     azimuth_estimated=est, shadow_fraction=frac, candidates=len(ks) + 9)
