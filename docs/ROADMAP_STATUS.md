# DepthWizard vs the 11-part "no time limit" roadmap

Status as of 30 Sep 2026. ✅ done and tested · 🟡 partial / prototype · ⬜ not started.
Competitor columns summarise what their public material shows (see
`COMPETITOR_ANALYSIS_AND_UPGRADE_PLAN.txt`).

## 1. Data
| Item | Status | Notes |
|---|---|---|
| Cartosat-1 stereo pseudo-labels | ⬜ | needs ISRO stereo pairs; top training priority once available |
| Multi-dataset training set | 🟡 | GAMUS train/val (≈40 GB) used; Vaihingen/Potsdam/DFC not yet |
| Indian validation sites | ⬜ | DC LiDAR + Quesenbank forest used meanwhile (`docs/BENCHMARKS.md`) |
| Cartosat input handling | 🟡 | any-bit-depth GeoTIFF stretch, CRS/GSD, sun angles as inputs; no pansharpening yet |
| Indian-condition augmentation | 🟡 | colour/rotation/flip augmentation; no haze/cloud-shadow model |

## 2. Model
| Item | Status | Notes |
|---|---|---|
| Overhead fine-tuned backbone | ✅ | DA-V2-Base on GAMUS (v2): 2.62 m RMSE, r 0.84 on 30 held-out tiles, no per-tile fitting |
| Multi-task / EO foundation model | ⬜ | |
| Metric height output | 🟡 | learned pixel-footprint scale (±40 %), resolution-matched tiling |
| Diffusion refinement | ⬜ | |
| Physical cues (shadow / view geometry) | 🟡 | shadow-consistency scale check (experimental, guarded) + sun-matched rendering |
| Ensembles + TTA | ✅ | 1/4/8-pass rotation ensemble, uncertainty map |
| Calibrated uncertainty | 🟡 | coverage measured: σ ranks error well but is 9–16× too small (BENCHMARKS §5); treat as relative |
| Distilled / fast model | 🟡 | ONNX export script; model kept loaded between jobs; batched ensemble passes; fp16 opt-in (`DEPTHWIZARD_FP16=1`) |

## 3. Calibration and geodesy
| Item | Status | Notes |
|---|---|---|
| DEM fusion | ✅ | automatic surface-vs-bare-earth detection |
| Vertical datum handling | 🟡 | datum recorded in GeoTIFF tags/metadata; auto-download sets EGM2008/EGM96; no grid conversion yet |
| 30 m reference consistency | ✅ | only for surface DEMs (fixed a bug that pulled buildings down on bare-earth DEMs) |
| Robust GCP fit | ✅ | Huber, leave-one-out error, spread check, provisional flag |
| Active-learning GCP suggestions | 🟡 | interactive GCP pins with live R²/RMSE/LOO; suggestions not yet |
| Stereo/multi-date photogrammetry | ⬜ | |

## 4. Post-processing
| Item | Status | Notes |
|---|---|---|
| Water flattening | ✅ | conservative large-smooth-water mask |
| Shadow pits | ✅ | above-ground structure is non-negative by construction |
| Land-cover rules | 🟡 | vegetation excluded from buildings (excess-green) |
| LoD1 buildings | ✅ | vector footprints, robust flat roofs, storeys, volume, confidence basis |
| LoD2 roofs / CityGML | 🟡 | CityJSON LoD1 export (convertible to CityGML); no roof shapes |
| DSM / DTM / nDSM separation | ✅ | all three exported as GeoTIFF |
| Off-nadir lean correction | ⬜ | |

## 5. Validation
| Item | Status | Notes |
|---|---|---|
| Full metric set | ✅ | RMSE, MAE, NMAD, bias, r, within 1/2/5 m, 30 m aggregate, per-building |
| Per landscape + DEM baseline | ✅ | always shown in app and report |
| Ablations | ✅ | previous build vs current, TTA passes (`docs/BENCHMARKS.md`) |
| Public benchmark / paper | ⬜ | |

## 6. Visualisation
| Item | Status | Notes |
|---|---|---|
| Textured LoD1 buildings | ✅ | fixed mirrored/downward extrusion; roofs textured, stand on DTM |
| AI facades / Gaussian splatting | 🟡 | procedural floors/windows on walls; no AI facades |
| LOD tiles / globe mode | 🟡 | 512 / 1024 mesh detail toggle; globe mode not started |
| Lighting | ✅ | ACES tone mapping, fitted soft shadows, normal-map hillshade, Cinematic mode (GTAO + SMAA + sky) |
| DEM vs DSM swipe | ✅ | geometric: left half really renders the input DEM |
| Uncertainty overlay | ✅ | confidence layer (exp(−σ/2 m)) |
| Recorded flythrough | ✅ | one-click 20 s WebM recording of the cinematic tour |
| VR / collaboration | ⬜ | |

## 7. Analysis tools
| Item | Status |
|---|---|
| Probe, profile, slope, aspect, curvature, 3D distance | ✅ |
| Cut / fill vs reference | ✅ |
| Connected flood (edge / clicked source / plane), depth, volume, buildings affected | ✅ |
| Landslide susceptibility screening layer | ✅ |
| Viewshed / line of sight | ✅ |
| Building statistics | ✅ |
| Rooftop solar potential | ✅ (indicative) |
| Height-limit / density checks | 🟡 height-limit check in the viewer |

## 8. Disaster management
| Item | Status | Notes |
|---|---|---|
| Change detection pre/post | ✅ | height-loss map, volumes, collapsed-roof list (11/12 on simulated test) |
| Rapid response | 🟡 | image → DSM + hazard layers in one run (≈20 s per 1024² on a laptop GPU) |
| Offline field kit | 🟡 | offline web app; PyInstaller build script; DEM cache not bundled |
| Damage report | ✅ | HTML report (print to PDF) + evidence JSON |
| Bhuvan / NDMA integration | ⬜ | REST API ready (`/docs`) |

## 9. Time series and scale
⬜ multi-date series, national tiling and automatic ingestion are not started.

## 10. Engineering
| Item | Status |
|---|---|
| One-file desktop app | 🟡 `packaging/build_windows.bat` + spec (not yet built on a clean PC) |
| ONNX / TensorRT | 🟡 export script |
| Large scenes | ✅ tiled inference; automatic downsampling above 64 MP (`DEPTHWIZARD_MAX_MP`); viewer downsamples |
| REST API + docs | ✅ FastAPI `/docs`, product, CityJSON, PLY, change endpoints |
| Exports | ✅ GeoTIFF (DSM/DTM/nDSM/σ), GLB, OBJ, PLY, CityJSON, one-click export-all ZIP |
| Tests / CI | ✅ 28 pytest tests, GitHub Actions workflow |
| Model card / reproducibility | ✅ `docs/MODEL_CARD.md`, `docs/BENCHMARKS.md` |
| Security | 🟡 local-only processing; no auth (single-user app) |

## 11. Product
| Item | Status |
|---|---|
| Role-based interfaces | ⬜ |
| Docs and sample data | ✅ README, TRAINING, BENCHMARKS, MODEL_CARD, samples |
| Correction → retraining loop | ⬜ |

## Where this leaves us against the competitors
* **Accuracy:** only team with a train/test-separated fine-tune, blind absolute
  scores against a DEM baseline, per-building LiDAR validation and an
  ablation. The biggest remaining gap is Cartosat/Indian test data.
* **3D and UX:** matches or exceeds every competitor feature seen (LoD1 city,
  flood with buildings affected, swipe, measurement, exports, report) and adds
  connected flood, viewshed, landslide, solar, change detection and video
  recording that none of them show.
* **Deployment:** amogh-hub still leads on a signed, soak-tested installer;
  building and testing our Windows package on a clean machine is the next step.
