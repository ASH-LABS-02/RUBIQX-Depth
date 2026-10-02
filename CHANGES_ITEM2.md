# Item 2 — Copernicus default calibration and vertical datum

## Changes

- Georeferenced imagery without a supplied DEM now requests public Copernicus GLO-30 COG windows by default. The fetcher mosaics only the padded image footprint, caches windows in `DEPTHWIZARD_DEM_CACHE` (default `data/dem_cache/`), skips 404 tiles, and records tile names, coverage, and DEM origin. `--no-fetch-dem` and the upload checkbox opt out. A supplied DEM still takes precedence. PNG/JPG uploads do not fetch a DEM.
- A failed download logs a warning and continues through the pre-existing learned-scale path. OpenTopography SRTM remains an optional fallback when `OPENTOPO_API_KEY` is present and `SRTMGL1` was explicitly requested.
- Copernicus-calibrated output records EGM2008 in the `VERTICAL_DATUM` tag and, when possible, uses a horizontal + EPSG:3855 compound CRS. EGM96 inputs use EPSG:5773. Unknown user DEM datum remains labelled `same as input DEM`. Raw WGS84 ellipsoidal GCP heights require `gcp_height_type=ellipsoidal`; PROJ converts them to EGM2008 or refuses with a geoid-grid error. The default GCP type is orthometric.
- The Validate panel shows native-cell agreement with Copernicus (DSM averaged into the DEM grid): RMSE, MAE, signed bias, correlation and cell count. This is also written to `metrics.json` without a LiDAR reference. The card identifies it as calibration consistency, **not independent accuracy**.

The public AWS tile naming and keyless access were checked with an HTTP HEAD request returning 200 for the Delhi `N28_00_E077_00` tile. Sources: [AWS Copernicus DEM Open Data](https://registry.opendata.aws/copernicus-dem/) and [PROJ grid CDN](https://cdn.proj.org/).

## GPU acceptance

The commands used the `D:\DepthWizard\checkpoints\da2-gamus-full` checkpoint, four rotation passes and the RTX 4060 GPU. All values below are metres; bias is estimate minus reference. Outputs are under `D:\DepthWizard\evaluation\`.

| Site and calibration input | LiDAR DSM RMSE | LiDAR bias | Agreement with calibration DEM at native cells (RMSE, bias, n) | Detected buildings |
| --- | ---: | ---: | ---: | ---: |
| Glover Park, simulated 30 m Copernicus surface | 4.38 | −0.07 | 1.10, −0.005, 289 | 144 |
| Capitol Hill East, simulated 30 m Copernicus surface | 3.67 | −0.001 | 0.92, +0.0004, 289 | 124 |
| Glover Park, real keyless Copernicus GLO-30 | 7.31 | −3.48 | 0.55, −0.016, 357 | 0 |
| Capitol Hill East, real keyless Copernicus GLO-30 | 5.32 | −2.65 | 0.41, +0.002, 357 | 0 |

The earlier DTM-calibrated `v2` runs recorded Glover Park LiDAR RMSE **4.35 m** (bias −2.20 m) and Capitol Hill East **2.86 m** (bias −0.52 m). Real Copernicus is therefore worse against this DC LiDAR at both sites; the smaller native-cell scores above show consistency with the calibration source, not improved fine-resolution accuracy. The simulated 30 m surface was derived from LiDAR DSM data, so its LiDAR scores are not a blind independent evaluation. Real Copernicus uses EGM2008 heights; the DC LiDAR is NAVD88. No LiDAR-based offset was fitted, and the observed bias includes an uncorrected vertical-datum difference. The real surface calibration also suppressed building detections at these sites; it should not be used as evidence of building-height quality.

Both real runs recorded `calibration.dem_origin=copernicus-glo30-auto`, full footprint coverage (`1.0`), and an EGM2008 GeoTIFF tag plus compound CRS. Glover Park used `N38_00_W078_00`; Capitol Hill East crossed into `N38_00_W077_00` as well.

With an empty cache and a blocked HTTP proxy, the Glover Park process completed in relative mode after logging `Could not download Copernicus DEM (offline?)`. With that area's cached window present, a fetch succeeded through the blocked proxy. The offline check used a deliberately failing proxy rather than disabling the laptop network adapter.

## Verification and limits

- `D:\DepthWizard\venv\Scripts\python.exe -m pytest -q`: 39 passed (57 warnings).
- New internet-free tests cover tile names and boundary crossing, 404 handling, cache hits, native-cell metrics and missing values, GCP height-type conversion and grid failure, and output datum metadata.
- PROJ needs the `us_nga_egm08_25.tif` grid for real ellipsoidal GCP conversion. It can obtain the grid with `PROJ_NETWORK=ON`; an offline installation must provide it locally. If unavailable, conversion fails with an explicit message.
- The source Copernicus DEM is approximately 30 m. Agreement to that input cannot establish sub-metre or building-level accuracy, and datum differences should be resolved before interpreting raw cross-source RMSE.

No model weights, training scripts, demo job data, API routes or README accuracy figures were changed. Commits are local only.
