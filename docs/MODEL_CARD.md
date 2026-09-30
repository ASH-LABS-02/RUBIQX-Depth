# Model card – DepthWizard GAMUS height model (`da2-gamus-full`)

| | |
|---|---|
| Base model | Depth Anything V2 Small (DINOv2-S encoder, DPT head), Apache-2.0 |
| Fine-tuning data | GAMUS train split (RGB + LiDAR above-ground height, CC BY 4.0), cities DC / NYC / PHL, 0.33 m GSD |
| Target | Above-ground height (AGL / nDSM); `config.json` carries `"depthwizard_target": "agl"` |
| Loss | Scale-and-shift-invariant L1 + multi-scale gradient matching; void value −5 masked |
| Training | 10 epochs, batch 4, 518 px crops, AdamW (encoder LR 5e-6, decoder 10×), rotation/flip/colour augmentation, RTX 4060 Laptop (≈2 GB VRAM, ≈55 min) |
| Selection | Best validation affine RMSE: 2.12 m (epoch 10, 858 validation crops) |
| Held-out test | 30 GAMUS test tiles: RMSE 4.76 → 3.00 m, r 0.41 → 0.79 vs pretrained |

## Intended use

Single-image surface-height estimation for overhead RGB imagery at roughly
0.3–1 m GSD, followed by DepthWizard calibration (GCPs, surface DEM or the
learned pixel-footprint scale) to metres.

## Metric scale

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
