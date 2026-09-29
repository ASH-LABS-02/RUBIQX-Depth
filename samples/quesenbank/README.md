# Quesenbank georeferenced forest samples

These are two 1024 × 1024 crops from the [Quesenbank 2022 UAV data release](https://zenodo.org/records/7554598) by Jackisch, Putzenlechner, and Dietze. The release is CC BY 4.0. The RGB orthomosaic and DSM come from the same survey; the published DEM is downsampled to 30 m pixels here to exercise DepthWizard's low-resolution DEM calibration path. The DSM is kept separate as a reference, not used in calibration or training.

Run `python scripts/prepare_quesenbank_sample.py` from the project root to verify the original files and recreate the crops. The script checks the publisher's MD5 hashes. The original RGB and DSM are tagged EPSG:32632. The source DEM lacks CRS and nodata metadata but its projected coordinates overlap the other rasters; the preparation script assigns that CRS and treats its zero-valued exterior as void. Each prepared crop has more than 99% valid RGB/DSM coverage. The two crops overlap and should be treated as one site, not independent benchmark scenes.

To run both scenes with the pretrained backbone:

```powershell
& 'D:\DepthWizard\venv\Scripts\python.exe' scripts\evaluate_batch.py samples\quesenbank\manifest.csv --model small -o D:\DepthWizard\evaluation\quesenbank-pretrained
```

The pretrained baseline was:

| Crop | DSM RMSE | DSM MAE | Correlation | 30 m DEM baseline RMSE |
|---|---:|---:|---:|---:|
| forest_north | 9.18 m | 6.00 m | 0.408 | 9.06 m |
| forest_south | 8.56 m | 5.45 m | 0.706 | 10.15 m |

These values are absolute DSM errors against the survey DSM after calibration from the coarse DEM. This forest site is outside the GAMUS urban training data. The high-resolution DEM and DSM are from the same survey, so the check validates the geospatial pipeline and transfer to a different landscape, but it is not independent LiDAR validation.

The trained GAMUS checkpoint improves further when DEM fusion uses a nonnegative, ground-anchored structure component:

| Crop | Previous fine-tuned RMSE | Current fine-tuned RMSE | Current MAE | Current correlation |
|---|---:|---:|---:|---:|
| forest_north | 7.87 m | 6.99 m | 3.55 m | 0.681 |
| forest_south | 7.71 m | 6.41 m | 3.65 m | 0.807 |
| **Mean** | **7.79 m** | **6.70 m** | **3.60 m** | **0.744** |

Run `scripts/evaluate_batch.py` with `--model D:\DepthWizard\checkpoints\da2-gamus-full` to reproduce. The 30 m calibration DEM was derived from the same survey as the DSM, so these scores remain a development check rather than independent elevation validation. This adjustment was selected after inspecting these crops; a separate sensor/site test is required before claiming generalization.

In the browser, import `forest_north_rgb.tif` or `forest_south_rgb.tif`, add its matching `_dem_30m.tif` under **Absolute scale**, and add `_reference_dsm.tif` under **Reference validation**. Use the model selector to compare pretrained and fine-tuned results. The completed 10-epoch checkpoint is at `D:\DepthWizard\checkpoints\da2-gamus-full`.
