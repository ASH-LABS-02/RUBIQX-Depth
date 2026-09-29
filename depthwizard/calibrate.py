"""Scale calibration: relative height -> metric height.

Three strategies, chosen automatically from what the user supplies:

1. DEM fusion (georeferenced image + low-res DEM such as SRTM 30 m / CartoDEM)
   DSM = DEM_terrain + datum_offset + k * structure
   * DEM_terrain  – the DEM reprojected/resampled to the image grid; it gives
                    the absolute datum and large-scale relief.
   * structure    – nonnegative high-pass of the network output, anchored at
                    its lower tail so the bare-earth DEM stays the floor.
   * k            – metres per relative unit, estimated (in order of priority)
                    from GCPs, from a robust fit of the low-passed network
                    output against the DEM, or from a scene-level prior. A
                    prior only gives approximate metric scale.

2. GCP affine (georeferenced or not, a few surveyed points)
   DSM = a * rel + b, fitted robustly (Huber IRLS) on the GCPs.

3. Relative only (plain PNG/JPG) – rDSM in [0, 1]; the viewer applies an
   optional prior-based scale purely for display.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

# Scene-level priors: rough upper-tail above-ground structure height (m).
# Used only when neither GCPs nor a trustworthy DEM fit is available.
SCENE_PRIORS = {
    "urban": 25.0,
    "sparse": 8.0,
    "forest": 22.0,
    "hilly": 15.0,
    "auto": 15.0,
}


@dataclass
class Calibration:
    method: str
    scale_k: float | None = None      # metres per relative unit (structure)
    a: float | None = None            # low-freq fit slope
    b: float | None = None
    fit_r: float | None = None        # correlation of low-freq fit
    n_gcp: int = 0
    datum_offset_m: float | None = None
    dem_coverage: float | None = None
    scale_source: str | None = None
    evidence_level: str | None = None
    ground_anchor_quantile: float | None = None
    structure_upper_quantile: float | None = None
    gcp_residual_rmse_m: float | None = None
    gcp_loo_rmse_m: float | None = None
    gcp_spread_fraction: float | None = None
    vertical_datum: str | None = None
    match_dem_30m: bool = False
    dem_canopy_corrected: bool = False
    is_agl: bool = False
    note: str = ""
    dtm: np.ndarray | None = None
    ndsm: np.ndarray | None = None

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None and not isinstance(v, np.ndarray)}


# ---------------------------------------------------------------- utilities
def huber_affine(x: np.ndarray, y: np.ndarray, iters: int = 20, delta: float | None = None):
    """Robust y ≈ a x + b via iteratively reweighted least squares (Huber)."""
    x, y = np.asarray(x, np.float64).ravel(), np.asarray(y, np.float64).ravel()
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if x.size < 2:
        raise ValueError("need at least 2 valid samples for calibration")
    w = np.ones_like(x)
    a, b = 1.0, 0.0
    for _ in range(iters):
        A = np.stack([x * w, w], 1)
        (a, b), *_ = np.linalg.lstsq(A, y * w, rcond=None)
        r = y - (a * x + b)
        s = delta or 1.4826 * np.median(np.abs(r - np.median(r))) + 1e-9
        u = np.abs(r) / (1.345 * s)
        w = np.where(u <= 1, 1.0, 1.0 / u)
        w = np.sqrt(w)
    return float(a), float(b)


def dem_to_grid(dem_path: str | Path, image, *, return_coverage: bool = False):
    """Reproject/resample a DEM onto the image grid, rejecting poor coverage.

    Small holes or fringe gaps are filled by nearest valid DEM pixels. A DEM
    missing more than 10% of the image footprint cannot anchor an absolute
    DSM, so it is rejected instead of silently extrapolated.
    """
    import rasterio
    from rasterio.warp import reproject, Resampling

    h, w = image.shape
    out = np.full((h, w), np.nan, np.float32)
    with rasterio.open(dem_path) as src:
        reproject(source=rasterio.band(src, 1), destination=out,
                  src_transform=src.transform, src_crs=src.crs,
                  src_nodata=src.nodata, dst_transform=image.transform,
                  dst_crs=image.crs, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    if np.isnan(out).all():
        raise ValueError("DEM does not overlap the image footprint")
    coverage = float(np.isfinite(out).mean())
    if coverage < 0.9:
        raise ValueError(f"DEM covers only {coverage:.1%} of the image; at least 90% is required")
    if np.isnan(out).any():  # fill small gaps with nearest valid
        idx = ndimage.distance_transform_edt(np.isnan(out), return_distances=False,
                                             return_indices=True)
        out = out[tuple(idx)]
    return (out, coverage) if return_coverage else out


def fetch_srtm(image, out_path: str | Path, api_key: str | None = None,
               demtype: str = "SRTMGL1") -> Path:
    """Download a global DEM for the image footprint from OpenTopography.
    demtype: SRTMGL1 (SRTM 30 m) or COP30 (Copernicus GLO-30, a DSM - usually
    the better anchor).
    Needs a free API key (env OPENTOPO_API_KEY). Offline alternative: pass a
    local DEM file (SRTM from USGS EarthExplorer, CartoDEM from Bhuvan)."""
    import urllib.request
    from rasterio.warp import transform_bounds

    key = api_key or os.environ.get("OPENTOPO_API_KEY")
    if not key:
        raise RuntimeError("set OPENTOPO_API_KEY or supply --dem path/to/dem.tif")
    h, w = image.shape
    left, top = image.transform * (0, 0)
    right, bottom = image.transform * (w, h)
    west, south, east, north = transform_bounds(image.crs, "EPSG:4326",
                                                min(left, right), min(top, bottom),
                                                max(left, right), max(top, bottom))
    pad = 0.005
    url = (f"https://portal.opentopography.org/API/globaldem?demtype={demtype}"
           f"&south={south - pad}&north={north + pad}&west={west - pad}&east={east + pad}"
           f"&outputFormat=GTiff&API_Key={key}")
    out_path = Path(out_path)
    urllib.request.urlretrieve(url, out_path)
    return out_path


def load_gcps(path: str | Path, image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """CSV with columns x,y,z. x/y are pixel col/row, or map coordinates when
    the image is georeferenced and the header says 'easting,northing,z' /
    'lon,lat,z'."""
    with open(path, newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError("GCP CSV is empty")
    keys = {k.lower().strip(): k for k in rows[0].keys()}
    if "z" not in keys:
        raise ValueError("GCP CSV needs a z height column")
    z = np.array([float(r[keys["z"]]) for r in rows])
    if "x" in keys and "y" in keys:
        col = np.array([float(r[keys["x"]]) for r in rows])
        row = np.array([float(r[keys["y"]]) for r in rows])
    else:
        if not image.georeferenced:
            raise ValueError("map-coordinate GCPs require a georeferenced image")
        if "lon" in keys and "lat" in keys:
            from rasterio.warp import transform
            longitude = [float(r[keys["lon"]]) for r in rows]
            latitude = [float(r[keys["lat"]]) for r in rows]
            E, N = transform("EPSG:4326", image.crs, longitude, latitude)
        elif "easting" in keys and "northing" in keys:
            E = np.array([float(r[keys["easting"]]) for r in rows])
            N = np.array([float(r[keys["northing"]]) for r in rows])
        else:
            raise ValueError("GCP CSV needs x,y,z; easting,northing,z; or lon,lat,z")
        inv = ~image.transform
        col, row = inv * (E, N)
    row, col = np.asarray(row), np.asarray(col)
    h, w = image.shape
    if (not np.isfinite(row).all() or not np.isfinite(col).all() or
            not np.isfinite(z).all()):
        raise ValueError("GCP coordinates and heights must be finite")
    if np.any((row < 0) | (row > h - 1) | (col < 0) | (col > w - 1)):
        raise ValueError("one or more GCPs lie outside the image footprint")
    return row, col, z


def _sample(arr, rows, cols):
    return ndimage.map_coordinates(arr, [rows, cols], order=1, mode="nearest")


def _gcp_quality(rows, cols, x, y, shape):
    """Summarize point distribution and leave-one-out affine transfer error.

    In-sample residuals can be zero with two points and must never be treated
    as independent validation. Sparse/clustered/inconsistent points are kept
    usable for prototyping but tagged provisional in export metadata.
    """
    h, w = shape
    spread_x = float(np.ptp(cols) / max(w - 1, 1))
    spread_y = float(np.ptp(rows) / max(h - 1, 1))
    spread = min(spread_x, spread_y)
    loo = None
    if len(x) >= 4:
        errors = []
        for i in range(len(x)):
            keep = np.arange(len(x)) != i
            if np.ptp(x[keep]) < 1e-3:
                continue
            a, b = huber_affine(x[keep], y[keep])
            errors.append(a * x[i] + b - y[i])
        if errors:
            loo = float(np.sqrt(np.mean(np.square(errors))))
    threshold = max(5.0, 0.2 * float(np.ptp(y)))
    credible = len(x) >= 6 and spread >= 0.3 and loo is not None and loo <= threshold
    return spread, loo, "surveyed-points" if credible else "provisional-gcp"


# ------------------------------------------------------------- main entry
def calibrate(rel: np.ndarray, image, *, dem_path=None, gcp_path=None,
              scene: str = "auto", dem_res_m: float = 30.0,
              is_agl: bool = False, match_dem_30m: bool = True,
              vertical_datum: str = "EGM2008"):
    """Return (height_map, units, Calibration)."""
    gsd = image.pixel_size_m or 1.0
    gcps = load_gcps(gcp_path, image) if gcp_path else None

    # ---- 1. DEM fusion -------------------------------------------------
    if dem_path and image.georeferenced:
        dem, coverage = dem_to_grid(dem_path, image, return_coverage=True)
        sigma = max(1.0, 0.5 * dem_res_m / gsd)          # match DEM resolution
        rel_lp = ndimage.gaussian_filter(rel, sigma)

        if is_agl:
            # AGL-trained backbone (e.g. GAMUS fine-tune): heights are already above-ground.
            # Extract ground floor via morphological opening (~60 m scale) instead of high-pass
            # which would make ground negative around buildings and flatten dense canopy.
            filter_size = max(3, int(round(60.0 / gsd)))
            if filter_size % 2 == 0:
                filter_size += 1
            ground_level = ndimage.grey_opening(rel, size=(filter_size, filter_size))
            ground_level = ndimage.gaussian_filter(ground_level, max(1.0, 0.5 * sigma))
            structure = np.maximum(rel - ground_level, 0.0)
            ground_q, upper_q = 0.0, 98.0
            ground_anchor = 0.0
            structure_upper = float(np.percentile(structure, upper_q))
            structure_span = max(structure_upper, 1e-6)
        else:
            highpass = rel - rel_lp
            # A high-pass has zero mean but an above-ground surface should not
            # systematically fall below the bare-earth DEM. Use the lower 30%
            # as a robust ground anchor and preserve only positive structures.
            ground_q, upper_q = 30.0, 98.0
            ground_anchor, structure_upper = np.percentile(highpass, (ground_q, upper_q))
            structure = np.maximum(highpass - ground_anchor, 0.0)
            structure_span = float(structure_upper - ground_anchor)

        dem_s = ndimage.gaussian_filter(dem, sigma)

        # decimate for the fit (speed, and independence of samples)
        stride = max(1, int(dem_res_m / gsd))
        xs, ys = rel_lp[::stride, ::stride].ravel(), dem_s[::stride, ::stride].ravel()
        r = float(np.corrcoef(xs, ys)[0, 1]) if xs.std() > 1e-6 and ys.std() > 1e-6 else 0.0
        a, b = huber_affine(xs, ys)

        datum_offset = 0.0
        gcp_rmse = None
        if gcps is not None and len(gcps[2]) >= 2:
            rows, cols, z = gcps
            resid = z - _sample(dem, rows, cols)          # structure height at GCPs
            sample_structure = _sample(structure, rows, cols)
            if np.ptp(sample_structure) < 1e-3:
                raise ValueError("GCPs do not span different relative heights; "
                                 "cannot determine structure scale")
            k, datum_offset = huber_affine(sample_structure, resid)
            if not np.isfinite(k) or k <= 0:
                raise ValueError("GCPs imply a nonpositive structure scale; "
                                 "check coordinates, heights, and vertical datum")
            gcp_rmse = float(np.sqrt(np.mean((k * sample_structure + datum_offset - resid) ** 2)))
            spread, loo, evidence = _gcp_quality(rows, cols, sample_structure, resid, rel.shape)
            cal = Calibration("dem+gcp", scale_k=k, a=a, b=b, fit_r=r, n_gcp=len(z),
                              datum_offset_m=datum_offset, dem_coverage=coverage,
                              scale_source="GCP", evidence_level=evidence,
                              ground_anchor_quantile=ground_q,
                              structure_upper_quantile=upper_q,
                              gcp_residual_rmse_m=gcp_rmse,
                              gcp_loo_rmse_m=loo, gcp_spread_fraction=spread,
                              note="DEM terrain; GCP structure scale and datum offset. "
                                   "GCP residual is an in-sample diagnostic, not validation. "
                                   "Six distributed points with stable leave-one-out error "
                                   "are needed for a stronger calibration claim; "
                                   "vertical datums must match.")
        elif r > 0.5 and a > 0:
            k = a
            cal = Calibration("dem-fit", scale_k=k, a=a, b=b, fit_r=r,
                              dem_coverage=coverage, scale_source="DEM low-frequency fit",
                              evidence_level="inferred",
                              ground_anchor_quantile=ground_q,
                              structure_upper_quantile=upper_q,
                              note="structure scale inferred from low-frequency DEM slope; "
                                   "verify against independent surface heights")
        else:
            k = SCENE_PRIORS.get(scene, SCENE_PRIORS["auto"]) / max(structure_span, 1e-6)
            cal = Calibration("dem+prior", scale_k=k, a=a, b=b, fit_r=r,
                              dem_coverage=coverage, scale_source="scene prior",
                              evidence_level="approximate",
                              ground_anchor_quantile=ground_q,
                              structure_upper_quantile=upper_q,
                              note=f"weak DEM correlation (r={r:.2f}); upper-tail structure "
                                   f"height assumed from '{scene}' prior. Metric heights are "
                                   "approximate until checked against independent reference.")
        if structure_span < 1e-3 and gcps is None:
            k = 0.0
            cal.method = "dem-only"
            cal.scale_k = 0.0
            cal.scale_source = "DEM only"
            cal.evidence_level = "terrain-only"
            cal.note = "relative model has insufficient structure contrast; only DEM terrain used"

        # A2: Correct coarse DEM for what it already contains (canopy and buildings)
        beta_map = {"urban": 0.6, "forest": 0.7, "sparse": 0.3, "hilly": 0.5, "auto": 0.5}
        beta = beta_map.get(scene, 0.5)
        block_size = max(1, int(round(dem_res_m / gsd)))
        coarse_struct = ndimage.uniform_filter(k * structure, size=block_size)
        dtm_terrain = dem - beta * coarse_struct

        dsm = dtm_terrain + datum_offset + k * structure

        # A3: 30m reference-consistent mode (matches Copernicus / SRTM 30m cell averages)
        if match_dem_30m:
            coarse_dsm = ndimage.uniform_filter(dsm, size=block_size)
            residual = dem - coarse_dsm
            correction = ndimage.gaussian_filter(residual, sigma=max(1.0, block_size / 2.0))
            dsm = dsm + correction
            dtm_terrain = dtm_terrain + correction

        cal.vertical_datum = vertical_datum
        cal.match_dem_30m = match_dem_30m
        cal.dem_canopy_corrected = True
        cal.is_agl = is_agl
        cal.dtm = dtm_terrain.astype(np.float32)
        cal.ndsm = np.maximum(dsm - dtm_terrain, 0.0).astype(np.float32)
        return dsm.astype(np.float32), "metre", cal

    # ---- 2. GCP affine -------------------------------------------------
    if gcps is not None and len(gcps[2]) >= 2:
        rows, cols, z = gcps
        samples = _sample(rel, rows, cols)
        if np.ptp(samples) < 1e-3:
            raise ValueError("GCPs do not span different relative heights; "
                             "cannot determine metric scale")
        a, b = huber_affine(samples, z)
        if not np.isfinite(a) or a <= 0:
            raise ValueError("GCPs imply a nonpositive height scale; "
                             "check coordinates and heights")
        pred = a * _sample(rel, rows, cols) + b
        r = float(np.corrcoef(pred, z)[0, 1]) if len(z) > 2 else None
        spread, loo, evidence = _gcp_quality(rows, cols, samples, z, rel.shape)
        return (a * rel + b).astype(np.float32), "metre", Calibration(
            "gcp-affine", a=a, b=b, fit_r=r, n_gcp=len(z),
            vertical_datum=vertical_datum,
            scale_source="GCP", evidence_level=evidence,
            gcp_residual_rmse_m=float(np.sqrt(np.mean((pred - z) ** 2))),
            gcp_loo_rmse_m=loo, gcp_spread_fraction=spread,
            note="GCP fit residual is in-sample; six distributed points with stable "
                 "leave-one-out error are needed for a stronger calibration claim. "
                 "Verify the vertical datum and validate with independent points")

    # ---- 3. relative ---------------------------------------------------
    note = "no DEM/GCP supplied" if image.georeferenced else "non-georeferenced input"
    return rel.astype(np.float32), "relative", Calibration(
        "relative", scale_source="none", evidence_level="relative", note=note)
