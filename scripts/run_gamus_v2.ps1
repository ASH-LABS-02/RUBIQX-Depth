# DepthWizard v2 training: Base model, metric loss, app-matched resolution,
# tall-pixel weighting, satellite augmentation, checkpoint chosen on absolute error.
# Writes to a NEW folder, so the current da2-gamus-full model is never touched.
param(
    [string]$Model = 'base',          # small | base
    [int]$Epochs = 30,
    [int]$Batch = 4,
    [int]$GradAccum = 1,
    [switch]$Smoke,                   # quick 20-step test run
    [switch]$Resume
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runRoot = 'D:\DepthWizard'
$data = Join-Path $runRoot 'GAMUS'
$python = Join-Path $runRoot 'venv\Scripts\python.exe'
$name = if ($Smoke) { 'gamus-v2-smoke' } else { "da2-$Model-gamus-v2" }
$out = Join-Path $runRoot "checkpoints\$name"
$env:TEMP = Join-Path $runRoot 'tmp'; $env:TMP = $env:TEMP
$env:HF_HOME = Join-Path $runRoot 'hf-cache'; $env:PYTHONUTF8 = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'; $env:PYTHONWARNINGS = 'ignore'
$env:DEPTHWIZARD_MODEL_CACHE = Join-Path $runRoot 'model-cache'   # keep downloads off C:
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
Set-Location -LiteralPath $projectRoot

$trainArgs = @('scripts\finetune_gamus.py',
    '--rgb', "$data\images\train\*.h5", '--height', "$data\heights\train\*.h5",
    '--val-rgb', "$data\images\val\*.h5", '--val-height', "$data\heights\val\*.h5",
    '--out', $out, '--model', $Model,
    '--target', 'metric', '--tall-weight', '0.5',
    '--net-gsd', '0.65', '--scale-jitter', '0.15', '--sat-aug', '--select', 'absolute',
    '--epochs', "$Epochs", '--batch', "$Batch", '--grad-accum', "$GradAccum",
    '--size', '518', '--amp-dtype', 'bf16', '--require-cuda')
if ($Smoke) { $trainArgs += @('--epochs', '1', '--max-steps', '20', '--max-train-samples', '80', '--max-val-samples', '20') }
if ($Resume) { $trainArgs += '--resume' }
# Python writes warnings to stderr; don't let PowerShell treat them as fatal
$ErrorActionPreference = 'Continue'
& $python @trainArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $runRoot "$name.log")
if ($LASTEXITCODE -ne 0) { throw "Training exited with code $LASTEXITCODE" }

# absolute (no fitting) score on the 30 held-out test tiles - compare with 3.46 m
& $python scripts\evaluate_gamus_h5.py --root $data --models $out --mode both `
    --out (Join-Path $runRoot "evaluation\$name")
