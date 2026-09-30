# DepthWizard

Single-view height estimation and 3D flythrough (SIH 2026, PS 26175, ISRO/SAC).

This tool takes one optical RGB satellite image and turns it into two things:

- A **Digital Surface Model (DSM)** saved as a GeoTIFF.
  - For a PNG/JPG, it is *relative* (heights on an arbitrary scale).
  - For a georeferenced GeoTIFF, it is *absolute*, in metres.
- An interactive **3D terrain** you can orbit, fly through and measure in the browser.

```
RGB image ──► Depth Anything V2 ──► relative height ──► scale calibration ──► DSM GeoTIFF
 (PNG/JPG/TIFF)   (pre-trained or      (tiled, globally      (DEM fusion / GCPs /        │
                   GAMUS fine-tuned)     aligned)              scene prior)               ▼
                                                                    Three.js viewer: orbit · fly · tour,
                                                                    probe, profiles, slope, error vs reference
```

## Highlights (v2.2)

| | |
|---|---|
| **Accuracy** | Blind absolute DSM error **5.04 m RMSE / 3.52 m MAE** over six LiDAR/survey scenes, vs 7.94 / 5.17 m for the input DEM alone and 7.06 / 5.05 m for the previous build ([benchmarks](docs/BENCHMARKS.md)). GAMUS fine-tune: correlation 0.41 → 0.79 on 30 held-out tiles. |
| **Calibration** | Detects whether the DEM is a surface model (Copernicus/SRTM) or bare earth; fits building scale from a surface DEM and matches it exactly at 30 m; otherwise uses GCPs, a learned pixel-footprint scale or a scene prior, always labelled. |
| **Products** | DSM, DTM, nDSM and per-pixel uncertainty GeoTIFFs · LoD1 buildings (CityJSON) · GLB/OBJ/PLY · HTML report · evidence JSON |
| **3D** | Textured LoD1 city on bare ground, sun-matched shadows, geometric DEM-vs-DSM swipe, orbit/fly/tour, one-click flythrough video |
| **Analysis** | Connected flood (edge / clicked source) with depth, volume and buildings affected · landslide susceptibility · viewshed · rooftop solar · pre/post change detection · profiles, 3D distance, cut/fill |
| **Engineering** | 12 automated tests + CI, REST API with `/docs`, ONNX export, PyInstaller build script, [model card](docs/MODEL_CARD.md), [roadmap status](docs/ROADMAP_STATUS.md) |

## Quick start

```bash
# Linux / macOS
./start.sh
# Windows
start.bat
```

This creates a virtual environment, installs the requirements and opens <http://127.0.0.1:8000>. Two synthetic demo scenes are pre-loaded.

To run it **offline**, first run `python scripts/download_models.py small base`. That caches the model weights once.

### Command line

The general form is `python -m depthwizard IMAGE [options]`.

Plain PNG, giving a relative DSM (`rdsm.tif`):

```bash
python -m depthwizard scene.png --gsd 0.6 -o out/
```

GeoTIFF with a DEM, giving an absolute DSM in metres (`dsm.tif`):

```bash
python -m depthwizard scene.tif --dem cop30.tif -o out/
```

The same, but downloading the DEM automatically. This needs a free OpenTopography API key:

```bash
export OPENTOPO_API_KEY=...
python -m depthwizard scene.tif --fetch-dem --dem-source COP30 -o out/
```

Adding ground control points and validating against a reference:

```bash
python -m depthwizard scene.tif --dem cop30.tif --gcp gcps.csv --ref lidar.tif --model base -o out/
```

Each run writes these files to the output folder:

| File | Contents |
|---|---|
| `dsm.tif` / `rdsm.tif` | Float32 DSM, same CRS and transform as the input, deflate-compressed |
| `metrics.json` | Validation scores (only when `--ref` is given) |
| `preview.png` | Colour-relief hillshade image |
| `viewer/` | Assets for the 3D viewer |

## How it works

### 1. Elevation extraction (`depthwizard/depth.py`)

The backbone is Depth Anything V2. You can use Small, Base or Large, or your own fine-tuned checkpoint.

Satellite scenes are much larger than the network's 518 px input, so it runs in two passes:

- **Global pass:** the whole scene, downsampled. This gives consistent large-scale structure.
- **Tile pass:** overlapping 1024 px tiles at full resolution. This gives fine detail.

Each tile's own arbitrary scale and shift is re-aligned to the global pass by least squares. The tiles are then blended with feathered edges, so no seams appear.

The network outputs inverse depth, where larger means closer to the camera. In a nadir view, closer means higher, so the output is used directly as relative height.

### 2. Scale calibration (`depthwizard/calibrate.py`)

The method is chosen automatically from what you supply.

| Inputs | Method | Formula |
|---|---|---|
| GeoTIFF + DEM | **DEM fusion** | `DSM = DEM_terrain + k · max(highpass(rel) − ground_anchor, 0)` |
| GeoTIFF + DEM + GCPs | DEM fusion, *k* from GCPs | Terrain from the DEM; structure scale fitted to the surveyed points |
| Any image + GCPs | Robust affine | `DSM = a · rel + b`, fitted with Huber IRLS |
| PNG / JPG | Relative | rDSM in the range [0, 1] |

**Why fusion.** A 30 m DEM (SRTM, Copernicus GLO-30 or CartoDEM) gets the absolute datum and the large-scale relief right. It cannot see buildings or trees. The network sees exactly those, but has no scale.

The DEM is reprojected onto the image grid and supplies the bare-earth terrain and datum. A lower-tail anchor makes the network's high-pass structure nonnegative, so the estimated surface does not systematically fall below the DEM. This is still an estimate of a surface, and its quality depends on the DEM, model transfer, and scale source.

The structure scale *k* comes from the first source that works:

1. The GCPs, if you supplied them.
2. A robust fit of the low-passed network output against the DEM, used only when that fit correlates well.
3. A scene-level prior: `--scene urban|sparse|forest|hilly`.

Calibration metadata records the DEM coverage, scale source, and evidence level. A weak DEM fit triggers a scene prior and labels the output **approximate**. GCP fits record in-sample residuals separately from leave-one-out error and mark sparse or clustered GCP configurations provisional; six distributed points with stable leave-one-out error support a stronger claim. An independent reference DSM or LiDAR set is still needed to measure accuracy.

### 3. Validation (`depthwizard/metrics.py`)

It reports these scores:

- RMSE, MAE, bias, NMAD and Pearson r.
- Edge-gradient RMSE and error by reference height above ground (with DEM baseline rows).
- The share of pixels within 1, 2 and 5 m.
- An affine-aligned score, which is the fair score for a relative DSM.
- An nDSM score: height above ground, with terrain removed, to test structure recovery.
- A **baseline**: the same scores for the input DEM alone. Beating this baseline shows the network adds value.

Results are also broken down per landscape class (urban, sparse, hilly, forest). The class of each tile is decided from the reference surface and a vegetation index.

### 4. Domain adaptation (`scripts/finetune_gamus.py`)

This script fine-tunes Depth Anything V2 on GAMUS, or any other RGB + height dataset. It uses:

- A scale-and-shift-invariant L1 loss.
- Multi-scale gradient matching, for sharp building edges.
- Rotation and flip augmentation.
- A lower learning rate for the encoder than the decoder.

It reads GAMUS's original HDF5 RGB/AGL pairs, supports gradient accumulation for an 8 GB GPU, and saves a resumable checkpoint each epoch. See [TRAINING.md](TRAINING.md) for the verified laptop setup, dataset downloader, short trial, 10-epoch command, and held-out evaluation. Use the trained checkpoint with `--model PATH_TO_CHECKPOINT`.

### 5. Batch evaluation (`scripts/evaluate_batch.py`)

```bash
python scripts/evaluate_batch.py manifest.csv --model base -o eval_out
```

The manifest columns are `image,reference[,dem][,gcp][,scene]`. The script writes:

- `results.csv`, with one row per scene.
- `summary.json`, with mean RMSE, MAE and r overall and per landscape class.

## 3D viewer (`web/`)

It uses Three.js, vendored locally, so no CDN is needed and it works offline.

The app opens into a dark three-part workspace: a thumbnail scene library, a large 3D terrain view, and an analysis inspector. Image import has separate DEM/GCP calibration, reference validation, and model options. The inspector separates scene exploration from validation; on narrow screens the canvas stays above the inspector and the scene library opens as a drawer. A linked comparison shows the source RGB and estimated height at the same pixel while keeping the 3D scene visible.

| Feature | Details |
|---|---|
| Navigation | **Orbit**; **Fly** (first-person, pointer-lock, WASD/QE/Shift, never drops below the terrain); **Tour** (automatic flythrough); **Top down** and **Fullscreen**; a minimap you can click to jump |
| Surfaces | Optical drape, height colour ramp, slope (° for metric DSMs; relative gradient otherwise), curvature, error vs a metric reference; contour lines; wireframe |
| Controls | Vertical exaggeration; display-only mesh smoothing; sun azimuth |
| Comparison | Linked source RGB and height maps with a shared cursor; clicking a pixel sets the 3D height probe |
| Probe | Estimated height, reference height, error, slope, aspect, and map coordinates (E/N) for georeferenced scenes |
| Profile | Two clicks draw an elevation cross-section: estimate vs reference, length, Δh, grade, profile RMSE |
| Analysis | Metric scenes support a rising horizontal flood plane with a terrain-only submerged-area estimate. With a metric reference DSM, cut/fill volumes compare the estimated and reference surfaces. |
| Validate tab | Metric cards, comparison table including the DEM baseline, per-landscape and reference-height-band tables, edge-gradient score, calibration details |
| Export | DSM GeoTIFF, textured GLB, textured OBJ ZIP, and a PNG screenshot stamped with scene/model/layer/display-Z provenance |
| Keyboard | O/F/T/D/V/R/H, 1–4 for surfaces, C for contours |

## Deployment

- **Local app:** `start.sh` / `start.bat`. This is a locally hosted web app, which the FAQ accepts.
- **Docker:** `docker build -t depthwizard . && docker run -p 8000:8000 depthwizard`. The model weights are baked into the image.
- **Desktop executable (optional):** `pip install pyinstaller` then:

  ```
  pyinstaller --onedir --add-data "web:web" --add-data "data:data" --collect-all transformers run.py
  ```

## Test data

`scripts/make_synthetic_scene.py` generates a fully known scene. It contains:

- A georeferenced RGB image in UTM 43N at 0.5 m GSD.
- A ground-truth DSM.
- A coarse "SRTM-like" 30 m DEM with noise.
- A GCP CSV.

This lets every mode and metric be checked without any downloads. It is a plumbing test, not an accuracy claim.

Two real GAMUS RGB/AGL test pairs are ready in [samples/gamus/](samples/gamus/README.md), with provenance, upload instructions, and a measured pretrained baseline. Two georeferenced forest crops with RGB, a 30 m DEM, and a reference DSM are ready in [samples/quesenbank/](samples/quesenbank/README.md). Those crops exercise the absolute DSM route, but broader independent LiDAR evaluation is still needed for an accuracy claim.

On those two Quesenbank crops, the fine-tuned model with the nonnegative DEM-fusion calibration has a mean absolute DSM RMSE of **6.70 m** and MAE of **3.60 m**, versus **7.79 m** and **4.93 m** for the prior fusion method. Both methods use a 30 m DEM downsampled from the same survey's high-resolution DEM, so this is a cross-landscape pipeline check, not independent LiDAR validation. The 30 GAMUS held-out test tiles have mean affine-aligned RMSE **3.00 m** versus **4.76 m** for the pretrained Small backbone; that alignment uses each test tile's AGL reference and measures relative shape only.

## Known limitations

- Monocular height is weakest on:
  - Uniform flat roofs, where there is little texture.
  - Water.
  - Scenes lit by a low sun, where long shadows are confused with height.
- Fine-tuning on overhead data (GAMUS) is the main lever for all of these.
- The scene prior for *k* is a fallback and yields approximate metric heights. Sparse GCPs yield provisional calibration even if their in-sample residual is small.
- Relative viewer geometry uses a display-only vertical scale to make the flythrough legible. The rDSM GeoTIFF and reported probe values remain unitless; no reference DSM is used to shape relative viewer geometry.
- Mesh exports are decimated to at most 512 samples along their longest side. Relative exports are explicitly unitless; neither mesh export replaces the full-resolution DSM GeoTIFF.
- Flood estimates use a static elevation plane over the DSM; they do not simulate drainage or water connectivity. Cut/fill compares DSM grids and is a screening estimate, not a surveyed earthwork quantity.
- If the model weights cannot be loaded, the pipeline falls back to a crude heuristic so the rest of the app still runs. The viewer flags this, and `--no-fallback` disables it.
