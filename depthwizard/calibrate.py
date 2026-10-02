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
    dem_origin: str | None = None
    dem_tile_names: list[str] | None = None
    scale_source: str | None = None
    evidence_level: str | None = None
    ground_anchor_quantile: float | None = None
    structure_upper_quantile: float | None = None
    gcp_residual_rmse_m: float | None = None
    gcp_loo_rmse_m: float | None = None
    gcp_spread_fraction: float | None = None
    dem_kind: str | None = None             # "surface" (COP30/SRTM) or "terrain" (bare-earth DTM)
    dem_hp_r: float | None = None           # structure vs DEM high-pass correlation at DEM scale
    dem_resolution_m: float | None = None
    reference_consistent: bool | None = None
    consistency_rmse_m: float | None = None # our DSM vs DEM, both averaged to DEM cells
    learned_scale_k: float | None = None
    shadow_iou: float | None = None
    sun_azimuth_deg: float | None = None
    sun_elevation_deg: float | None = None
    structure_model: str | None = None
    vertical_datum: str | None = None
    match_dem_30m: bool | None = None
    is_agl: bool | None = None
    note: str = ""
    dtm: np.ndarray | None = None
    ndsm: np.ndarray | None = None

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items()
                if v is not None and k != "extras" and not isinstance(v, np.ndarray)}


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


def _ellipsoidal_to_egm2008(row: np.ndarray, col: np.ndarray, z: np.ndarray, image) -> np.ndarray:
    """Convert raw WGS84 GNSS heights with the real EGM2008 geoid grid."""
    if not image.georeferenced or image.crs is None:
        raise ValueError("ellipsoidal GCP heights require a georeferenced image")
    try:
        from pyproj import Transformer, network
        from rasterio.warp import transform
        if os.environ.get("PROJ_NETWORK", "ON").upper() not in {"OFF", "NO", "FALSE", "0"}:
            network.set_network_enabled(True)
        x, y = image.transform * (col, row)
        lon, lat = transform(image.crs, "EPSG:4326", list(x), list(y))
        converter = Transformer.from_crs("EPSG:4979", "EPSG:4326+3855",
                                         always_xy=True, allow_ballpark=False, only_best=True)
        _, _, height = converter.transform(lon, lat, z.tolist(), errcheck=True)
        height = np.asarray(height, dtype=np.float64)
        if not np.isfinite(height).all():
            raise ValueError("EGM2008 conversion returned invalid heights")
        return height
    except (ImportError, Exception) as exc:  # noqa: BLE001
        raise RuntimeError("Cannot convert ellipsoidal GCP heights to EGM2008: "
                           "install pyproj and the us_nga_egm08_25.tif PROJ grid "
                           "or enable PROJ_NETWORK=ON. Heights were not mixed.") from exc


def load_gcps(path: str | Path, image, *, gcp_height_type: str = "orthometric") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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
    if gcp_height_type not in {"orthometric", "ellipsoidal"}:
        raise ValueError("gcp_height_type must be orthometric or ellipsoidal")
    if gcp_height_type == "ellipsoidal":
        z = _ellipsoidal_to_egm2008(row, col, z, image)
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
DATUMS = {"COP30": "EGM2008 geoid (Copernicus GLO-30)", "SRTMGL1": "EGM96 geoid (SRTM)",
          "CARTODEM": "EGM96 geoid (CartoDEM)"}


def dem_resolution_m(dem_path) -> float:
    import rasterio
    with rasterio.open(dem_path) as src:
        rx = abs(src.transform.a)
        if src.crs is not None and src.crs.is_geographic:
            lat = src.bounds.bottom + (src.bounds.top - src.bounds.bottom) / 2
            return float(rx * 111_320 * np.cos(np.radians(lat)))
        return float(rx)


def extract_structure(rel: np.ndarray, gsd: float, agl: bool, dem_res_m: float = 30.0):
    """Above-ground structure in normalised units, zero on bare ground.

    AGL-trained (GAMUS) checkpoints already predict height above ground, so
    the lower tail is ground and nothing is high-passed away; on DC LiDAR this
    raised correlation with the true nDSM from 0.65/0.62 (old high-pass) to
    0.77/0.66. Generic depth models mix terrain and structure, so they keep
    the DEM-scale high-pass with a lower-tail ground anchor."""
    if agl:
        return np.maximum(rel - np.percentile(rel, 2.0), 0.0).astype(np.float32), "agl-lower-tail"
    sigma = max(1.0, 0.5 * dem_res_m / gsd)
    hp = rel - ndimage.gaussian_filter(rel, sigma)
    return np.maximum(hp - np.percentile(hp, 30.0), 0.0).astype(np.float32), "highpass-q30"


def _surface_fit(structure, dem, n):
    """Fit DEM ≈ terrain + k·mean(structure) at DEM scale using high-passed
    fields (terrain is smooth). Returns (k, r)."""
    S = ndimage.uniform_filter(structure.astype(np.float64), size=n)
    D = dem.astype(np.float64)
    hpS = S - ndimage.gaussian_filter(S, 1.5 * n)
    hpD = D - ndimage.gaussian_filter(D, 1.5 * n)
    st = max(1, n // 2)
    x, y = hpS[::st, ::st].ravel(), hpD[::st, ::st].ravel()
    if x.std() < 1e-9 or y.std() < 1e-9:
        return 0.0, 0.0, S
    r = float(np.corrcoef(x, y)[0, 1])
    k, _ = huber_affine(x, y)
    return float(k), r, S


def calibrate(rel: np.ndarray, image, *, dem_path=None, gcp_path=None,
              scene: str = "auto", dem_res_m: float | None = None, agl: bool = False,
              learned_scale: float | None = None, dem_kind: str = "auto",
              reference_consistent: bool = True, sun_elevation: float | None = None,
              sun_azimuth: float | None = None, dem_source: str | None = None,
              is_agl: bool | None = None, match_dem_30m: bool | None = None,
              vertical_datum: str | None = None,
              gcp_height_type: str = "orthometric"):
    """Return (height_map, units, Calibration).

    Scale evidence priority for the structure (buildings/trees) component:
      GCPs  >  surface-DEM fit  >  shadow consistency  >  learned pixel-footprint
      scale (AGL checkpoints)  >  scene prior.
    """
    # compatibility with the earlier keyword names
    if is_agl is not None:
        agl = bool(is_agl)
    if match_dem_30m is not None:
        reference_consistent = bool(match_dem_30m)
    gsd = image.pixel_size_m or 1.0
    gcps = load_gcps(gcp_path, image, gcp_height_type=gcp_height_type) if gcp_path else None
    if gcps is not None and gcp_height_type == "ellipsoidal" and dem_path:
        datum_name = vertical_datum or DATUMS.get((dem_source or "").upper(), "")
        if "EGM2008" not in datum_name:
            raise ValueError("ellipsoidal GCPs were converted to EGM2008; "
                             "the supplied DEM must explicitly use EGM2008 as well")

    # ---- 1. DEM fusion -------------------------------------------------
    if dem_path and image.georeferenced:
        dem, coverage = dem_to_grid(dem_path, image, return_coverage=True)
        res = dem_res_m or dem_resolution_m(dem_path)
        n = max(3, int(round(res / gsd)))
        structure, smodel = extract_structure(rel, gsd, agl, res)
        span = float(np.percentile(structure, 98))
        k_dem, r_hp, S = _surface_fit(structure, dem, n)
        kind = dem_kind
        if kind == "auto":
            kind = "surface" if (r_hp > 0.3 and k_dem > 0) else "terrain"
        datum = vertical_datum or DATUMS.get((dem_source or "").upper(), "same as input DEM")
        cal = Calibration("dem-fusion", dem_coverage=coverage, dem_kind=kind, dem_hp_r=r_hp,
                          dem_resolution_m=res, structure_model=smodel, vertical_datum=datum,
                          learned_scale_k=learned_scale)
        datum_offset = 0.0
        k = None
        if gcps is not None and len(gcps[2]) >= 2:
            rows, cols, z = gcps
            base = dem - (k_dem * S if kind == "surface" and k_dem > 0 else 0.0)
            resid = z - _sample(base, rows, cols)
            sample_structure = _sample(structure, rows, cols)
            if np.ptp(sample_structure) < 1e-3:
                raise ValueError("GCPs do not span different relative heights; "
                                 "cannot determine structure scale")
            k, datum_offset = huber_affine(sample_structure, resid)
            if not np.isfinite(k) or k <= 0:
                raise ValueError("GCPs imply a nonpositive structure scale; "
                                 "check coordinates, heights, and vertical datum")
            spread, loo, evidence = _gcp_quality(rows, cols, sample_structure, resid, rel.shape)
            cal.method, cal.scale_source, cal.evidence_level = "dem+gcp", "GCP", evidence
            cal.n_gcp, cal.datum_offset_m = len(z), datum_offset
            cal.gcp_residual_rmse_m = float(np.sqrt(np.mean(
                (k * sample_structure + datum_offset - resid) ** 2)))
            cal.gcp_loo_rmse_m, cal.gcp_spread_fraction = loo, spread
            cal.note = "Structure scale and datum offset from GCPs; GCP residual is in-sample."
        elif kind == "surface" and k_dem > 0:
            k = k_dem
            cal.method, cal.scale_source, cal.evidence_level = "dem-surface-fit", "surface DEM", "measured"
            cal.note = (f"The DEM is a surface model (building/canopy signal r={r_hp:.2f}); "
                        "structure scale fitted where the DEM sees buildings; terrain = DEM "
                        "minus the fitted structure mean.")
        if k is None and sun_elevation:
            from .shadows import fit_scale
            base = dem
            fit = fit_scale(structure, base, image.rgb, gsd, float(sun_elevation), sun_azimuth)
            ok = (fit is not None and 0.02 < fit.shadow_fraction < 0.25 and fit.iou > 0.25
                  and 1.0 < fit.k < 190.0)
            if fit is not None:
                cal.shadow_iou, cal.sun_azimuth_deg = fit.iou, fit.azimuth_deg
                cal.sun_elevation_deg = float(sun_elevation)
            if ok:
                k = fit.k
                cal.method, cal.scale_source, cal.evidence_level = "dem+shadow", "shadow consistency", "measured"
                cal.note = (f"Structure scale chosen so rendered shadows match observed shadows "
                            f"(IoU {fit.iou:.2f}, sun az {fit.azimuth_deg:.0f}°"
                            f"{' estimated' if fit.azimuth_estimated else ''}).")
        if k is None and learned_scale:
            k = float(learned_scale)
            cal.method, cal.scale_source, cal.evidence_level = "dem+learned-scale", "learned pixel-footprint scale", "approximate"
            cal.note = ("Structure heights from the fine-tuned model's learned metre-per-pixel "
                        "scale (±40 % typical); add GCPs or a surface DEM for measured scale.")
        if k is None:
            k = SCENE_PRIORS.get(scene, SCENE_PRIORS["auto"]) / max(span, 1e-6)
            cal.method, cal.scale_source, cal.evidence_level = "dem+prior", "scene prior", "approximate"
            cal.note = (f"No scale evidence; upper-tail structure height assumed from the "
                        f"'{scene}' prior. Metric heights are approximate.")
        if span < 1e-3 and gcps is None:
            k = 0.0
            cal.method, cal.scale_source, cal.evidence_level = "dem-only", "DEM only", "terrain-only"
            cal.note = "model found no structure contrast; only DEM terrain used"
        cal.scale_k = float(k)
        if kind == "surface":
            terrain = dem - k * S                       # DEM already contains mean structure
            dsm = terrain + datum_offset + k * structure
            if reference_consistent:
                # force agreement with the reference DEM at its own resolution
                resid = dem - ndimage.uniform_filter(dsm, size=n)
                dsm = dsm + ndimage.gaussian_filter(resid, n / 3)
            cal.reference_consistent = bool(reference_consistent)
        else:
            dsm = dem + datum_offset + k * structure
            cal.reference_consistent = False
        agg = ndimage.uniform_filter(dsm, size=n) - (dem if kind == "surface" else dem + k * S)
        cal.consistency_rmse_m = float(np.sqrt(np.mean(agg[n:-n, n:-n] ** 2)))
        ground = (dem - k * S if kind == "surface" else dem) + datum_offset
        cal.extras = {"dtm": ground.astype(np.float32), "ndsm": (k * structure).astype(np.float32),
                      "dem": dem.astype(np.float32)}
        cal.dtm, cal.ndsm = cal.extras["dtm"], cal.extras["ndsm"]
        cal.match_dem_30m, cal.is_agl = bool(cal.reference_consistent), bool(agl)
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
            vertical_datum=(DATUMS["COP30"] if gcp_height_type == "ellipsoidal"
                            else vertical_datum or "same as supplied orthometric GCP heights"),
            scale_source="GCP", evidence_level=evidence,
            gcp_residual_rmse_m=float(np.sqrt(np.mean((pred - z) ** 2))),
            gcp_loo_rmse_m=loo, gcp_spread_fraction=spread,
            note="GCP fit residual is in-sample; six distributed points with stable "
                 "leave-one-out error are needed for a stronger calibration claim. "
                 "Verify the vertical datum and validate with independent points")

    # ---- 3. relative ---------------------------------------------------
    note = "no DEM/GCP supplied" if image.georeferenced else "non-georeferenced input"
    cal = Calibration("relative", scale_source="none", evidence_level="relative", note=note,
                      learned_scale_k=learned_scale)
    if agl and learned_scale:
        cal.note += ("; heights above ground can be read approximately in metres "
                     f"using the learned scale ({learned_scale:.1f} m per unit)")
    return rel.astype(np.float32), "relative", cal

def apply_height_anchors(ndsm_raw: np.ndarray, labels: np.ndarray, anchors: list[dict], est_heights: dict[int, float]) -> tuple[float, dict]:
    """
    Apply global scale reference using known building heights.
    anchors: list of dicts with 'building_id' and 'height_m'
    est_heights: dict of building_id -> original p70 roof height (from extract_buildings)
    Returns (scale_factor, stats)
    """
    ratios = []
    stats = {
        "n_used": 0,
        "rejected": [],
        "ratios": [],
        "cv": 0.0,
        "loo_rmse": None,
        "warning": None
    }
    
    for anchor in anchors:
        b_id = anchor.get("building_id")
        known_h = anchor.get("height_m")
        if b_id is None or known_h is None:
            stats["rejected"].append({"anchor": anchor, "reason": "missing building_id or height_m"})
            continue
            
        est_h = est_heights.get(b_id)
        if est_h is None:
            stats["rejected"].append({"anchor": anchor, "reason": "building not found or has no height"})
            continue
            
        if est_h < 1.5:
            stats["rejected"].append({"anchor": anchor, "reason": f"estimated height {est_h:.1f} m is under 1.5 m"})
            continue
            
        r_i = known_h / est_h
        ratios.append(r_i)
        stats["ratios"].append({"building_id": b_id, "est": est_h, "known": known_h, "ratio": r_i})
        
    stats["n_used"] = len(ratios)
    
    if len(ratios) == 0:
        return 1.0, stats
        
    s = float(np.median(ratios))
    s = min(max(s, 0.5), 3.0)
    
    if len(ratios) > 1:
        cv = float(np.std(ratios) / np.mean(ratios))
        stats["cv"] = cv
        if cv > 0.25:
            stats["warning"] = f"Anchors disagree: coefficient of variation of ratios above 25% ({cv:.2f})"
            
    if len(ratios) >= 3:
        # Leave-one-out error
        errors = []
        for i in range(len(ratios)):
            loo_s = np.median(ratios[:i] + ratios[i+1:])
            errors.append((loo_s * stats["ratios"][i]["est"]) - stats["ratios"][i]["known"])
        stats["loo_rmse"] = float(np.sqrt(np.mean(np.square(errors))))
        
    return float(s), stats
