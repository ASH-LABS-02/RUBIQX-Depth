# Roadmap execution · 4 October 2026

This follows the approved order. “Implemented” describes source behaviour,
not certified accuracy, emergency readiness or a verified cloud deployment.
The production v6a checkpoint was not overwritten. The reserved 2024 DC DSMs
were used only for diagnostic scoring, never training, fitting or selection.

## All 20 items

| # | Change | Delivered / measured | Remaining evidence or constraint |
|---|---|---|---|
| 1 | Trace height loss | `--audit-stages` saves pre-normalisation, normalised, median, calibrated, water, Copernicus and final height stages plus roof extraction. The Glover diagnostic locates most tall-object underestimation before water/calibration. | It is related-domain, historically inspected data; bands use an estimated DTM, not independent building/tree truth. |
| 2 | Audit water labels | Aligned label scorer reports water/nonwater IoU, precision and recall; unknown labels are ignored explicitly. No water was detected on the six development crops, so no water threshold was changed. | Independent water labels are missing. A visually plausible mask is not validation. |
| 3 | Improve tall objects | Two small development epochs, height-band loss/selection and a protected baseline gate. The selected epoch-1 candidate improves overall/very-tall error; rare-tail normalisation was tested separately and remains OFF. | Candidate not promoted: middle-height error worsened; canopy and untouched geographic sites are unvalidated. |
| 4 | Semantic masks/licensing | Building/canopy scoring workflow retained; production now refuses a semantic checkpoint without verified licence and training-provenance fields. Prototype remains optional. | The candidate publisher's licence and split evidence remain unresolved. The guard cannot grant rights or verify a publisher's assertions. |
| 5 | Indian benchmark | Existing strict manifest workflow and an [India acquisition plan](india-benchmark-plan.md), separating urban/building and hill/terrain evaluation. | Bengaluru 10 m imagery has no paired independent DSM/labels. No Indian accuracy result is claimed. |
| 6 | Better uncertainty | Bias-inclusive ±1σ/±2σ coverage by height band, optional reference class, and calibration route; visible in Validate for new scored jobs. | Existing uncertainty constants remain provisional. No new fit without separate development references and class labels. |
| 7 | Resolution/terrain dependence | Six frozen GAMUS validation crops at 0.33, 0.65, 1.5, 2.5 and 3 m; reproducible raw/median/water diagnostics. | Urban development subset only; keep the 2.5 m mode cutoff pending broader terrain evidence. |
| 8 | Datum/coordinates | Official EGM96/EGM2008 grids numerically checked at four locations and through a raster round trip. Main live lat/lon uses source affine + PROJ rather than corner interpolation. | Numerical consistency is not certification against surveyed control points; missing/unknown source datum still requires evidence. |
| 9 | Stream large terrain | Bounded height/texture windows; Surface/Optical quadtree on metric scenes >2048 px, ≤48 tiles and ≤3 concurrent loads. Parent stays until four children load; failures retain it. Nodata is omitted from triangles. | Other layers and analysis still use saved display grids. Maximum saved texture is 4096 px. This is not unlimited-resolution GIS streaming. |
| 10 | Adaptive quality | Balanced quality uses measured P95 frame pacing, hysteresis, AO/shadow/pixel-ratio adjustments and recorded adaptive context. Idle/hidden gaps cannot lower quality. | No controlled before/after RTX 4060 speed claim. |
| 11 | Tree transitions | 250 ms alpha-hash crossfade between nearby branching crowns and distant representations; metric placement preserved. | Illustrative trees, not surveyed positions/species; both representations remain in memory and overlap briefly during transitions. |
| 12 | Building projection | Wall UV axis selection fixes collapsed facade patterns on diagonal walls; observed roof texture projection retained. | Walls remain procedural because a single overhead image does not observe facades. |
| 13 | Saved views/undo | Per-scene local viewpoints save camera position/up vector, target, layer and exaggeration. Four bounded transactional calibration snapshots; failed edits roll back and the last edit can be undone. | Browser-local viewpoints; 256 MB maximum per calibration snapshot. |
| 14 | Validation/provenance | Height/class/route coverage tables state their basis; absent class truth stays unknown. Intermediate rasters are reference-free; scoring is separate. | Historical scenes need reprocessing to gain the new stratified panel. |
| 15 | Job recovery | Queue position, estimated stage progress, cooperative cancellation between stages/inference tiles, persisted status and retry from saved uploads. Incomplete scenes are omitted from the gallery. | An in-flight model forward completes before cancellation. Stage percentages are estimates, not elapsed-time predictions. |
| 16 | Drainage/flow flood | Bounded, mass-conserving four-neighbour surface flow, rainfall, infiltration and drainage loss; downloadable evidence and sampled-grid 3D runoff depth overlay. | Closed crop, no upstream/outfall/sewer network, sub-grid drainage omitted; conservation tests only, no real flood-event validation. Existing bathtub scenario remains separate. |
| 17 | Route constraints | Aligned road preference, access exclusion and provisional uncertainty cutoff. Exact-grid binary GeoTIFF upload, provenance/hash and rejection counts/reasons. | Roads/access must be supplied; absent data is unknown. No bridges, traffic, debris or verified structural safety. |
| 18 | ONNX benchmark | Matched CPU timing and raw metric parity for FP32 and INT8 MatMul weights. Both failed the frozen deployment gate, so production remains PyTorch. | Two development inputs, fixed 518 input; not a full DSM/reference benchmark or AWS timing. |
| 19 | Regression/release | Python/API tests, all frontend syntax checks, Node regression checks in CI, synthetic 3072² streaming QA, desktop/mobile browser checks and source/hash release audit. | CI/clean-machine Windows package/Docker execution and target GPU acceptance require their respective environments. |
| 20 | AWS deployment hardening | Production compose + Caddy HTTPS, mandatory authentication in production, cross-origin mutation guard, health check and build/model/UI identity. | AWS CLI reports no credentials; no SSH deployment key or Docker is available locally. No live server change or TLS certificate is claimed. |

## Development model experiment

128 GAMUS training tiles, 24 fixed validation tiles, two epochs, 518 network
input, BF16 and batch 1 with gradient accumulation 2. Peak VRAM was 2.51 GiB.
These are development scores, not the historical 30-tile test benchmark.

| Metric | Starting v6a | Selected epoch 1 |
|---|---:|---:|
| Mean per-tile unfitted AGL RMSE | 2.415 m | 2.346 m |
| Pooled 2.5–15 m AGL RMSE | 3.258 m | 3.383 m |
| Pooled 15–30 m AGL RMSE | 4.807 m | 4.715 m |
| Pooled ≥30 m AGL RMSE | 4.848 m | 4.539 m |
| ≥30 m bias | −3.106 m | −2.574 m |

Height bands combine buildings and vegetation; there are no independent class
labels in this experiment. Production stays at v6a because the middle-height
band deteriorated. Epoch 2 did not beat epoch 1 on the balanced selector.

Local files:

- Dataset: `D:/DepthWizard/GAMUS/images` and `D:/DepthWizard/GAMUS/heights`.
- Selected candidate: `D:/DepthWizard/checkpoints/da2-roadmap-tall-development-20261004/model.safetensors`.
- Candidate processor/config are alongside the weights.
- `last.pt` in that directory is the **epoch-2 training/optimizer resume state**;
  it is not the selected inference checkpoint.
- `selected-hf-receipt.json` identifies selected weights and their development scores.
- Original production: `D:/DepthWizard/checkpoints/da2-gamus-full`.

## Resolution and normalisation experiment

Fixed seed 2404: DC_32_21, DC_40_62, PHL_6369, PHL_6415, PHL_6501,
PHL_6705, centre crops only; four-pass rotation inference.

| Image GSD | Current mean AGL RMSE | Experimental uncapped metric tail |
|---|---:|---:|
| 0.33 m | 2.39960 m | 2.40015 m |
| 0.65 m | 2.43572 m | 2.43685 m |
| 1.5 m | 2.51525 m | 2.51527 m |
| 2.5 m | 2.65112 m | 2.65350 m |
| 3 m | 2.74453 m | 2.74740 m |

The optional `--preserve-metric-tail` retains predictions above the normalisation
99.5th percentile. It is OFF: mean RMSE did not improve at any tested resolution.
These results do not select a terrain-mode cutoff or establish satellite/forest transfer.

## Stage diagnosis

The historical Glover run used the supplied 2018 terrain DEM, disabled automatic
anchors/Copernicus height correction, and scored the reserved 2024 LiDAR only
after inference. Its 1 m reference was bilinearly aligned to the 0.5 m RGB grid.
Final DSM RMSE was 4.454 m; the pre-normalisation metric AGL already had 4.448 m
RMSE against reference-minus-calibration-DTM. Therefore normalisation/water
processing is not the main explanation for this scene's underestimation.
Reference differences can also contain 2018–2024 changes and ground error.
No production setting was selected from this diagnostic.

Coverage on this scene was 76.3% within ±1σ overall but 0% for ≥30 m proxy AGL.
That illustrates why the two-scene uncertainty fit cannot be treated as a
general confidence guarantee or verified route-safety criterion.

## ONNX gate

Four CPU threads, two development images, warmup excluded, three repeats each.
Timing includes only forward execution; shared preprocessing is excluded.

| Engine | Median | Speedup | Raw metric parity MAE / P99 | Decision |
|---|---:|---:|---:|---|
| PyTorch FP32 | 4.651 s | 1× | baseline | Current |
| ONNX FP32 | 4.426 s | 1.05× | 0.000001 / 0.000014 m | Rejected: <1.1× speed gate |
| ONNX INT8 MatMul | 3.026 s | 1.54× | 0.149 / 1.485 m | Rejected: >0.05 m MAE / >0.25 m P99 |

No 2–3× CPU or AWS speed claim is made. Optional dependencies are in
`requirements-onnx.txt`; exported weights remain outside Git.

## Geodesy

[Official PROJ grids](https://cdn.proj.org/) `us_nga_egm96_15.tif` and
`us_nga_egm08_25.tif` were checked at DC, Bengaluru, the Himalaya and Cologne.
Manual bilinear grid offsets and PROJ agree within 0.0001 m. The synthetic raster
ellipsoid → EGM2008 → ellipsoid round-trip maximum error is 1.42×10⁻¹⁴ m.
This checks signs/interpolation/code consistency, not survey accuracy.

## Local regression and browser evidence

- `pytest -q`: **90 passed**, 174 dependency/deprecation warnings, no failures.
- Node frontend tests: **15 passed**; every `web/*.js` syntax check passed.
- Glover Park City rendered with branching dark-green trees and no console
  errors. Saved view → Top down → Restore returned the original orientation.
- The runoff overlay showed sampled water depth, and hide removed it. Its
  50 mm / 60 minute scenario accounted for 13,109 m³ rainfall, 1,311 m³ losses
  and 11,798 m³ storage with reported zero mass-balance discrepancy. This is a
  conservation check, not validation against a flood event. The exposure
  counters belong to the separate water-level scenario.
- At 390 × 844 the import dialog fit the viewport; document width was 390 px.
  This is a responsive spot check, not full accessibility acceptance.
- A local PNG upload was cancelled, retried from its saved inputs and cancelled
  again through the UI. Final stopped labels and enabled retry were verified;
  cancelled jobs did not replace the current scene. No console errors occurred.
- A synthetic 3072² metric scene loaded the Optical terrain quadtree without
  console errors. Display smoothing/spike controls are disabled while original
  DSM tiles are streamed. Other layers retain their saved display mesh.

| 30 s local capture | Frames | FPS | Frame P95 | Streaming |
|---|---:|---:|---:|---|
| [Topo](performance/topo-qa-20261004.json) | 1,267 | 42.27 | 26.1 ms | Saved display mesh |
| [Optical](performance/streaming-qa-20261004.json) | 925 | 30.83 | 33.9 ms | 5 tiles, 0 failures |

These are different layer/adaptive states on the reported **Intel UHD** browser
renderer at 1280 × 720, not a controlled speed comparison or RTX 4060 result.
Profiles were exported from visible DOM output; the profile download interaction
was not verified. [Current city screenshot](images/roadmap-current.png).

The [local release audit](../evaluation/roadmap-20261004-local-release.json)
passed served-source/version/model metadata checks. It records the tested source
commit before this documentation commit, with matching app/UI hashes and
`source_dirty=false` for the application source directories.
Authentication was off for localhost QA.
The [AWS audit](../evaluation/roadmap-20261004-live-release.json) timed out on
root, health, scenes and model endpoints on 4 October. No remote revision or
availability was established.

## Reproduce and inspect

[Machine-readable experiment evidence](../evaluation/roadmap-20261004-evidence.json)
records exact scores, selected checkpoint hash and scope. Local large artifacts
remain under `D:/DepthWizard/evaluation/`.

```powershell
$py = 'D:/DepthWizard/venv/Scripts/python.exe'
& $py -m pytest -q
node --test tests/*.test.mjs
& $py scripts/check_release.py --base-url http://127.0.0.1:8010 --timeout 10
& $py scripts/evaluate_development.py --root D:/DepthWizard/GAMUS --model D:/DepthWizard/checkpoints/da2-gamus-full --count 6 --out D:/DepthWizard/evaluation/new-resolution-audit
& $py scripts/benchmark_onnx.py D:/DepthWizard/checkpoints/da2-gamus-full --images IMAGE1.png IMAGE2.png --out D:/DepthWizard/evaluation/new-onnx-audit
& $py scripts/verify_geodesy.py --grid-dir D:/DepthWizard/dem_cache/proj --out D:/DepthWizard/evaluation/new-geodesy-audit
```

For production, set domain/build identity and user/password via deployment
environment variables, put the checkpoint in `models/da2-gamus-full`, and use
`docker compose -f docker-compose.production.yml up --build -d`. Never commit
credentials. A domain/DNS, authorised cloud credentials and actual remote
release/HTTPS checks are still required before declaring deployment complete.
