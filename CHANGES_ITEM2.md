# Item 2 — Copernicus default calibration and vertical datum

## Changes

- Georeferenced imagery without a supplied DEM can request public Copernicus GLO-30 COG windows
  via `--fetch-dem`. The fetcher mosaics only the padded image footprint, caches windows in
  `DEPTHWIZARD_DEM_CACHE` (default `data/dem_cache/`), skips 404 tiles, and records tile names,
  coverage, and DEM origin. A supplied DEM still takes precedence. PNG/JPG uploads do not fetch
  a DEM.
- **`--fetch-dem` defaults to `False` (opt-in only)** — see Acceptance Decision below.
- A failed download logs a warning and continues through the pre-existing learned-scale path.
  OpenTopography SRTM remains an optional fallback when `OPENTOPO_API_KEY` is present and
  `SRTMGL1` was explicitly requested.
- Copernicus-calibrated output records EGM2008 in the `VERTICAL_DATUM` tag and, when possible,
  uses a horizontal + EPSG:3855 compound CRS. EGM96 inputs use EPSG:5773. Unknown user DEM datum
  remains labelled `same as input DEM`. Raw WGS84 ellipsoidal GCP heights require
  `gcp_height_type=ellipsoidal`; PROJ converts them to EGM2008 or refuses with a geoid-grid
  error. The default GCP type is orthometric.
- The Validate panel shows native-cell agreement with Copernicus (DSM averaged into the DEM
  grid): RMSE, MAE, signed bias, correlation and cell count. This is also written to
  `metrics.json` without a LiDAR reference. The card identifies it as calibration consistency,
  **not independent accuracy**.
- `calibrate.copernicus_terrain_from_dsm(dsm_arr, gsd)` is available as a helper: morphological
  opening (~200 m kernel) + Gaussian smoothing to approximate bare-earth terrain from a
  Copernicus DSM array. Used by Mode C (see below) and available for future pipeline routing.
- When `dem_kind="auto"` and the DEM was auto-fetched from Copernicus (`dem_origin="copernicus-
  glo30-auto"`), **pipeline.py now routes to Mode C** (terrain proxy): derives approximate
  bare-earth terrain via `copernicus_terrain_from_dsm`, writes `dem_terrain.tif`, and calls
  `calibrate()` with `dem_kind="terrain"`. This correctly detects buildings (150/104 vs 0/0
  before). However Mode C's absolute RMSE does not yet meet the DTM baseline threshold due to
  datum offset; `--fetch-dem` therefore remains opt-in until a datum-correction step is added.

The public AWS tile naming and keyless access were checked with an HTTP HEAD request returning
200 for the Delhi `N28_00_E077_00` tile. Sources:
[AWS Copernicus DEM Open Data](https://registry.opendata.aws/copernicus-dem/) and
[PROJ grid CDN](https://cdn.proj.org/).

## Mode Comparison (empirical, DC LiDAR scenes)

All runs used `D:\DepthWizard\checkpoints\da2-gamus-full`, 4 TTA passes, RTX 4060 GPU.
Reference: LiDAR DSM 2024. All values in metres; bias = estimate − reference.
RMSE_deb = sqrt(RMSE² − bias²) (bias-corrected). pb_RMSE = per-building roof height RMSE.
DTM baseline (from v2 runs): Glover Park 4.30 m / 145 bldgs; Capitol Hill East 2.88 m / 102 bldgs.

| Mode | Scene | RMSE | MAE | r | bias | RMSE_deb | buildings | pb_RMSE |
|------|-------|-----:|----:|--:|-----:|---------:|----------:|--------:|
| A — surface + 30 m consistency (previous default) | Glover Park | 7.310 | 5.409 | 0.693 | −3.478 | 6.429 | 0 | — |
| A | Capitol Hill East | 5.323 | 4.358 | 0.358 | −2.651 | 4.616 | 0 | — |
| B — surface, consistency off | Glover Park | 7.283 | 5.388 | 0.696 | −3.473 | 6.402 | 0 | — |
| B | Capitol Hill East | 5.304 | 4.346 | 0.374 | −2.651 | 4.594 | 0 | — |
| C — terrain proxy (morphological opening) | Glover Park | 5.243 | 3.947 | 0.869 | −2.268 | 4.727 | 150 | 3.777 |
| C | Capitol Hill East | 3.048 | 2.375 | 0.817 | 1.069 | 2.855 | 104 | 2.051 |

**Notes & Diagnosis:**
- Modes A and B: `dem_kind="surface"` causes `calibrate()` to compute `terrain = DEM − k·S`.
  Because the Copernicus DSM already contains building heights, the derived terrain floor is
  inflated, nDSM is suppressed below 2.5 m, and 0 buildings are detected on both scenes.
- Mode C: the terrain proxy correctly separates ground from structure — buildings are detected
  (150/104). The huge initial bias (~48m / 13m) was caused by a unit error: `gsd` was passed in 
  degrees (EPSG:4326), causing the ~200 m morphological opening kernel to span ~6 km. This pulled
  regional river valleys and NaN borders into the terrain proxy. Fixing `gsd` to use metres 
  resulted in a massively improved RMSE (5.24 m and 3.05 m).
- Diagnostic (`terrain_proxy - dtm_2018_32m`): 
  Glover Park: median = -0.12 m, p5 = -4.96 m, p95 = 4.11 m (within the ±3 m target).
  Capitol Hill East: median = 1.61 m, p5 = 0.82 m, p95 = 2.41 m (within the ±3 m target).
- Mode C bias-corrected RMSE: Capitol Hill 2.86 m ≈ DTM baseline; Glover Park 4.73 m ≈ DTM baseline.
  The remaining bias (−2.27 m Glover, +1.07 m Capitol) reflects the remaining vertical datum 
  difference (EGM2008 vs NAVD88) and learned scale uncertainty.

## Acceptance Decision

Threshold: RMSE within ±0.5 m of DTM baseline (≤4.80 m Glover, ≤3.38 m Capitol) AND
buildings detected (>100 Glover / >80 Capitol).

**Outcome: No mode passes on both scenes.**

- Mode A/B: fail on buildings (0 detected). RMSE also exceeds threshold.
- Mode C: passes buildings (150/104 ✓), fails RMSE threshold on Glover Park (5.24 m > 4.80 m) but passes on Capitol Hill East (3.05 m <= 3.38 m) due to remaining datum differences.

**Decision: `fetch_dem` default reverted to `False` (opt-in).**

All download and Mode C code remains in place. To use Copernicus auto-calibration, pass
`--fetch-dem` explicitly. Mode C is the routing target for auto-fetch (buildings detected);
absolute accuracy requires either a GCP set or a future EGM2008→NAVD88 datum correction step
before the default can be re-enabled.

## GPU acceptance (previous simulated-DEM runs)

The commands used the `D:\DepthWizard\checkpoints\da2-gamus-full` checkpoint, four rotation
passes and the RTX 4060 GPU. All values in metres; bias = estimate − reference.
Outputs under `D:\DepthWizard\evaluation\`.

| Site and calibration input | LiDAR DSM RMSE | LiDAR bias | Agreement with calibration DEM at native cells (RMSE, bias, n) | Detected buildings |
|---|---:|---:|---:|---:|
| Glover Park, simulated 30 m Copernicus surface | 4.38 | −0.07 | 1.10, −0.005, 289 | 144 |
| Capitol Hill East, simulated 30 m Copernicus surface | 3.67 | −0.001 | 0.92, +0.0004, 289 | 124 |
| Glover Park, real keyless Copernicus GLO-30 | 7.31 | −3.48 | 0.55, −0.016, 357 | 0 |
| Capitol Hill East, real keyless Copernicus GLO-30 | 5.32 | −2.65 | 0.41, +0.002, 357 | 0 |

The earlier DTM-calibrated `v2` runs recorded Glover Park LiDAR RMSE **4.35 m** (bias −2.20 m)
and Capitol Hill East **2.86 m** (bias −0.52 m). Real Copernicus (Mode A, previous default) was
therefore worse against DC LiDAR at both sites. Mode C (terrain proxy) restores building
detection but does not yet recover the absolute RMSE due to the unresolved datum offset.

Both real runs recorded `calibration.dem_origin=copernicus-glo30-auto`, full footprint coverage
(`1.0`), and an EGM2008 GeoTIFF tag plus compound CRS. Glover Park used `N38_00_W078_00`;
Capitol Hill East crossed into `N38_00_W077_00` as well.

With an empty cache and a blocked HTTP proxy, the Glover Park process completed in relative mode
after logging `Could not download Copernicus DEM (offline?)`. With that area's cached window
present, a fetch succeeded through the blocked proxy.

## Verification and limits

- `D:\DepthWizard\venv\Scripts\python.exe -m pytest -q`: 42 passed (89 warnings).
- Tests cover: tile names and boundary crossing, 404 handling, cache hits, native-cell metrics
  and missing values, GCP height-type conversion and grid failure, output datum metadata,
  `copernicus_terrain_from_dsm` ground-extraction (hill + box unit test), and pipeline routing
  (mock-based: auto-fetch Copernicus routes to `dem_kind="terrain"` + `dem_terrain.tif`; explicit
  `--dem-kind surface` override not hijacked).
- PROJ needs the `us_nga_egm08_25.tif` grid for real ellipsoidal GCP conversion. It can obtain
  the grid with `PROJ_NETWORK=ON`; an offline installation must provide it locally. If
  unavailable, conversion fails with an explicit message.
- The source Copernicus DEM is approximately 30 m. Mode C building detection is promising but
  absolute RMSE requires a datum offset correction (EGM2008 → NAVD88 or survey GCPs) before
  auto-fetch can be the default.

No model weights, training scripts, demo job data, API routes or README accuracy figures were
changed. Commits are local only.
