"""Conservative RGB-guided refinement for *display and footprint extraction*.

The result is deliberately not a replacement for the calibrated DSM or nDSM:
the RGB image can contain shadows, painted lines, and trees that are unrelated
to height.  Keep the original arrays for measurements, validation, and exports.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def refine_ndsm_edges(
    ndsm: np.ndarray,
    rgb: np.ndarray,
    *,
    sigma_color: float = 0.14,
    sigma_height_m: float | None = None,
    sigma_spatial_px: float = 1.15,
    detail_strength: float = 0.8,
    radius: int = 2,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """Return an edge-preserving nDSM copy guided by RGB and height similarity.

    A small joint bilateral filter reduces roof speckle without mixing heights
    across a strong colour *or height* boundary.  A bounded detail term makes
    coincident colour/height edges a little crisper.  No edge is inferred from
    colour alone.  The function is deterministic and leaves NaNs untouched.

    Heights and colour must share the same pixel grid.  ``mask`` can restrict
    refinement to selected regions; samples never cross the mask boundary.
    Parameters assume metres, or a display-scaled relative height field.
    """
    source = np.asarray(ndsm, dtype=np.float32)
    colour = np.asarray(rgb)
    if source.ndim != 2 or colour.shape != (*source.shape, 3):
        raise ValueError("ndsm must be HxW and rgb must be HxWx3 on the same grid")
    if radius not in (1, 2, 3):
        raise ValueError("radius must be 1, 2, or 3 pixels")
    if sigma_color <= 0 or sigma_spatial_px <= 0:
        raise ValueError("filter bandwidths must be positive")
    if not 0 <= detail_strength <= 1:
        raise ValueError("detail_strength must be in [0, 1]")

    valid = np.isfinite(source)
    if not np.any(valid):
        return source.copy()
    if mask is not None:
        selected = np.asarray(mask, dtype=bool).copy()
        if selected.shape != source.shape:
            raise ValueError("mask must share the nDSM grid")
    else:
        selected = valid
    selected &= valid
    if not np.any(selected):
        return source.copy()

    if sigma_height_m is None:
        # A local roof filter should not mix ground and a 10–30 m structure.
        p95 = float(np.percentile(source[valid], 95))
        sigma_height_m = max(0.5, min(2.0, p95 * 0.08))
    if sigma_height_m <= 0:
        raise ValueError("sigma_height_m must be positive")

    colour = colour.astype(np.float32)
    if colour.max(initial=0) > 1.5:
        colour *= 1.0 / 255.0
    colour = np.clip(colour, 0.0, 1.0)
    safe = np.where(valid, source, 0.0)
    pad_h = np.pad(safe, radius, mode="reflect")
    pad_c = np.pad(colour, ((radius, radius), (radius, radius), (0, 0)), mode="reflect")
    pad_sel = np.pad(selected, radius, mode="reflect")
    height, width = source.shape
    acc = safe.copy()  # centre sample always has weight 1
    total = np.ones_like(safe)

    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == dy == 0:
                continue
            yy, xx = radius + dy, radius + dx
            neighbour_h = pad_h[yy:yy + height, xx:xx + width]
            neighbour_c = pad_c[yy:yy + height, xx:xx + width]
            colour_sq = np.sum((neighbour_c - colour) ** 2, axis=2)
            height_sq = (neighbour_h - safe) ** 2
            exponent = (
                colour_sq / (2.0 * sigma_color ** 2)
                + height_sq / (2.0 * sigma_height_m ** 2)
                + (dx * dx + dy * dy) / (2.0 * sigma_spatial_px ** 2)
            )
            weight = np.exp(-np.minimum(exponent, 80.0))
            neighbour_sel = pad_sel[yy:yy + height, xx:xx + width]
            weight *= selected & neighbour_sel
            acc += neighbour_h * weight
            total += weight

    filtered = acc / total
    # Sharpen only *coincident* RGB and height edges.  This avoids turning a
    # dark road marking or shadow into a made-up step in the surface.
    if detail_strength:
        gray = 0.2126 * colour[..., 0] + 0.7152 * colour[..., 1] + 0.0722 * colour[..., 2]
        rgb_gx = ndimage.sobel(gray, axis=1) / 8.0
        rgb_gy = ndimage.sobel(gray, axis=0) / 8.0
        h_gx = ndimage.sobel(safe, axis=1) / 8.0
        h_gy = ndimage.sobel(safe, axis=0) / 8.0
        rgb_mag = np.hypot(rgb_gx, rgb_gy)
        h_mag = np.hypot(h_gx, h_gy)
        alignment = np.abs(rgb_gx * h_gx + rgb_gy * h_gy) / (rgb_mag * h_mag + 1e-6)
        colour_gate = np.clip((rgb_mag - 0.035) / 0.08, 0.0, 1.0)
        height_gate = np.clip((h_mag - 0.25 * sigma_height_m) / sigma_height_m, 0.0, 1.0)
        boost = detail_strength * colour_gate * height_gate * alignment
        # In smooth interiors retain only 35% of residual speckle.  At a
        # coincident colour/height boundary the factor can reach 1.15, a
        # slight unsharp step bounded below by the local raw extrema.
        refined = filtered + (0.35 + boost) * (safe - filtered)
    else:
        refined = filtered

    # Bounded, non-negative output: no invented extrema or negative heights.
    local_min = ndimage.minimum_filter(safe, size=3, mode="reflect")
    local_max = ndimage.maximum_filter(safe, size=3, mode="reflect")
    refined = np.clip(refined, np.maximum(local_min, 0.0), local_max)
    return np.where(selected, refined, source).astype(np.float32)
