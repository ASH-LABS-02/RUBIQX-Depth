# DepthWizard benchmarks

Current model: v2 (Depth Anything V2 Base, metric loss, 30 epochs on the full
GAMUS train split; see `docs/MODEL_CARD.md`). Sections 1b and 2a report v2;
older sections are kept for comparison. The v1 DC result was produced with the
GAMUS fine-tuned checkpoint (`da2-gamus-full` v1, 10 epochs, RTX 4060) using the checked-in
`samples/dc_lidar/manifest.csv` and `samples/dc_lidar/evaluation/` outputs.
"Reference held out" means the 2024 LiDAR DSM was used for scoring only, not
to construct the calibration input or height anchors. Other rows are labelled
by the evidence they use; they must not be pooled into a blind accuracy claim.

## 1. Relative height shape – 30 held-out GAMUS test tiles

Scale and offset fitted per tile to the AGL reference (shape diagnostic, not
absolute accuracy). Test tiles were never used for training or validation.

| Backbone | Mean RMSE | Mean MAE | Mean r |
|---|---:|---:|---:|
| Depth Anything V2 Small (pretrained) | 4.76 m | 3.71 m | 0.41 |
| DepthWizard v1 (Small, scale-invariant loss) | 3.00 m | 1.90 m | 0.79 |
| **DepthWizard v2 (Base, metric loss) – current** | **2.56 m** | **1.51 m** | **0.84** |

Results: `docs/eval/gamus30/` (v1) and `docs/eval/gamus30-v2/` (v2), rows marked `[aligned]`.

### 1b. Absolute height – same 30 tiles, **no fitting to the reference**

**Current model (v2).** Depth Anything V2 Base fine-tuned for 30 epochs on the
full GAMUS train split with a metric height loss (plus the shape loss),
extra weight on pixels taller than 3 m, training at the inference resolution
(0.65 m per network pixel, ±15 % scale jitter) with satellite-style
degradation (blur, haze, noise), and checkpoint selection on validation
*absolute* RMSE (best 2.23 m, epoch 24). Nothing is fitted per tile.
0.33 m/px, TTA 1.

| Tiles | RMSE | MAE | r | Mean bias | Median est/ref height, objects > 3 m |
|---|---:|---:|---:|---:|---:|
| DC (10) | 3.45 m | 2.06 m | 0.90 | −0.02 m | 0.96 |
| NYC (10) | 2.76 m | 1.64 m | 0.71 | −0.21 m | 0.84 |
| PHL (10) | 1.65 m | 0.74 m | 0.90 | +0.01 m | 1.00 |
| **All 30** | **2.62 m** | **1.48 m** | **0.84** | **−0.07 m** | **0.97** |

Against v1 on the same tiles: RMSE −24 %, MAE −32 %, bias −0.82 → −0.07 m,
tall objects 0.82 → 0.97 of true height; every city improved. Absolute RMSE
is now only 0.06 m above the shape-aligned score, so scale is essentially
solved on this data and the remaining error is shape (smooth crowns, leaf-off
trees, flat roofs). NYC tall objects (0.84) are the weakest group. All test
tiles come from the same three US cities as training; this is not evidence
for Cartosat or Indian scenes. Raw results: `docs/eval/gamus30-v2/`.

**Previous model (v1, kept for reference).**

The v1 checkpoint's own metric output (learned pixel-footprint scale
C = 0.674, fitted on GAMUS *validation* tiles, never on these test tiles)
scored directly against the LiDAR AGL. Nothing is fitted per tile, so this is
the honest single-image number. 0.33 m/px, TTA 1.

| Tiles | RMSE | MAE | r | Mean bias | Median est/ref height, objects > 3 m |
|---|---:|---:|---:|---:|---:|
| DC (10) | 4.88 m | 3.20 m | 0.84 | −1.52 m | 0.71 |
| NYC (10) | 3.52 m | 2.31 m | 0.66 | −0.92 m | 0.70 |
| PHL (10) | 1.99 m | 1.01 m | 0.88 | −0.03 m | 0.98 |
| **All 30** | **3.46 m** | **2.18 m** | **0.79** | **−0.82 m** | **0.82** |

Absolute RMSE is only 0.46 m worse than the shape-aligned score, so the learned
scale transfers to unseen tiles. The remaining error is mostly a height
*under*-estimate of tall objects (about 30 % low in DC and NYC), the same
building bias seen in §3. The pretrained backbone cannot be scored this way
because it has no metric output. All tiles are US cities (DC, New York,
Philadelphia); this is not evidence for Indian scenes.

### 1c. Failure cases

![Failure cases on held-out GAMUS tiles](images/failure_cases.jpg)

The four rows are the three worst test tiles and one typical good tile. Errors
concentrate on **tall vegetation and large flat roofs**, which come out too low:

* **NYC_20732 – leaf-off forest (9.2 m RMSE).** Bare winter trees 25–30 m tall
  read as a low, smooth canopy; there is little texture to anchor height.
* **DC_39_21 – downtown mid-rise (7.2 m).** Footprints are right but large flat
  roofs are about 35 % low.
* **NYC_16766 – isolated cemetery trees (6.0 m).** Crowns are found but about
  55 % too low.
* **PHL_4266 – roads, low trees, row houses (1.3 m).** Typical low-rise scenes
  are accurate, with a small positive bias on tree crowns.

The prediction is also smoother than LiDAR (fine crown texture is lost), which
adds error at object edges.

### 1d. Negative results (what we tried that did not help)

* **Post-hoc height correction.** A single gain, a gain above a height
  threshold, and a power curve were fitted on 40 GAMUS *validation* tiles and
  scored on the 30 test tiles. The best improved test RMSE from 3.46 m to
  3.44 m – within noise – so no correction is applied. On validation tiles the
  learned scale is already close to optimal (tall objects at 0.90 of true
  height); the larger shortfall on test tiles comes mainly from NYC, a city not
  in the validation set. Reproduce with `scripts/check_height_correction.py`.
* **Resolution on the DC scene.** On Glover Park the raw model already predicts
  buildings at 0.45 of their LiDAR height (the pipeline adds no loss: 0.44
  after calibration). Resampling the image to 0.25–0.5 m/px gives the same
  0.45, because tiling normalises the network's view. A likely cause is the
  imagery type: the DC 2023 mosaic appears close to a true orthophoto with
  little visible building lean, while GAMUS tiles show facades that the model
  uses as a height cue. This is a hypothesis, not yet tested; a height anchor or
  GCPs correct it in practice (§3).

### 1g. Satellite training data – current model (v5)

v5 = v4 fine-tuned 4 epochs on GAMUS plus the **Urban 3D Challenge** dataset
(WorldView satellite RGB, 0.5 m, Jacksonville/Tampa/Richmond; heights = Vricon
satellite-stereo DSM − DTM, CC BY-NC). Train = Provisional_Train + Unused_Data,
validation = Provisional_Test, test = Sequestered_Test (never used for training
or selection). Mixed 70 % GAMUS / 30 % Urban 3D, a third of crops panchromatic,
selection on the mean of GAMUS and Urban 3D validation. Scripts:
`scripts/prepare_urban3d.py`, `scripts/run_gamus_urban3d.ps1`; details in
`CHANGES_URBAN3D.md`; raw results `docs/eval/v5-urban3d/`.

| Test (no fitting) | v4 | **v5 (shipped)** |
|---|---:|---:|
| Urban 3D test, 159 satellite tiles, 0.5 m | 3.06 m, r 0.83, bias −0.99, tall 0.81 | **1.82 m, r 0.93, bias −0.12, tall 0.97** |
| GAMUS 30 tiles, colour, 0.33 m / 0.6 m | 2.65 / 2.51 m | **2.61 / 2.47 m** (bias ≈ 0) |
| GAMUS 30 tiles, panchromatic, 0.6 m | 2.86 m | **2.76 m** |
| Blind DC Glover Park, colour / panchromatic | 4.30 / 4.70 m | 4.37 / **4.51 m** |
| Blind DC Capitol Hill East, colour / panchromatic | 2.88 / 3.22 m | **2.84 / 3.13 m** |
| Blind DC mean, colour / panchromatic | 3.59 / 3.96 m | 3.61 / **3.82 m** |

Satellite error falls 41 % and the tall-object under-estimate on satellite
imagery disappears; aerial and DC results are unchanged within noise. Caveat:
Urban 3D test tiles come from the same cities as its training tiles, and its
heights are satellite-stereo (smoother than LiDAR) – this shows adaptation to
satellite imagery, not performance on Indian scenes.

### 1f. Panchromatic (single-band) input – v4

Cartosat-2S acquires 0.6 m imagery in one panchromatic band (colour is 1.6 m),
so SAC's test images may be greyscale. v2 was trained on colour only. Inputs
were converted to greyscale, or to a simulated 11-bit panchromatic sensor
(luminance as DN with noise, then the app loader's stretch); DC scenes were
written as 1-band 16-bit GeoTIFFs and run through the full pipeline.
Script: `scripts/pan_check.py`; raw results: `docs/eval/pan/` (v2) and
`docs/eval/v4-pan/` (v4).

**v4** = v2 fine-tuned 2 more epochs with a third of training crops
panchromatic, validated and selected on colour *and* greyscale
(`scripts/run_gamus_pan.ps1`; the 6-epoch run was stopped after epoch 3 got
worse, epoch 2 kept).

| Test (no fitting) | v2 | **v4 (shipped)** |
|---|---:|---:|
| GAMUS 30 tiles, colour, 0.33 m | 2.62 m | 2.65 m |
| GAMUS 30 tiles, colour, 0.6 m | 2.48 m | 2.51 m |
| GAMUS 30 tiles, greyscale, 0.6 m | 3.25 m | **2.80 m** |
| GAMUS 30 tiles, simulated panchromatic, 0.6 m | 3.35 m (bias +0.65) | **2.86 m** (bias −0.01) |
| Blind DC Glover Park, colour / panchromatic | 4.35 / 4.92 m | **4.30 / 4.70 m** |
| Blind DC Capitol Hill East, colour / panchromatic | 2.86 / 3.17 m | 2.88 / 3.22 m |
| Blind DC mean, colour / panchromatic | 3.61 / 4.05 m | **3.59 / 3.96 m** |

Colour accuracy is unchanged within noise; panchromatic error falls 15 % on
GAMUS and 2 % on DC, and the panchromatic over-estimate disappears. Colour
remains better than panchromatic by about 0.35 m, so a colour or
pan-sharpened product should be preferred when available.

### 1e. Resolution stress test, 0.33 m to 10 m

SAC requires the method to work for 0.35-10 m imagery without overfitting to
0.6 m. Each of the 30 held-out GAMUS test tiles was area-averaged to a coarser
resolution (a simulated coarser sensor), the LiDAR height averaged to the same
grid, and the model's metric output from the coarse image alone scored on that
grid (no fitting). Script: `scripts/resolution_sweep.py`; raw results:
`docs/eval/resolution-sweep/`.

| Image GSD | v2 RMSE / MAE / r | v3 RMSE / MAE / r |
|---|---|---|
| 0.33 m | **2.62** / 1.48 / 0.84 | 2.62 / 1.47 / 0.84 |
| 0.6 m (Cartosat-2S) | **2.48** / 1.40 / 0.85 | 2.48 / 1.39 / 0.85 |
| 1.0 m | **2.38** / 1.36 / 0.86 | 2.38 / 1.34 / 0.86 |
| 1.5 m | 2.43 / 1.39 / 0.85 | **2.41** / 1.36 / 0.86 |
| 2.5 m | 2.65 / 1.53 / 0.84 | **2.50** / 1.43 / 0.86 |
| 5 m | 3.37 / 2.04 / 0.76 | **3.02** / 1.81 / 0.78 |
| 10 m | 6.47 / 4.46 / 0.29 | **5.84** / 3.92 / 0.43 |

The shipped model (v2) is stable from 0.33 to 2.5 m (2.38-2.65 m RMSE, r
0.84-0.86), so it is not tuned to one resolution. From 5 m it degrades, and at
10 m single-image detail is gone for any model (buildings are smaller than a
pixel); there DepthWizard relies on the DEM for heights and uses the image for
texture and land-cover context. All tiles are US aerial imagery degraded in
software, not real Cartosat or Sentinel data.

**v3 (not shipped).** v2 fine-tuned 24 more epochs with simulated 0.35-2.5 m
sensors (`--res-range`) and double weight on tall pixels. It matches v2 up to
1 m and is 6-10 % better at 2.5-10 m, but on the blind DC scenes (0.5 m) it is
slightly worse (Glover Park 4.44 vs 4.35 m, Capitol Hill 2.92 vs 2.86 m) and
biased lower (-0.22 vs -0.07 m on GAMUS; tall objects 0.94 vs 0.97 of true
height), so the stronger tall weighting did not help. v2 stays the default;
v3 is kept as a candidate for coarse (>2 m) imagery. Raw results:
`docs/eval/gamus30-v3/`, `docs/eval/dc-v3/`.

## 2. Absolute DSM – reference-held-out DC LiDAR evaluation

### 2a. v2 (Base, metric loss); current v4 in §1f

Same inputs and scoring as below (2023 image, 2018 DTM at 32 m, 2024 LiDAR DSM
for scoring only), rerun with the v2 checkpoint, TTA 4, scene prior *urban*.
Raw metrics: `docs/eval/dc-v2/`.

| Scene | DEM only RMSE / MAE | v1 RMSE / MAE | **v2 RMSE / MAE** | v2 r | v2 bias | per-building RMSE / r (v1 → v2) |
|---|---:|---:|---:|---:|---:|---|
| DC Glover Park | 10.09 / 7.42 | 6.21 / 4.51 | **4.35 / 3.12** | 0.91 | −2.20 m | 5.72 / 0.40 → **3.83 / 0.56** |
| DC Capitol Hill East | 8.00 / 6.41 | 3.93 / 2.97 | **2.86 / 2.07** | 0.82 | −0.52 m | 5.70 / 0.39 → **2.09 / 0.66** |
| **Mean** | **9.04 / 6.92** | **5.07 / 3.74** | **3.61 / 2.59** | 0.87 | | |

v2 lowers RMSE by 30 % (Glover Park) and 27 % (Capitol Hill) against v1, and by
60 % against the input DTM. At 30 m aggregation the error is 2.70 m and 1.16 m.
The remaining error is concentrated in tall objects: on Glover Park pixels
above 15 m are 7.6 m too low and the median building is 6.4 m against 9.1 m.
Both sites are near GAMUS DC training tiles, so this remains a related-domain
check.

### 2b. Previous model (v1)

Two urban scenes use 2023 optical imagery and a **2018 bare-earth DTM averaged
to 32 m** for calibration; the **2024 LiDAR DSM** is used only as the reference.
The mean absolute DSM error is **5.07 m RMSE, 3.74 m MAE, Pearson r = 0.819**
(versus **9.04 m RMSE, 6.92 m MAE** for the input DTM alone). These are scene
means, not a pixel-pooled score. See `samples/dc_lidar/evaluation/summary.json`
and the per-scene `metrics.json` files; source URLs and file hashes are in
`samples/dc_lidar/provenance.json`. The sites are close to GAMUS DC training
tiles, so this is a small related-domain check, not a held-out geographic
generalization study.

### Additional pipeline checks (mixed calibration evidence)

The following six-scene aggregate includes two simulated surface DEMs created
by downsampling the **same 2024 LiDAR DSM used for scoring**, plus two forest
DEMs from the same survey as their DSM reference. It measures pipeline behavior
under those inputs; **5.04 m RMSE / 3.52 m MAE is not an independent accuracy
result**. The separately listed LiDAR-anchored Glover Park run is excluded from
that six-scene mean.

| Scene | Calibration evidence | Reference | DEM only RMSE / MAE | Previous build RMSE / MAE | **Current** RMSE / MAE | r |
|---|---|---|---:|---:|---:|---:|
| DC Glover Park (urban) | 2018 DTM, 32 m; reference held out | DC 2024 LiDAR DSM | 10.09 / 7.42 | 9.17 / 7.43 | **6.21 / 4.51** | 0.85 |
| DC Capitol Hill East (urban) | 2018 DTM, 32 m; reference held out | DC 2024 LiDAR DSM | 8.00 / 6.41 | 7.89 / 6.64 | **3.93 / 2.97** | 0.79 |
| DC Glover Park (3 LiDAR building anchors; excluded from mean) | Heights from the scoring LiDAR | DC 2024 LiDAR DSM | 10.09 / 7.42 | - | **4.23 / 2.99** | 0.89 |
| DC Glover Park | Simulated surface DEM from scoring LiDAR* | DC 2024 LiDAR DSM | 5.74 / 4.62 | 4.44 / 3.40 | **4.60 / 3.64** | 0.86 |
| DC Capitol Hill East | Simulated surface DEM from scoring LiDAR* | DC 2024 LiDAR DSM | 4.57 / 3.79 | 3.95 / 3.19 | **3.91 / 3.18** | 0.61 |
| Quesenbank forest north | 30 m DEM from same survey | UAV survey DSM | 9.06 / 3.92 | 7.99 / 4.50 | **6.25 / 3.50** | 0.76 |
| Quesenbank forest south | 30 m DEM from same survey | UAV survey DSM | 10.15 / 4.88 | 8.90 / 5.12 | **5.36 / 3.34** | 0.89 |
| **Mixed-evidence mean (six scenes)** | | | **7.94 / 5.17** | **7.06 / 5.05** | **5.04 / 3.52** | |

\* LiDAR DSM averaged to 30 m plus N(0, 2 m) noise – a stand-in for
Copernicus GLO-30 over this site, used only to exercise the surface-DEM path.
Because the scoring LiDAR created this input, those two rows are not
reference-independent, even though noise was added.
"Previous build" = the 29 Sep 23:45 laptop build (high-pass structure, fixed
canopy factor, 30 m matching applied to every DEM type).

What changed between the previous and current build:

1. **DEM type detection.** The previous build forced the output to average to
   the DEM even when the DEM was bare earth, pulling buildings down. The
   current build tests whether the DEM "sees" buildings (correlation of its
   high-pass with the model's structure map) and only enforces 30 m
   consistency for surface DEMs (Copernicus, SRTM).
2. **Above-ground structure.** The GAMUS model predicts height above ground,
   so structure is taken from its lower tail instead of a high-pass (nDSM
   correlation with LiDAR 0.65 → 0.77 in Glover Park).
3. **Learned metric scale.** Raw model output × pixel footprint × 0.674
   (fitted on 23 GAMUS *validation* tiles) gives approximate metres, used when
   no GCP or surface DEM provides a measured scale. Scenes are tiled so the
   network sees ~0.65 m per input pixel, as in training.
4. **Rotation ensemble** (4 passes by default).

### Rotation ensemble ablation (mean of the six mixed-evidence checks)

| Passes | Mean RMSE | Mean MAE |
|---:|---:|---:|
| 1 | 5.10 m | 3.48 m |
| 4 (default) | 5.04 m | 3.52 m |

The accuracy gain is small; the main value is the per-pixel uncertainty map
and building confidence.

## 3. Per-building roof height (LoD1) vs LiDAR

| Scene | Buildings | RMSE | r | median est. / LiDAR |
|---|---:|---:|---:|---:|
| DC Glover Park, v2 model (bare-earth DEM, learned scale) | 145 | **3.83 m** | **0.56** | 6.4 / 9.1 m |
| DC Capitol Hill East, v2 model (bare-earth DEM, learned scale) | 102 | **2.09 m** | **0.66** | 7.9 / 8.6 m |
| DC Glover Park, v1 (bare-earth DEM, learned scale) | 131 | 5.72 m | 0.40 | 4.3 / 9.4 m |
| DC Capitol Hill, v1 (surface DEM fit) | 97 | 5.70 m | 0.39 | – |

Buildings are ranked better than chance but heights are biased low when only
the learned scale is available (aerial 2023 DC orthophoto differs from the
GAMUS satellite imagery). GCPs or a surface DEM remove most of this bias.

## 4. Change detection (simulated)

Twelve largest roofs in Glover Park were replaced by ground-coloured rubble in
the image (`rgb_post_simulated.tif`, clearly labelled test fixture). Pre/post
comparison flagged 12 buildings: 11 correct, 1 false positive, 1 missed
(precision 0.92, recall 0.92); 67,600 m³ of height loss.

## 5. Uncertainty calibration (coverage check)

`scripts/uncertainty_coverage.py` compares the rotation-ensemble spread (σ,
`uncertainty.tif`) with the true error against LiDAR, after removing the
scene-wide bias.

| Scene | Median σ | Errors within 1σ (ideal 68 %) | within 2σ (ideal 95 %) | RMSE by σ quartile (low → high) |
|---|---:|---:|---:|---|
| DC Glover Park | 0.34 m | 6 % | 12 % | 3.3 → 3.7 → 4.7 → 6.9 m |
| DC Capitol Hill East | 0.45 m | 11 % | 21 % | 2.5 → 3.2 → 3.4 → 3.8 m |

Finding: σ **ranks** reliability well (error grows steadily with σ), but its
absolute size is 9–16× too small, because the ensemble only captures
orientation disagreement, not calibration or domain error.

### 5b. Calibrated error bar (now used for `uncertainty.tif`)

Metric scenes now export a calibrated 1-sigma error,
`sigma = sqrt(4.0² + (5.5 × spread)²)` metres (`depthwizard/uncertainty.py`);
the raw spread is kept as `ensemble_spread.tif` and still drives the viewer's
relative reliability layer. Leave-one-scene-out check on raw (not
bias-removed) errors:

| Fitted on | Tested on | Within 1σ, raw spread | Within 1σ, calibrated (ideal 68 %) | Within 2σ, calibrated (ideal 95 %) |
|---|---|---:|---:|---:|
| Glover Park | Capitol Hill East | 9 % | 88 % | 99 % |
| Capitol Hill East | Glover Park | 5 % | 52 % | 79 % |

The shipped constants are fitted on both scenes. Two scenes is a small
calibration set, so the error bar is labelled provisional; it is fitted to the
DEM + learned-scale route and is conservative for GCP- or anchor-calibrated
scenes. Refit with `scripts/uncertainty_coverage.py` as reference scenes are added.

## Reproduce

```bash
python -m depthwizard samples/dc_lidar/glover_park/rgb.tif \
  --dem samples/dc_lidar/glover_park/dtm_2018_32m.tif \
  --ref samples/dc_lidar/glover_park/lidar_dsm_2024.tif \
  --model models/da2-gamus-full --scene urban -o out/glover
# GAMUS test split: <root>/images/test/*.h5 and <root>/heights/test/*.h5
export DEPTHWIZARD_GAMUS_ROOT=/path/to/GAMUS
python scripts/evaluate_gamus_h5.py --models small models/da2-gamus-full \
  --mode both --out out/gamus30          # aligned (shape) and absolute (no fitting)
python scripts/uncertainty_coverage.py samples/dc_lidar/evaluation/001_rgb \
  samples/dc_lidar/glover_park/rgb.tif samples/dc_lidar/glover_park/lidar_dsm_2024.tif
pytest -q tests
```

## Honest limits

* No Cartosat-2S scene with an independent reference has been evaluated yet –
  the organisers' test data is the next priority.
* The DC LiDAR tiles are near GAMUS DC training tiles (different imagery and
  years); treat them as related-domain, not fully independent.
* Forest crops share a survey with their reference DSM.
* The simulated COP30 rows use the scoring LiDAR to form their calibration
  inputs, and the three-building-anchor row also uses that LiDAR. Neither is a
  reference-held-out accuracy measurement.
