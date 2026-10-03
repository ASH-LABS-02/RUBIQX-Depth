# DepthWizard implementation and evidence status

Status as of **3 October 2026**. ✅ implemented in the current source ·
🟡 partial, experimental or awaiting validation · ⬜ no implementation documented.
These labels describe implementation scope; they do not certify accuracy,
performance, hardware compatibility or release readiness. Historical checks
and pending work are distinguished in [release readiness](release-readiness.md).

## 1. Data

| Item | Status | Notes |
|---|---|---|
| Cartosat-1 stereo pseudo-labels | ⬜ | Requires suitable stereo pairs and source/licence/split provenance |
| Multi-dataset training set | ✅ | GAMUS plus Urban 3D satellite data in v5/v6a; Vaihingen/Potsdam/DFC integration remains future work |
| Indian validation sites | ⬜ | No independent Indian/Cartosat height benchmark; historical DC and Quesenbank results have the evidence limits in [BENCHMARKS](BENCHMARKS.md) |
| Cartosat input handling | 🟡 | GeoTIFF stretch, CRS/GSD and sun-angle inputs; simulated panchromatic checks exist, actual Cartosat validation and pansharpening remain pending |
| Indian-condition augmentation | 🟡 | Blur/downsampling, haze/noise, colour/rotation/flip and panchromatic augmentation exist; transfer to Indian conditions is unvalidated |

## 2. Model

| Item | Status | Notes |
|---|---|---|
| Overhead fine-tuned backbone | ✅ | Current documented v6a: DA-V2-Base on GAMUS + Urban 3D; recorded RMSE 2.61 m on 30 GAMUS colour tiles at 0.33 m, 1.66 m on 159 Urban 3D tiles, 2.76 m on GAMUS simulated panchromatic at 0.6 m; no per-tile height fitting |
| Multi-task / EO foundation model | 🟡 | Optional semantic checkpoint experiment exists; licensing and independent building/canopy validation remain unresolved ([prototype](semantic-prototype.md)) |
| Metric height output | ✅ | v2 and later use metric training with resolution-matched tiling; the ±40 % v1 learned-scale description is historical |
| Diffusion refinement | ⬜ | |
| Physical cues / automatic anchors | 🟡 | Shadow geometry requires known solar elevation; OSM height/levels cues and guarded rescaling exist. Anchors remain calibration inputs, with provisional provenance, not independent validation |
| Ensembles + TTA | ✅ | 1/4/8-pass rotation inference; single-pass/heuristic outputs do not provide pixel uncertainty/reliability maps |
| Calibrated uncertainty | 🟡 | Provisional `sqrt(4.0² + (5.5 × spread)²)` m error model fitted on two historically inspected DC scenes; their references cannot independently validate the final fit. Other domains/routes unvalidated ([§5](BENCHMARKS.md)) |
| Distilled / fast model | 🟡 | ONNX export script, model caching, batched ensemble passes and opt-in fp16 exist; a measured fast-model acceptance result is pending |

## 3. Calibration and geodesy

| Item | Status | Notes |
|---|---|---|
| DEM fusion | ✅ | Surface/bare-earth detection; auto-fetched Copernicus uses a terrain proxy for fine imagery and surface consistency for coarse imagery. Terrain proxy is approximate |
| Vertical datum handling | 🟡 | Datum metadata, ellipsoidal GCP → EGM2008 and explicit [raster conversion](vertical-datum.md) among ellipsoidal/EGM96/EGM2008 exist. Real local PROJ grids are required; missing grids fail. Numerical/geodetic validation and arbitrary datum reconciliation remain pending |
| 30 m reference consistency | ✅ | Surface DEM native-cell agreement is calibration consistency, not independent accuracy |
| Robust GCP fit | ✅ | Robust fit, leave-one-out diagnostics, spread checks and provisional evidence labels |
| Active-learning GCP suggestions | 🟡 | Interactive pins and fit diagnostics exist; automatic suggestions remain pending |
| Stereo/multi-date photogrammetry | ⬜ | |

## 4. Post-processing

| Item | Status | Notes |
|---|---|---|
| Water flattening | ✅ | Conservative large-smooth-water mask; errors in water identification remain possible |
| Shadow pits | ✅ | Nonnegative above-ground structure; this does not establish shadow-height accuracy |
| Land-cover rules | 🟡 | Height/RGB vegetation rejection by default; optional semantic building/canopy masks remain a research prototype |
| LoD1 buildings | ✅ | Vector candidates, robust roof height, storeys, volume and confidence basis; detected footprints/heights require independent validation |
| Roof shapes / CityGML | 🟡 | Flat/plane/gable shape hypotheses and CityJSON LoD1/LoD2.0 output exist; unsupported roofs fall back to flat. Shapes are inferred, not surveyed; native CityGML remains pending |
| DSM / DTM / nDSM separation | ✅ | GeoTIFF products retain metric/relative units; the estimated DTM is not a surveyed terrain model |
| Off-nadir lean correction | ⬜ | |

## 5. Validation

| Item | Status | Notes |
|---|---|---|
| Full metric set | ✅ | RMSE, MAE, NMAD, bias, r, within 1/2/5 m, 30 m aggregate and per-building metrics when usable references are supplied |
| Per landscape + DEM baseline | ✅ | Supported metrics appear in app/report for referenced scenes; calibration-DEM agreement is labelled separately |
| Ablations | ✅ | Versioned historical model/pipeline/TTA experiments in [BENCHMARKS](BENCHMARKS.md); existing DC results are related-domain diagnostics |
| Independent future benchmark | 🟡 | [Manifest and evaluation workflow](independent-benchmark.md) implemented; one frozen [NRW diagnostic](nrw-cologne-diagnostic.md) scored with unresolved temporal/training provenance. Untouched scene-disjoint test sites and independent class labels remain pending |
| Public benchmark / paper | ⬜ | Broad validation and competitor superiority are not established by the current evidence |

## 6. Visualisation

| Item | Status | Notes |
|---|---|---|
| Textured City buildings | ✅ | Inferred flat/plane/gable overlays stand on displayed terrain; optional canopy trees are a display layer |
| AI facades / Gaussian splatting | 🟡 | Procedural floors/windows exist; AI facades and Gaussian splatting remain pending |
| LOD / globe mode | 🟡 | Mesh detail controls and spatial tree chunks with detailed/simplified representations exist; quality-dependent tree shadows and screen-size switching are display only. Both tree representations consume memory; globe/terrain tile streaming remain pending |
| Lighting | ✅ | ACES, soft shadows, normal-map hillshade, GTAO + SMAA + sky; quality/device limits still need measured checks |
| DEM vs DSM swipe | ✅ | Input DEM and DSM geometry comparison |
| Reliability overlay | ✅ | Ensemble agreement, not an accuracy probability; unavailable without a valid ensemble |
| Recorded flythrough | ✅ | 30 s cinematic recording when canvas capture/MediaRecorder are supported; browser/codec compatibility needs release checks |
| Walk navigation | 🟡 | Metric scenes only; clear-ground spawning, estimated footprint barriers and slope/step limits. Uses displayed terrain and does not establish pedestrian access or safety |
| VR / collaboration | 🟡 | WebXR immersive session support is implemented, gated on secure context/browser/headset support. Hardware acceptance remains pending; collaboration is not implemented |

## 7. Analysis tools

| Item | Status | Notes |
|---|---|---|
| Probe, profile, slope, aspect, curvature, 3D distance | ✅ | Measurements inherit source-grid, calibration and unit limitations |
| Cut / fill vs reference | ✅ | Requires compatible reference elevations/datum |
| Connected flood, depth, volume, buildings affected | ✅ | Screening assumptions are exposed; not a validated hydraulic model |
| Landslide susceptibility layer | ✅ | Screening index, not a probability or verified hazard assessment |
| Viewshed / line of sight | ✅ | Model-based visibility; does not establish communications service |
| Building statistics | ✅ | Candidate footprints/heights, not census or surveyed inventory |
| Rooftop solar potential | ✅ | Indicative assumptions only |
| Height-limit / density checks | 🟡 | Viewer height-limit check implemented; regulatory/field validation remains external |

## 8. Disaster management

| Item | Status | Notes |
|---|---|---|
| Change detection pre/post | ✅ | Height-loss map, volumes and roof-loss candidates; 11/12 historical simulated test is not real-event validation |
| Rapid response | 🟡 | Image → DSM + screening layers pipeline exists; current end-to-end timing on target hardware remains unmeasured |
| Offline field kit | 🟡 | Local app and PyInstaller build script; model files, DEM/geoid caches and clean-machine packaging need release checks |
| Damage report | ✅ | Report PDF/HTML and evidence JSON preserve estimate/provenance caveats |
| Bhuvan / NDMA integration | ⬜ | REST API exists; service integration is not demonstrated |

## 9. Time series and scale

Pairwise change comparison exists. Multi-date series, national tiling and
automatic ingestion have no implementation documented.

## 10. Engineering

| Item | Status | Notes |
|---|---|---|
| One-file desktop app | 🟡 | Build script/spec exist; clean-machine build, installation and soak checks remain pending |
| ONNX / TensorRT | 🟡 | ONNX export script exists; TensorRT acceptance is not demonstrated |
| Large scenes | 🟡 | Tiled inference, 64 MP input limit/downsampling and sampled viewer grids; target-device memory/performance acceptance remains pending |
| REST API + docs | ✅ | FastAPI `/docs`, products, CityJSON, PLY and change endpoints |
| Exports | ✅ | GeoTIFF, GLB, OBJ, PLY, CityJSON and export-all ZIP; sampled meshes/points are distinguished from full-grid DSM |
| Tests / CI | 🟡 | GitHub Actions workflow exists. The earlier 3 Oct semantic-prototype check recorded 72 pytest passes; this is not a fresh run for the latest LOD/modal/Walk changes |
| Model card / reproducibility | ✅ | [MODEL_CARD](MODEL_CARD.md), [BENCHMARKS](BENCHMARKS.md), raw historical results and future independent benchmark workflow |
| Modal accessibility | 🟡 | Upload/search/gallery/help focus containment, trigger restoration, topmost Escape and background interaction suspension implemented; browser/assistive-technology checks pending |
| Render profiling | 🟡 | Browser frame pacing, CPU submission and renderer counts can be exported with context. No measured FPS gain or GPU completion timing is claimed |
| Security / deployment scope | 🟡 | Single-user local app without authentication; broader deployment readiness is unestablished |

## 11. Product

| Item | Status | Notes |
|---|---|---|
| Role-based interfaces | ⬜ | |
| Docs and sample data | ✅ | README, TRAINING, BENCHMARKS, MODEL_CARD and versioned examples |
| Correction → retraining loop | ⬜ | Interactive calibration exists; automated retraining loop remains pending |

Next evidence work is new independent data and frozen evaluation settings.
Next release work is measured rendering/navigation, keyboard/assistive checks
and clean-machine packaging. Public competitor feature descriptions alone
cannot establish comparative accuracy, performance or overall superiority.
