# Indian benchmark acquisition plan

Status: **not ready to score**. The local Bengaluru Sentinel-2 10 m RGB is an
input demonstration, not a paired independent building-height benchmark.

Use `evaluation/benchmark-manifest.example.json` and the strict preflight in
`scripts/validate_benchmark.py`; missing source/date/CRS/datum/licence/hash and
training-overlap evidence must fail preflight.

| Cohort | Required input/reference pair | Evaluation |
|---|---|---|
| Indian metro | RGB/PAN+RGB ≤1 m plus independently surveyed DSM and bare-earth DTM for the same AOI/time window | Unfitted DSM/AGL errors, ≥15/≥30 m objects, per-building heights, class masks and coverage |
| Hill settlement | Optical scene plus independently surveyed terrain/surface and documented vertical datum | Relief, slopes, shadow sensitivity, canopy/building separation and datum error |
| Coarse landscape | Sentinel-2 RGB plus an independent, dated terrain reference | Terrain/surface mode and resolution sensitivity; no individual-building claim at 10 m |

Reserve at least three geographically distinct development AOIs and three
untouched blind-test AOIs. Freeze the checkpoint/configuration before accessing
test references. Audit every model's training/pretraining AOIs; unknown overlap
prevents an independence claim. Keep calibration DEM/GCP/anchors separate from
scoring references. Do not use Copernicus both to calibrate and as independent
height truth for that run.

The [official SAC challenge repository](https://github.com/IMG-PROCESS-SAC/SIH-DepthWizard-2026)
currently points to remote-sensing dataset resources; it does not provide the
missing paired Indian survey in this checkout. Obtain actual survey products
and their terms before replacing the manifest placeholders. Record acquisition
date differences explicitly; construction/vegetation changes are not model error
alone. Independent water/building/canopy labels are also required.

Until these pairs exist, the completed code can ingest/score them but an Indian
accuracy number, calibrated Indian uncertainty or completed Cartosat transfer
cannot be reported.
