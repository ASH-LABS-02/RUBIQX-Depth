# Real GAMUS test samples

These are two paired tiles from the **test** split of [earthflow/GAMUS](https://huggingface.co/datasets/earthflow/GAMUS), the dataset recommended by the [DepthWizard problem statement repository](https://github.com/IMG-PROCESS-SAC/SIH-DepthWizard-2026). GAMUS is released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Cite Xiong et al., [*GAMUS: A Geometry-aware Multi-modal Semantic Segmentation Benchmark for Remote Sensing Data*](https://arxiv.org/abs/2305.14914) when using the data.

| Tile | Image for upload | Reference for upload |
| --- | --- | --- |
| Washington, DC | `DC_03_26_rgb.png` | `DC_03_26_agl.tif` |
| New York City | `NYC_00735_rgb.png` | `NYC_00735_agl.tif` |

Each RGB image and height raster is 1024 × 1024. The paper gives a ground sampling distance of **0.33 m/pixel**. The reference is an above-ground height map (AGL/nDSM) derived from LiDAR, **not an absolute elevation DSM**. The source HDF5 files have no coordinate reference system, so these samples exercise DepthWizard's non-georeferenced rDSM path. They cannot validate the GeoTIFF + DEM path.

In the app, choose **Import image**, upload one PNG, open **Reference validation** and upload its matching AGL TIFF, then set **Pixel size for non-georeferenced input** to `0.33`. Leave DEM and GCP empty. Pick **Depth Anything V2 · Small**. The **Validate** tab reports affine-aligned metrics against the AGL reference. Because this alignment uses the reference values, those scores measure shape recovery; they are not standalone absolute-height accuracy.

`raw/` preserves the four original HDF5 files. `scripts/prepare_gamus_samples.py` downloads and converts them, verifies their SHA-256 hashes, and maps the exact `-5` void value in the DC height tile to GeoTIFF nodata. Other values are preserved. `manifest.csv` is ready for `scripts/evaluate_batch.py`. Keep these tiles out of training and validation sets.

Pretrained Depth Anything V2 Small, before remote-sensing fine-tuning, scored:

| Held-out tile | Affine RMSE | MAE | Pearson r |
| --- | ---: | ---: | ---: |
| DC_03_26 | 10.82 m | 9.31 m | 0.078 |
| NYC_00735 | 6.80 m | 5.70 m | 0.337 |

These are two diagnostic tiles, not a dataset-wide benchmark. Re-run them after fine-tuning and then evaluate more held-out scenes from varied landscapes.
