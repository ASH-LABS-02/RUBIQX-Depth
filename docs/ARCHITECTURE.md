# Architecture and command-line guide

[Back to README](../README.md)

## Data flow

RGB image → model inference → height calibration → DSM/DTM/nDSM → objects,
validation and exports → locally served Three.js viewer.

The Python pipeline is in `depthwizard/pipeline.py`; model inference is in
`depth.py`, calibration in `calibrate.py`, metrics in `metrics.py`, and public
Copernicus acquisition in `dem_fetch.py`. The browser is in `web/`; `server.py`
serves the application and processing APIs.

## Inference

The current v6a checkpoint is a Depth Anything V2 Base fine-tune using GAMUS aerial
and Urban 3D satellite imagery. Its metric above-ground output and resolution-aware
tiling must be distinguished from pretrained relative-depth inference. The
pretrained path aligns overlapping tile predictions with a global depth estimate;
tile processing and test-time augmentation reduce seams and expose prediction
spread, but cannot guarantee accuracy. See the [model card](MODEL_CARD.md) and
[training guide](../TRAINING.md).

Model loading failures stop processing by default. Web uploads never silently use
heuristic predictions. Explicit CLI `--allow-fallback` is for prototype plumbing
only, not accuracy evaluation.

## Height evidence and calibration

Plain non-georeferenced imagery produces unitless relative output. An assumed
horizontal GSD alone is not proof of absolute vertical scale. Georeferenced imagery
can use a terrain DEM, a surface DEM, GCPs or known-height anchors; output metadata
records the calibration evidence and units.

Copernicus GLO-30 and SRTM are surface models and can contain buildings and canopy.
A terrain proxy derived from them is an estimate of ground, not surveyed bare earth.
With estimated above-ground height `h` and ground `g`, the surface is `g + h` and
nDSM is DSM minus DTM. Surface consistency at coarse DEM resolution is different
from fitting the optional global above-ground scale.

Copernicus height scaling (`--cop-scale`) and multi-resolution blending remain
experimental and disabled by default after inconsistent measured improvement.
Automatic OSM/shadow anchors are subject to availability and rejection checks;
record their provenance and disable them for a controlled comparison.

Automatic Copernicus retrieval uses public AWS tiles and cached downloads.
OpenTopography fallback has separate service/key requirements. DEM coverage must
meet the pipeline threshold; missing coverage must not be treated as zero elevation.
Vertical datum declarations do not transform heights. GNSS/GCP conversion requires
the appropriate geoid grids; see [vertical datum handling](vertical-datum.md).

## Validation and uncertainty

References are scoring inputs. Never fit height predictions, calibration or model
selection to the reserved DC 2024 LiDAR files. Metrics include RMSE, MAE, bias,
correlation and available height-band/building diagnostics. A reference-aligned
shape score is not an absolute metric-height score. Same-survey calibration and
related-domain diagnostic results are labelled separately in
[benchmarks](BENCHMARKS.md).

Metric uncertainty is provisional, calibrated on two DC scenes. Relative ensemble
spread is unitless; single-pass runs do not provide an ensemble uncertainty map.
Reliability indicators are not accuracy probabilities.

## CLI examples

Run from the repository root with dependencies installed:

```powershell
# Relative input with explicit pretrained model
python -m depthwizard image.png --model small --gsd 0.6 -o output/relative

# Metric input using a local fine-tuned checkpoint and terrain evidence
python -m depthwizard image.tif --model models/da2-gamus-full --dem terrain.tif --dem-kind terrain -o output/metric

# Controlled scoring: reference is excluded from estimation
python -m depthwizard samples/dc_lidar/glover_park/rgb.tif --model models/da2-gamus-full --dem samples/dc_lidar/glover_park/dtm_2018_32m.tif --dem-kind terrain --ref samples/dc_lidar/glover_park/lidar_dsm_2024.tif --scene urban --no-auto-anchors --no-cop-scale -o output/glover-score

# Inspect all supported options
python -m depthwizard --help
```

`--gcp` accepts a control-point CSV; `--anchor ID:HEIGHT` and `--anchors` accept
known building heights. `--audit-stages` saves intermediate rasters without fitting
to the scoring reference. Automatic DEM retrieval is enabled by default for
georeferenced inputs and can be disabled with `--no-fetch-dem`.

## Outputs and serving

Outputs include height rasters, metadata/provenance, previews and viewer assets;
validation metrics require a reference. DSM/DTM/nDSM GeoTIFFs preserve raster values
and georeferencing. Decimated GLB/OBJ/PLY geometry is a visual derivative, not a
replacement for the raster. Available evidence exports include reports and ZIPs.
Relative exports remain explicitly unitless.

The server supports queued uploads, status, cancellation and retries. Full-resolution
Surface/Optical tile streaming has bounded tile/load budgets. Mission tools require
aligned terrain and suitable local metric coordinates; unsuitable distance frames
are rejected. See the [viewer guide](VIEWER.md) and
[execution report](roadmap-execution-20261004.md).

## Deployment

`docker compose up --build` serves port 8000, persists `data/`, and mounts `models/`.
The Docker image caches pretrained Small weights; it does not supply the v6a
checkpoint. Put that checkpoint in `models/da2-gamus-full` before using the metric
model. `/api/scenes` is the container healthcheck endpoint.

Production HTTPS/auth configuration is separate from local compose. Deployment,
credentials and endpoint checks are documented in [release readiness](release-readiness.md).
A prepared configuration is not proof of a deployed or healthy service.
