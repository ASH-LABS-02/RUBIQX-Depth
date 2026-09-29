# Fine-tuning Depth Anything V2 on GAMUS

The two real scenes in `samples/gamus/` are held-out checks. The pretrained small model gave poor affine-aligned height scores on them, so a remote-sensing fine-tune is a sensible next experiment. GAMUS supplies co-registered RGB and above-ground height (AGL/nDSM), not absolute terrain elevation. The trained backbone still needs DepthWizard's DEM or GCP calibration for an absolute DSM.

## This laptop

Detected RTX 4060 Laptop GPU with 8 GB VRAM and an NVIDIA driver reporting CUDA 13.2. Python on C: initially had a CPU-only PyTorch build. D: had about 295 GB free when this guide was prepared. Put the dataset, CUDA environment, and checkpoints on D:; use C: for the small project source and sample files.

## Prepare the CUDA environment

From the project directory in PowerShell:

```powershell
python -m venv D:\DepthWizard\venv
$py = 'D:\DepthWizard\venv\Scripts\python.exe'
& $py -m pip install torch==2.13.0+cu130 torchvision==0.28.0+cu130 --index-url https://download.pytorch.org/whl/cu130
& $py -m pip install -r requirements.txt h5py huggingface_hub
& $py -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The last command must print `True` for CUDA before a full run. CUDA wheel availability and the driver should be checked against the [current PyTorch installer](https://docs.pytorch.org/get-started/locally/) if this environment changes.

## Download training data

For a quick GPU and data-loader trial, take four pairs per city from each of the train and validation splits:

```powershell
& $py scripts/download_gamus_training.py --root D:\DepthWizard\GAMUS --per-city 4
```

To grow that same folder to the full **RGB + AGL train and validation splits**:

```powershell
& $py scripts/download_gamus_training.py --root D:\DepthWizard\GAMUS
```

The full GAMUS repository is about 80 GB including semantic classes and the test split. Height fine-tuning needs the RGB + AGL train/validation files only. The downloader retains the official split and leaves the test split out of training. Re-running it resumes files already present. If Hugging Face rate limits an unauthenticated download, sign in with `hf auth login` and rerun.

## Run a trial, then 10 epochs

Run a short trial first:

```powershell
& $py scripts/finetune_gamus.py `
  --rgb 'D:\DepthWizard\GAMUS\images\train\*.h5' --height 'D:\DepthWizard\GAMUS\heights\train\*.h5' `
  --val-rgb 'D:\DepthWizard\GAMUS\images\val\*.h5' --val-height 'D:\DepthWizard\GAMUS\heights\val\*.h5' `
  --out 'D:\DepthWizard\checkpoints\da2-gamus-pilot' --batch 4 --grad-accum 1 --size 518 `
  --max-steps 2 --epochs 10 --require-cuda
```

After the full data download, run the same command without `--max-steps` and change `--out` to `D:\DepthWizard\checkpoints\da2-gamus-full`. A two-step trial at batch 4 peaked at **2.05 GiB VRAM** on this RTX 4060. If a later run runs out of memory, use `--batch 1 --grad-accum 4` for the same effective batch size. The trainer reads HDF5 directly, masks the exact `-5` void value, saves the best model by validation RMSE, and writes `last.pt` after every epoch. If interrupted, add `--resume` with the same epoch count and other settings. The 10-epoch full-data run may take many hours on a laptop; watch GPU memory and temperature.

The short trial checkpoint is only a pipeline check. Start the full run in a **new output directory** so its partial learning and schedule do not carry into the real run.

For this laptop, `scripts/run_gamus_4060.ps1` runs the full RGB/AGL download, 10-epoch training, and held-out evaluation in sequence. It resumes an existing `last.pt` checkpoint when restarted. Check `D:\DepthWizard\training-status.json`, `training.out.log`, and `training.err.log` for progress. The best validation checkpoint goes to `D:\DepthWizard\checkpoints\da2-gamus-full`; the browser's model menu detects it as **GAMUS fine-tuned · local prototype** once an epoch has completed. `start.bat` uses the prepared D: CUDA Python automatically, or the interpreter selected by `DEPTHWIZARD_PYTHON`.

## Evaluate held-out data

Keep `samples/gamus/` out of train and validation. After fine-tuning:

```powershell
& $py scripts/evaluate_batch.py samples/gamus/manifest.csv `
  --model 'D:\DepthWizard\checkpoints\da2-gamus-full' --gsd 0.33 `
  -o 'D:\DepthWizard\evaluation\gamus-test'
```

Compare each tile's RMSE, MAE, and correlation with `samples/gamus/README.md`. An improvement on only two tiles is a promising signal; a credible PS 26175 accuracy claim needs a wider held-out set and georeferenced imagery with true absolute DSM/DEM references.

For a broader relative-height check, download ten test RGB/AGL pairs per GAMUS city (30 pairs total) and score both backbones without converting them to intermediate rasters:

```powershell
& $py scripts/download_gamus_training.py --root D:\DepthWizard\GAMUS --splits test --per-city 10
& $py scripts/evaluate_gamus_h5.py --root D:\DepthWizard\GAMUS `
  --models small D:\DepthWizard\checkpoints\da2-gamus-full `
  --out D:\DepthWizard\evaluation\gamus-30-test
```

The test script fits scale and shift using each tile's full AGL reference, then reports RMSE, MAE and correlation. This is a relative-shape diagnostic, **not** an operational absolute DSM estimate. The GAMUS validation score during training uses the same affine alignment. One validation center crop (`DC_54_25`) has no valid height pixels and is excluded from that score; the full tile itself contains usable pixels.
