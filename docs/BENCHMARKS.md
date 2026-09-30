# DepthWizard benchmarks

All numbers below were produced by the code in this repository on 30 Sep 2026
with the GAMUS fine-tuned checkpoint (`da2-gamus-full`, 10 epochs, RTX 4060).
"Blind" means the estimate used **no information from the reference**; rows
that fit to the reference are labelled as such.

## 1. Relative height shape – 30 held-out GAMUS test tiles

Scale and offset fitted per tile to the AGL reference (shape diagnostic, not
absolute accuracy). Test tiles were never used for training or validation.

| Backbone | Mean RMSE | Mean MAE | Mean r |
|---|---:|---:|---:|
| Depth Anything V2 Small (pretrained) | 4.76 m | 3.71 m | 0.41 |
| **DepthWizard GAMUS fine-tune** | **3.00 m** | **1.90 m** | **0.79** |

All 30 tiles improved. Source: `D:\DepthWizard\evaluation\gamus-30-test`.

## 2. Absolute DSM – six scenes, blind

| Scene | Reference | Input DEM | DEM only RMSE / MAE | Previous build RMSE / MAE | **Current** RMSE / MAE | r |
|---|---|---|---:|---:|---:|---:|
| DC Glover Park (urban) | DC 2024 LiDAR DSM | 2018 DTM, 32 m (bare earth) | 10.09 / 7.42 | 9.17 / 7.43 | **6.21 / 4.51** | 0.85 |
| DC Capitol Hill East (urban) | DC 2024 LiDAR DSM | 2018 DTM, 32 m (bare earth) | 8.00 / 6.41 | 7.89 / 6.64 | **3.93 / 2.97** | 0.79 |
| DC Glover Park (anchored – 3 LiDAR buildings, not blind) | DC 2024 LiDAR DSM | 2018 DTM, 32 m (bare earth) | 10.09 / 7.42 | - | **4.23 / 2.99** | 0.89 |
| DC Glover Park | DC 2024 LiDAR DSM | simulated COP30 surface DEM* | 5.74 / 4.62 | 4.44 / 3.40 | **4.60 / 3.64** | 0.86 |
| DC Capitol Hill East | DC 2024 LiDAR DSM | simulated COP30 surface DEM* | 4.57 / 3.79 | 3.95 / 3.19 | **3.91 / 3.18** | 0.61 |
| Quesenbank forest north | UAV survey DSM | survey DEM, 30 m | 9.06 / 3.92 | 7.99 / 4.50 | **6.25 / 3.50** | 0.76 |
| Quesenbank forest south | UAV survey DSM | survey DEM, 30 m | 10.15 / 4.88 | 8.90 / 5.12 | **5.36 / 3.34** | 0.89 |
| **Mean** | | | **7.94 / 5.17** | **7.06 / 5.05** | **5.04 / 3.52** | |

\* LiDAR DSM averaged to 30 m plus N(0, 2 m) noise – a stand-in for
Copernicus GLO-30 over this site, used only to exercise the surface-DEM path.
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

### Rotation ensemble ablation (mean of the six scenes)

| Passes | Mean RMSE | Mean MAE |
|---:|---:|---:|
| 1 | 5.10 m | 3.48 m |
| 4 (default) | 5.04 m | 3.52 m |

The accuracy gain is small; the main value is the per-pixel uncertainty map
and building confidence.

## 3. Per-building roof height (LoD1) vs LiDAR

| Scene | Buildings | RMSE | r | median est. / LiDAR |
|---|---:|---:|---:|---:|
| DC Glover Park (bare-earth DEM, learned scale) | 131 | 5.72 m | 0.40 | 4.3 / 9.4 m |
| DC Capitol Hill (surface DEM fit) | 97 | 5.70 m | 0.39 | – |

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
orientation disagreement, not calibration or domain error. Treat the
uncertainty and confidence layers as a *relative* map of where to check, not
as calibrated metres. Next step: fit a σ scale on GAMUS validation tiles (not
on these test scenes) and add a calibration-scale term.

## Reproduce

```bash
python -m depthwizard samples/dc_lidar/glover_park/rgb.tif \
  --dem samples/dc_lidar/glover_park/dtm_2018_32m.tif \
  --ref samples/dc_lidar/glover_park/lidar_dsm_2024.tif \
  --model D:/DepthWizard/checkpoints/da2-gamus-full --scene urban -o out/glover
python scripts/evaluate_gamus_h5.py --root D:/DepthWizard/GAMUS \
  --models small D:/DepthWizard/checkpoints/da2-gamus-full --out out/gamus30
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
