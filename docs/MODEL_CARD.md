# Model card – DepthWizard GAMUS height model (`da2-gamus-full`, v5)

| | |
|---|---|
| Base model | Depth Anything V2 **Base** (DINOv2-B encoder, DPT head, ~97 M parameters), CC BY-NC 4.0 weights* |
| Fine-tuning data | Full GAMUS train split (5,004 tiles; RGB + LiDAR above-ground height, CC BY 4.0), cities DC / NYC / PHL, 0.33 m GSD |
| Target | Above-ground height in metres; `config.json` carries `depthwizard_target`, `depthwizard_pixel_height` (1.0) and `depthwizard_train_net_gsd` (0.65) |
| Loss | Metric L1 on height (pixel_height = 1) + scale-and-shift-invariant shape loss; pixels > 3 m weighted ×1.5 |
| Training | 30 epochs, batch 4, 518 px crops at 0.65 m per network pixel (±15 % scale jitter), satellite-style degradation (blur/downsample, haze, noise), AdamW LR 5e-6, OneCycle; RTX 4060 Laptop, 4.6 GB VRAM, ≈18 min/epoch (≈7.5 h) |
| Selection | Best validation **absolute** RMSE: 2.23 m (epoch 24, 859 validation tiles) |
| Satellite (v5) | v4 + 4 epochs on GAMUS + Urban 3D (WorldView 0.5 m, CC BY-NC). Urban 3D test 3.06 → 1.82 m; GAMUS 0.6 m colour 2.47 m, panchromatic 2.76 m; blind DC colour 3.61 m. See BENCHMARKS §1g |
| Panchromatic (v4) | v4 = v2 + 2 epochs with a third of crops greyscale/panchromatic, selected on colour + greyscale validation (2.25 / 2.61 m). Test, 0.6 m: colour 2.51 m, panchromatic 2.86 m (v2 3.35 m); blind DC colour 3.59 m |
| Held-out test (v2) | 30 GAMUS test tiles, no per-tile fitting: **2.62 m RMSE, 1.48 m MAE, r 0.84, bias −0.07 m**, tall objects 0.97 of true height (v1 Small: 3.46 m, 2.18 m, r 0.79, bias −0.82 m, 0.82) |

\* Check the licence of the Depth Anything V2 Base weights before commercial use; the Small model is Apache-2.0.
The previous v1 model (Small, scale-invariant loss, learned scale C = 0.674) is described below for reference.

## Intended use

Single-image surface-height estimation for overhead RGB imagery at roughly
0.3–1 m GSD, followed by DepthWizard calibration (GCPs, surface DEM or the
learned pixel-footprint scale) to metres.

## Metric scale (v1; v2 is trained directly in metres)

The loss is scale-invariant, but the network's raw output behaves like height
in "network pixels": metres ≈ 0.674 × raw × (ground metres per network input
pixel). The constant was fitted on 23 GAMUS *validation* tiles (IQR 0.52–0.76).
On independent DC aerial orthophotos the matching constant was 0.86–1.13, so
treat this scale as ±40 %. DepthWizard uses it only when no measured scale
(GCPs, surface DEM) is available, and labels such outputs "approximate".

## Known limitations

* Trained on three US cities; Indian urban morphology, dense informal
  settlements, monsoon haze and Cartosat radiometry are out of domain.
* UAV imagery much finer than 0.3 m (e.g. 0.12 m) is out of the trusted
  scale range; the learned scale is then disabled.
* Canopy heights are learned from LiDAR first returns; dense forest is harder
  than buildings.
* Water, glass roofs and long shadows remain error sources.

## Recommended next training steps

1. Add Cartosat-1 stereo-derived DSMs (ISRO sensor domain) as pseudo-labels.
2. Train with random GSD resampling (0.3–1.2 m) and a metric head (L1 in
   metres) so scale is predicted directly.
3. Evaluate Depth Anything V2 Base / Depth Anything 3 backbones.
