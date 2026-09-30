"""Conservative LoD1.5 roof *shape hypotheses* from a building nDSM.

The estimates are for the optional city overlay, not reference elevations.
Single-view monocular height often lacks enough roof detail; in that case this
module intentionally keeps a flat LoD1 roof.  A plane/gable label is never a
claim that the actual architectural roof was surveyed.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage


def _polygon_area(points: list[list[float]] | list[tuple[float, float]]) -> float:
    return 0.5 * sum(points[i][0] * points[(i + 1) % len(points)][1]
                     - points[(i + 1) % len(points)][0] * points[i][1]
                     for i in range(len(points)))


def supports_gable_geometry(
    polygon_world: list[list[float]] | None,
    ridge_world: list[list[float]],
    ridge_fraction: float,
    pitch_fraction_per_m: float,
    height_m: float,
) -> bool:
    """Reject ridge hypotheses that cannot form two valid convex roof panels.

    The browser and CityJSON both need one ridge crossing each side of a
    simple convex footprint.  Concave structures fall back to plane/flat until
    a proper roof decomposition is available.
    """
    if polygon_world is None or len(polygon_world) < 4 or len(ridge_world) != 2:
        return False
    points = [[float(p[0]), float(p[1])] for p in polygon_world]
    if points[0] == points[-1]:
        points.pop()
    points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    if len(points) < 3 or height_m <= 0 or pitch_fraction_per_m <= 0:
        return False
    area = abs(_polygon_area(points))
    if area < 1e-5:
        return False
    turns = []
    for i in range(len(points)):
        a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-8:
            turns.append(cross)
    if not turns or not (all(t > 0 for t in turns) or all(t < 0 for t in turns)):
        return False

    ax, ay = map(float, ridge_world[0])
    bx, by = map(float, ridge_world[1])
    dx, dy = bx - ax, by - ay
    ridge_length = math.hypot(dx, dy)
    if ridge_length < 1e-3:
        return False
    signed = [(dx * (p[1] - ay) - dy * (p[0] - ax)) / ridge_length for p in points]
    eps = 1e-7
    if min(signed) >= -eps or max(signed) <= eps:
        return False
    boundary, distances = [], []
    for i, point in enumerate(points):
        j = (i + 1) % len(points)
        boundary.append(point)
        distances.append(signed[i])
        s0, s1 = signed[i], signed[j]
        if s0 * s1 < -(eps * eps):
            alpha = s0 / (s0 - s1)
            other = points[j]
            boundary.append([point[0] + alpha * (other[0] - point[0]),
                             point[1] + alpha * (other[1] - point[1])])
            distances.append(0.0)
    if sum(abs(s) <= eps for s in distances) != 2:
        return False
    for keep_positive in (True, False):
        face = [p for p, s in zip(boundary, distances) if (s >= -eps if keep_positive else s <= eps)]
        if len(face) < 3 or abs(_polygon_area(face)) < area * 0.03:
            return False
    fractions = [ridge_fraction - pitch_fraction_per_m * abs(s) for s in distances]
    return (all(math.isfinite(f) for f in fractions)
            and min(fractions) > max(0.04, 0.2 / height_m)
            and max(fractions) < 1.6)


def rescale_roof_fit(roof_fit: dict | None, scale: float) -> None:
    """Update pitch and residuals after an *absolute-from-raw* height rescale.

    Geometry fractions remain unchanged; ``scale`` is the new building height
    divided by its original inferred height.  This mutates only display and
    provenance fields, preserving unscaled values for repeat rescale/reset.
    """
    if not roof_fit or not math.isfinite(scale) or scale <= 0:
        return
    for key in ("flat_rmse_m", "fit_rmse_m"):
        if key in roof_fit:
            raw_key = f"raw_{key}"
            roof_fit.setdefault(raw_key, float(roof_fit[key]))
            roof_fit[key] = round(float(roof_fit[raw_key]) * scale, 3)
    if roof_fit.get("pitch_deg") is not None:
        roof_fit.setdefault("raw_pitch_deg", float(roof_fit["pitch_deg"]))
        raw_pitch = math.radians(float(roof_fit["raw_pitch_deg"]))
        roof_fit["pitch_deg"] = round(math.degrees(math.atan(math.tan(raw_pitch) * scale)), 1)
    roof_fit["height_scale_from_fit"] = round(scale, 6)


def _robust_fit(matrix: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Small Huber IRLS fit, deterministic and resistant to rooftop artefacts."""
    beta = np.linalg.lstsq(matrix, values, rcond=None)[0]
    for _ in range(3):
        residual = values - matrix @ beta
        mad = 1.4826 * np.median(np.abs(residual - np.median(residual)))
        cutoff = max(0.35, 1.5 * mad)
        weights = np.minimum(1.0, cutoff / np.maximum(np.abs(residual), 1e-8))
        sqrt_w = np.sqrt(weights)
        beta = np.linalg.lstsq(matrix * sqrt_w[:, None], values * sqrt_w, rcond=None)[0]
    return beta, values - matrix @ beta


def _score(residual: np.ndarray, clip_m: float) -> float:
    return float(np.sqrt(np.mean(np.minimum(residual ** 2, clip_m ** 2))))


def fit_roof_shape(
    ndsm_crop: np.ndarray,
    footprint_crop: np.ndarray,
    *,
    image_shape: tuple[int, int],
    row_offset: int,
    col_offset: int,
    gsd: float,
    world_w: float,
    world_h: float,
    height_m: float,
    uncertainty_crop: np.ndarray | None = None,
    max_samples: int = 3000,
    footprint_polygon_world: list[list[float]] | None = None,
) -> dict:
    """Classify a footprint as flat, one tilted plane, or a two-plane gable.

    Inputs are cropped to the footprint's bounding box.  Pass the same
    ``polygon_world`` that the renderer receives; without it no gable is
    emitted.  Output fractions are
    relative to the reported building height, so a later scale anchor can
    rescale the whole building without invalidating the roof geometry.

    ``ridge_world`` uses the same ``[x, z]`` frame as ``polygon_world``.  A
    client can render a gable surface with height fraction
    ``ridge_fraction - pitch_fraction_per_m * distance_to_ridge``.  For a
    plane, use ``center_fraction + dot(gradient_fraction_per_m, point-center)``.
    """
    values_grid = np.asarray(ndsm_crop, dtype=np.float32)
    footprint = np.asarray(footprint_crop, dtype=bool)
    if values_grid.ndim != 2 or footprint.shape != values_grid.shape:
        raise ValueError("nDSM crop and footprint must share a 2D grid")
    if gsd <= 0 or world_w <= 0 or world_h <= 0 or height_m <= 0:
        raise ValueError("ground spacing, scene extent and building height must be positive")
    if max_samples < 100:
        raise ValueError("max_samples must be at least 100")

    result = {"type": "flat", "method": "nDSM robust shape fit; visual hypothesis",
              "height_fraction": 1.0, "support_pixels": int(footprint.sum())}
    # Erode one pixel: mixed roof/wall/ground pixels are the main source of
    # false pitched roofs in single-view DSMs.
    interior = ndimage.binary_erosion(footprint, structure=np.ones((3, 3), bool))
    interior &= np.isfinite(values_grid)
    if interior.sum() < 64 or footprint.sum() < 100:
        result["reason"] = "insufficient clean roof interior"
        return result

    smooth = ndimage.median_filter(np.where(np.isfinite(values_grid), values_grid, 0.0), size=3)
    rr, cc = np.nonzero(interior)
    if rr.size > max_samples:
        keep = np.linspace(0, rr.size - 1, max_samples, dtype=np.int64)
        rr, cc = rr[keep], cc[keep]
    z = smooth[rr, cc].astype(np.float64)
    finite = np.isfinite(z) & (z >= 0)
    rr, cc, z = rr[finite], cc[finite], z[finite]
    if z.size < 64:
        result["reason"] = "insufficient valid roof heights"
        return result

    # Pixels are transformed to local physical metres for meaningful pitch.
    col = cc.astype(np.float64) + col_offset + 0.5
    row = rr.astype(np.float64) + row_offset + 0.5
    c0, r0 = float(np.mean(col)), float(np.mean(row))
    x, y = (col - c0) * gsd, (row - r0) * gsd
    flat_h = float(np.median(z))
    flat_residual = z - flat_h
    spread = 1.4826 * np.median(np.abs(flat_residual))
    clip_m = max(0.7, 3.0 * spread)
    flat_score = _score(flat_residual, clip_m)
    result["flat_rmse_m"] = round(flat_score, 3)
    result["fit_rmse_m"] = round(flat_score, 3)
    result["support_pixels"] = int(z.size)

    sigma = 0.0
    if uncertainty_crop is not None and uncertainty_crop.shape == footprint.shape:
        unc = np.asarray(uncertainty_crop)[rr, cc]
        unc = unc[np.isfinite(unc) & (unc >= 0)]
        if unc.size:
            sigma = float(np.median(unc))
    # Reject pitch below the estimated noise floor.  This is a heuristic,
    # not a calibrated uncertainty probability.
    min_rise = max(0.8, 1.5 * sigma)
    if flat_score < max(0.4, 0.35 * sigma):
        result["reason"] = "roof is approximately flat at model resolution"
        return result

    def to_world(pixel_col: float, pixel_row: float) -> list[float]:
        ih, iw = image_shape
        return [float(round((pixel_col / max(iw - 1, 1) - 0.5) * world_w, 3)),
                float(round((pixel_row / max(ih - 1, 1) - 0.5) * world_h, 3))]

    plane_x = np.column_stack((np.ones_like(x), x, y))
    plane_beta, plane_residual = _robust_fit(plane_x, z)
    plane_score = _score(plane_residual, clip_m)
    gradient = plane_beta[1:]
    slope = float(np.linalg.norm(gradient))
    projected = x * gradient[0] + y * gradient[1]
    plane_rise = float(np.percentile(projected, 95) - np.percentile(projected, 5))
    plane_pitch = math.degrees(math.atan(slope))
    max_rise = max(6.0, 0.65 * height_m)
    model_score_limit = max(2.5, 0.16 * height_m)
    plane_ok = (
        plane_rise >= min_rise
        and plane_rise <= max_rise
        and 4.0 <= plane_pitch <= 45.0
        and plane_score <= model_score_limit
        and plane_score <= flat_score * 0.72
    )

    # Candidate ridge directions: PCA major axis, its perpendicular, and the
    # two diagonal axes.  A square footprint thus does not force one ridge.
    covariance = np.cov(np.stack((x, y)))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    major = eigenvectors[:, int(np.argmax(eigenvalues))]
    base_angle = math.atan2(float(major[1]), float(major[0]))
    best_gable = None
    for turn in (0.0, math.pi / 4, math.pi / 2, 3 * math.pi / 4):
        angle = base_angle + turn
        axis = np.array([math.cos(angle), math.sin(angle)])
        normal = np.array([-axis[1], axis[0]])
        across = x * normal[0] + y * normal[1]
        width = float(np.percentile(across, 95) - np.percentile(across, 5))
        if width < max(3.0, 4.0 * gsd):
            continue
        for offset in (-0.15 * width, 0.0, 0.15 * width):
            # Ridge needs useful support on *both* sides, otherwise a tilted
            # plane can be misclassified as a gable.
            left = float(np.mean(across < offset))
            if not 0.25 <= left <= 0.75:
                continue
            distance = np.abs(across - offset)
            design = np.column_stack((np.ones_like(distance), -distance))
            beta, residual = _robust_fit(design, z)
            pitch = math.degrees(math.atan(float(beta[1])))
            rise = float(beta[1] * np.percentile(distance, 90))
            score = _score(residual, clip_m)
            if not (beta[1] > 0 and min_rise <= rise <= max_rise and 7.0 <= pitch <= 50.0):
                continue
            if best_gable is None or score < best_gable["score"]:
                best_gable = {"score": score, "rise": rise, "pitch": pitch,
                              "ridge_h": float(beta[0]), "axis": axis,
                              "normal": normal, "offset": float(offset)}

    # A gable must beat both simpler models by a substantial margin.  If it
    # merely ties a plane, keep the simpler roof rather than invent a ridge.
    if (best_gable is not None and best_gable["score"] <= model_score_limit
            and best_gable["score"] <= flat_score * 0.63
            and best_gable["score"] <= plane_score * 0.82):
        axis, normal = best_gable["axis"], best_gable["normal"]
        along = x * axis[0] + y * axis[1]
        t0, t1 = np.percentile(along, [5, 95])
        end0 = np.array([c0, r0]) + (t0 * axis + best_gable["offset"] * normal) / gsd
        end1 = np.array([c0, r0]) + (t1 * axis + best_gable["offset"] * normal) / gsd
        ridge_world = [to_world(*end0), to_world(*end1)]
        # The scene's world dimensions are proportional to pixel dimensions;
        # their metre scale differs by <1 pixel at the edge.
        candidate = {
            "type": "gable",
            "ridge_world": ridge_world,
            "ridge_fraction": round(best_gable["ridge_h"] / height_m, 4),
            "eave_fraction": round((best_gable["ridge_h"] - best_gable["rise"]) / height_m, 4),
            "rise_fraction": round(best_gable["rise"] / height_m, 4),
            "pitch_fraction_per_m": round(math.tan(math.radians(best_gable["pitch"])) / height_m, 6),
            "pitch_deg": round(best_gable["pitch"], 1),
            "fit_rmse_m": round(best_gable["score"], 3),
            "rmse_reduction_fraction": round(1.0 - best_gable["score"] / max(flat_score, 1e-6), 3),
        }
        if supports_gable_geometry(
            footprint_polygon_world, ridge_world, candidate["ridge_fraction"],
            candidate["pitch_fraction_per_m"], height_m,
        ):
            result.update(candidate)
            return result

    if plane_ok:
        ih, iw = image_shape
        # d(image metres)/d(world metres), separately in x and z.
        sx = gsd * max(iw - 1, 1) / world_w
        sz = gsd * max(ih - 1, 1) / world_h
        result.update({
            "type": "plane",
            "center_world": to_world(c0, r0),
            "center_fraction": round(float(plane_beta[0]) / height_m, 4),
            "gradient_fraction_per_m": [round(float(gradient[0] * sx / height_m), 6),
                                        round(float(gradient[1] * sz / height_m), 6)],
            "rise_fraction": round(plane_rise / height_m, 4),
            "pitch_deg": round(plane_pitch, 1),
            "fit_rmse_m": round(plane_score, 3),
            "rmse_reduction_fraction": round(1.0 - plane_score / max(flat_score, 1e-6), 3),
        })
    else:
        result["reason"] = "slope/ridge not resolved above model noise"
    return result
