# Model card – DepthWizard GAMUS height model (`da2-gamus-full`, v6a)

Current documented model lineage: **v6a**, as recorded in
[BENCHMARKS §1h](BENCHMARKS.md).
The model alias has represented several versions; reproducible runs need the
actual checkpoint hash and configuration. Older training recipes and scores
below retain their version labels.

| | |
|---|---|
| Base model | Depth Anything V2 **Base** (DINOv2-B encoder, DPT head, ~97 M parameters), CC BY-NC 4.0 weights* |
| Fine-tuning data | GAMUS train split (5,004 tiles; RGB + LiDAR above-ground height, CC BY 4.0), DC / NYC / PHL at 0.33 m GSD; v5/v6a also use Urban 3D satellite RGB at 0.5 m, with satellite-stereo DSM − DTM targets (CC BY-NC) |
| Target | Above-ground height in metres; `config.json` carries `depthwizard_target`, `depthwizard_pixel_height` (1.0) and `depthwizard_train_net_gsd` (0.65) |
| Loss | Metric L1 on height (pixel_height = 1) + scale-and-shift-invariant shape loss; the current training code weights metric pixels by `1 + tall_weight × clamp(height / 10 m, 0, 3)`, with tall_weight 0.75 for v6a |
| Base training (historical v2) | 30 epochs, batch 4, 518 px crops at 0.65 m per network pixel (±15 % scale jitter), satellite-style degradation (blur/downsample, haze, noise), AdamW LR 5e-6, OneCycle; RTX 4060 Laptop, 4.6 GB VRAM, ≈18 min/epoch (≈7.5 h) |
| Base selection (historical v2) | Best validation **absolute** RMSE: 2.23 m (epoch 24, 859 validation tiles); this is not the final v6a selection score |
| Satellite-weighted (current v6a) | v5 + 6 epochs, 50 % Urban 3D, tall weight 0.75, LR 2e-6. Urban 3D test 1.66 m (r 0.94); GAMUS colour 0.33 / 0.6 m: 2.61 / 2.47 m, panchromatic 0.6 m: 2.76 m; historical DC diagnostic mean colour 3.64 m, panchromatic 3.79 m. BENCHMARKS §1h |
| Satellite (historical v5) | v4 + 4 epochs on GAMUS + Urban 3D (WorldView 0.5 m, CC BY-NC). Urban 3D test 3.06 → 1.82 m; GAMUS 0.6 m colour 2.47 m, panchromatic 2.76 m; historical DC diagnostic mean colour 3.61 m. See BENCHMARKS §1g |
| Panchromatic (historical v4) | v4 = v2 + 2 epochs with a third of crops greyscale/panchromatic, selected on colour + greyscale validation (2.25 / 2.61 m). Test, 0.6 m: colour 2.51 m, panchromatic 2.86 m (v2 3.35 m); historical DC diagnostic mean colour 3.59 m |
| Held-out test (v2) | 30 GAMUS test tiles, no per-tile fitting: **2.62 m RMSE, 1.48 m MAE, r 0.84, bias −0.07 m**, tall objects 0.97 of true height (v1 Small: 3.46 m, 2.18 m, r 0.79, bias −0.82 m, 0.82) |

\* Check the licence of the Depth Anything V2 Base weights before commercial use; the Small model is Apache-2.0.
The previous v1 model (Small, scale-invariant loss, learned scale C = 0.674) is described below for reference.

## Intended use

Single-image surface-height estimation for overhead RGB imagery at roughly
0.3–1 m GSD, followed by DepthWizard calibration (GCPs, surface DEM or the
learned pixel-footprint scale) to metres.

## Metric scale (historical v1; v2 and later use metric training)

The loss is scale-invariant, but the network's raw output behaves like height
in "network pixels": metres ≈ 0.674 × raw × (ground metres per network input
pixel). The constant was fitted on 23 GAMUS *validation* tiles (IQR 0.52–0.76).
On the historical DC aerial-orthophoto diagnostics the matching constant was 0.86–1.13, so
treat this scale as ±40 %. DepthWizard uses it only when no measured scale
(GCPs, surface DEM) is available, and labels such outputs "approximate".

## Known limitations

* Training/evaluation cover known US urban domains: GAMUS DC / NYC / PHL and
  Urban 3D satellite scenes. Same-domain test scores do not establish geographic
  generalization; source-AOI overlap and scene disjointness need an audit.
  Indian urban morphology, dense informal settlements, monsoon haze and
  Cartosat radiometry lack independent validation.
* UAV imagery much finer than 0.3 m (e.g. 0.12 m) is out of the trusted
  scale range; the learned scale is then disabled.
* Canopy heights are learned from LiDAR first returns; dense forest is harder
  than buildings.
* Water, glass roofs and long shadows remain error sources.
* DC height references were withheld from height construction/calibration in
  the labelled runs, but the sites are near training areas and have been
  historically inspected across experiments. Their scores are related-domain
  diagnostics, not fresh geographically blind validation.
* Metric ensemble uncertainty uses `sqrt(4.0² + (5.5 × spread)²)` metres,
  fitted on these same two DC references. The historical leave-one-scene-out
  1σ coverage of 52–88 % does not independently validate the final two-scene
  fit. Other landscapes and calibration routes are unvalidated. Relative runs
  retain unitless spread; single-pass/heuristic runs have no pixel uncertainty
  or reliability map. See [BENCHMARKS §5](BENCHMARKS.md).
* Flat/plane/gable roofs, canopy tree instances and distance-dependent tree
  LOD are inferred viewer geometry. They do not improve the height grid or
  establish building/canopy accuracy. Automatic shadow/OSM anchors are
  provisional calibration cues; they cannot also serve as independent truth.
* Walk uses estimated footprints and displayed terrain with movement limits;
  it does not establish real pedestrian access. Modal keyboard/focus handling
  and the latest LOD/navigation changes still need the browser/device checks
  listed in [release readiness](release-readiness.md). No FPS or accuracy gain
  is claimed from these viewer changes.

## Next evidence and training steps

1. Freeze the checkpoint/settings and evaluate new scene-disjoint sites using
   the [independent benchmark workflow](independent-benchmark.md); include
   Indian/Cartosat scenes, hilly terrain, forests and difficult tall objects.
2. Fit uncertainty on a development set, then score frozen constants on
   different, untouched reference scenes and calibration routes.
3. Add appropriately licensed Cartosat-1 stereo-derived DSMs as pseudo-labels
   when available, with explicit source-AOI/split provenance. Metric training,
   satellite degradation and panchromatic augmentation already exist; future
   changes need comparison against the frozen v6a baseline.
