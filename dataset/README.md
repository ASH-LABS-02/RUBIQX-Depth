# DepthWizard Datasets & Benchmark Reference

This document details the datasets used for training, calibrating, and benchmarking **DepthWizard** for single-view satellite and aerial Digital Surface Model (DSM) height estimation.

---

## 1. Overview of Data Architecture

To maintain a fast, clean Git repository (< 100 MB clone size), large raw training corpora (41+ GB) are hosted on public data repositories (Hugging Face Datasets) and downloaded on demand, while lightweight, curated benchmark samples are packaged directly within the repository.

```
DepthWizard/
├── dataset/                     # Dataset documentation & specifications (this folder)
├── samples/                     # Included immediate out-of-the-box benchmark samples
│   ├── dc_lidar/               # Washington DC urban LiDAR DSM (0.5m GSD)
│   ├── quesenbank/             # Quesenbank forest canopy orthomosaic + 30m DEM
│   ├── gamus/                  # Sample aerial RGB + AGL pairs (DC & NYC)
│   └── synthetic/              # Synthetic validation scene with GCPs
└── scripts/
    ├── download_gamus_training.py  # One-command automated dataset downloader
    ├── prepare_dc_lidar_sample.py  # Prepares urban LiDAR samples
    └── finetune_gamus.py           # Training pipeline on local datasets
```

---

## 2. Included Lightweight Samples (Ready to Test Immediately)

No downloads are required to evaluate DepthWizard. The following curated scenes are already committed to [`samples/`](../samples/):

| Dataset Sample | Modality | Ground Sampling Distance (GSD) | Reference Ground Truth | Primary Validation Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Washington DC (Glover Park & Capitol Hill East)** | Aerial Orthophoto (RGB) | 0.50 m / pixel | 2024 USGS 3DEP LiDAR DSM (0.5m) | Urban LoD1 3D building height & roof elevation recovery |
| **Quesenbank Forest (North & South)** | High-res Drone Orthomosaic | 0.20 m / pixel | Multi-spectral LiDAR DSM | Canopy penetration & DEM terrain correction ($H_{canopy}$) |
| **GAMUS Samples (NYC & DC)** | High-res Aerial RGB | 0.50 m / pixel | Normalized DSM / AGL GeoTIFF | Zero-shot monocular foundation model evaluation |
| **Synthetic Terrain** | Orthophoto + Low-res DEM | 1.00 m / pixel | Exact Mathematical Surface | Ground Control Point (GCP) spline calibration verification |

To test any included scene in the viewer:
```bash
python run.py
# Open http://127.0.0.1:8000 in your browser
```

---

## 3. Large-Scale Training Dataset: GAMUS (41.2 GB)

For training or fine-tuning Depth Anything V2 foundation backbones on geospatial overhead imagery, DepthWizard supports the **GAMUS** (Global Aerial Multimodal Urban Satellite) dataset.

### Specifications
* **Total Size:** ~41.2 GB
* **Total Tiles:** 5,893 paired 512×512 tiles across multiple world cities
* **Inputs:** Single-view 3-channel optical aerial/satellite RGB
* **Ground Truth:** Absolute Above-Ground Level (AGL) / nDSM height rasters in meters
* **Splits:**
  * **Train:** 5,004 paired images & height maps (~35.0 GB)
  * **Validation:** 859 paired images & height maps (~6.0 GB)
  * **Test Benchmark:** 30 paired images & height maps (~210 MB)

### Automated Download
To download the full GAMUS dataset to an external or secondary drive (e.g. `D:/DepthWizard/GAMUS`):

```bash
# 1. Install huggingface hub (if not already installed)
pip install huggingface_hub

# 2. Download and extract training tiles automatically
python scripts/download_gamus_training.py --out-dir D:/DepthWizard/GAMUS
```

The script downloads the parquet/image archives from Hugging Face, validates checksums, and organizes them into:
```
D:/DepthWizard/GAMUS/
├── images/
│   ├── train/     # 5,004 RGB PNG tiles
│   ├── val/       # 859 RGB PNG tiles
│   └── test/      # 30 RGB PNG tiles
└── heights/
    ├── train/     # 5,004 AGL height TIFFs (meters)
    ├── val/       # 859 AGL height TIFFs (meters)
    └── test/      # 30 AGL height TIFFs (meters)
```

---

## 4. Fine-Tuning DepthWizard on Local Datasets

Once the dataset is in place, fine-tuning Depth Anything V2 (Small, Base, or Large) is run using `scripts/finetune_gamus.py`:

```powershell
# Run with GPU acceleration (NVIDIA RTX 4060 or better)
D:\DepthWizard\venv\Scripts\python.exe scripts\finetune_gamus.py `
    --data-dir D:\DepthWizard\GAMUS `
    --backbone small `
    --epochs 10 `
    --batch-size 8 `
    --lr 1e-4 `
    --output-dir D:\DepthWizard\checkpoints\da2-gamus-full
```

### Loss Formulations Used
1. **Scale-and-Shift Invariant Loss ($\mathcal{L}_{ssi}$):** Aligns relative disparity with ground truth AGL height without penalizing global scale offsets.
2. **Multi-Scale Gradient Edge Loss ($\mathcal{L}_{grad}$):** Enforces sharp roof perimeters, building facades, and steep terrain escarpments.
3. **Pearson Correlation Loss ($\mathcal{L}_{corr}$):** Penalizes negative structural correlation across varied terrain gradients.

---

## 5. Reference DEM Datasets (Global Vertical Calibration)

DepthWizard integrates with global satellite-derived Digital Elevation Models for absolute vertical datum scaling:
* **Copernicus GLO-30 (COP30):** 30-meter global radar DEM (European Space Agency, EGM2008 datum). Default for global operations.
* **SRTM 30m (SRTMGL1):** 30-meter Shuttle Radar Topography Mission (NASA/USGS, EGM96 datum).
* **CartoDEM (ISRO):** Cartosat-1 Indian sub-continent high-resolution stereo DEM.

DEM tiles can be uploaded directly in the studio UI or automatically fetched using Copernicus Open Access APIs during inference.
