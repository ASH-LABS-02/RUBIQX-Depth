# Add Urban 3D (Satellite) Data to Training

## 1. Converter Stats
We added the USSOCOM Urban 3D Challenge dataset (Vricon satellite-stereo) to train the Depth Anything V2 Base model for better satellite generalization. The images are 0.5m WorldView satellite imagery. We cropped the 2048x2048 tiles into 1024x1024 crops, skipping any with < 50% valid AGL.
* Note: Urban 3D heights are satellite-stereo (Vricon), which are smoother than LiDAR. Licence is CC BY-NC (non-commercial).

**Stats per split:**
* **Train** (01-Provisional_Train + 04-Unused_Data): AGL p50=0.81m, p95=16.61m, p99=23.09m | Valid: 97.99% | >3m: 35.81%
* **Val** (02-Provisional_Test): AGL p50=2.00m, p95=15.90m, p99=22.62m | Valid: 99.01% | >3m: 43.66%
* **Test** (03-Sequestered_Test): AGL p50=1.12m, p95=15.82m, p99=23.35m | Valid: 99.70% | >3m: 33.92%

## 2. Baseline Evaluation
Prior to training, the existing v4 model (`da2-gamus-full`) was evaluated on the Urban 3D Test split to establish a baseline:
* **RMSE**: 3.06 m
* **MAE**: 1.89 m
* **r**: 0.825
* **Bias**: -0.99 m
* **Tall Ratio**: 0.814

## 3. Training Log Summary
We added multi-dataset training support via the `--extra` argument and WeightedRandomSampler to `finetune_gamus.py`.
The model was fine-tuned for 4 epochs starting from `da2-base-gamus-v2-pan`.
We set an early stopping condition to abort if GAMUS-val colour absolute RMSE degraded by > 0.10 m after epoch 1.
* **Epoch 1**: GAMUS val absolute RMSE slightly increased from 2.245 m to 2.254 m (+0.009 m, passing the condition). Urban3D val absolute RMSE massively improved from 2.556 m to 1.752 m.
* **Epoch 4 (Final)**: Validation scores continued to improve steadily across the remaining epochs, resulting in `da2-base-v5-urban3d`.

## 4. Acceptance Table (v4 vs v5)

| Evaluation Metric | v4 (`da2-gamus-full`) | v5 (`da2-base-v5-urban3d`) | Diff |
| :--- | :--- | :--- | :--- |
| **(a) GAMUS 30 Test Tiles (0.6 m)** | | | |
| Colour RMSE | 2.51 m | 2.47 m | -0.04 m |
| Panchromatic RMSE | 2.86 m | 2.76 m | -0.10 m |
| **(b) Urban 3D Test Split** | | | |
| Absolute RMSE | 3.06 m | 1.82 m | -1.24 m |
| **(c) Blind DC LiDAR (Terrain DTM)** | | | |
| Glover Park (Colour) | 4.30 m | 4.37 m | +0.07 m |
| Glover Park (Pan) | 4.70 m | 4.51 m | -0.19 m |
| Capitol Hill East (Colour) | 2.88 m | 2.84 m | -0.04 m |
| Capitol Hill East (Pan) | 3.22 m | 3.13 m | -0.09 m |
| *Mean DC LiDAR (Colour)* | *3.59 m* | *3.605 m* | *+0.015 m* |

## 5. Decision
**ALL Ship Rules Hold:**
* (b) Urban 3D performance clearly and drastically improved (-1.24 m RMSE).
* (a) GAMUS 30 Test Tiles performance is strictly better (not worse than +0.05 m).
* (c) Mean DC LiDAR Colour performance is +0.015 m (not worse than +0.05 m limit). 

**Result**: We recommend shipping v5. The final model is saved at `D:\DepthWizard\checkpoints\da2-base-v5-urban3d`, pending team review to be officially copied to `da2-gamus-full`.
