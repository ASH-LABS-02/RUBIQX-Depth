# DepthWizard v3 training: continue from the v2 model and teach it coarser
# imagery (0.35-2.5 m simulated sensors) with stronger weight on tall objects.
# Writes to a NEW folder; the app's da2-gamus-full model is never touched.
param(
    [string]$Init = 'D:\DepthWizard\checkpoints\da2-base-gamus-v2',
    [string]$Name = 'da2-base-gamus-v3',
    [int]$Epochs = 12,
    [int]$Batch = 4,
    [double]$TallWeight = 1.0,
    [double]$Lr = 3e-6,
    [switch]$Smoke,
    [switch]$Resume
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runRoot = 'D:\DepthWizard'
$data = Join-Path $runRoot 'GAMUS'
$python = Join-Path $runRoot 'venv\Scripts\python.exe'
if ($Smoke) { $Name = 'gamus-v3-smoke' }
$out = Join-Path $runRoot "checkpoints\$Name"
$env:TEMP = Join-Path $runRoot 'tmp'; $env:TMP = $env:TEMP
$env:HF_HOME = Join-Path $runRoot 'hf-cache'; $env:PYTHONUTF8 = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'; $env:PYTHONWARNINGS = 'ignore'
$env:DEPTHWIZARD_MODEL_CACHE = Join-Path $runRoot 'model-cache'
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
Set-Location -LiteralPath $projectRoot

$trainArgs = @('scripts\finetune_gamus.py',
    '--rgb', "$data\images\train\*.h5", '--height', "$data\heights\train\*.h5",
    '--val-rgb', "$data\images\val\*.h5", '--val-height', "$data\heights\val\*.h5",
    '--out', $out, '--model', $Init,
    '--target', 'metric', '--tall-weight', "$TallWeight",
    '--net-gsd', '0.65', '--scale-jitter', '0.15', '--sat-aug', '--res-range', '0.35', '2.5',
    '--select', 'absolute', '--lr', "$Lr",
    '--epochs', "$Epochs", '--batch', "$Batch",
    '--size', '518', '--amp-dtype', 'bf16', '--require-cuda')
if ($Smoke) { $trainArgs += @('--epochs', '1', '--max-steps', '20', '--max-train-samples', '80', '--max-val-samples', '20') }
if ($Resume) { $trainArgs += '--resume' }
$ErrorActionPreference = 'Continue'
& $python @trainArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $runRoot "$Name.log")
if ($LASTEXITCODE -ne 0) { throw "Training exited with code $LASTEXITCODE" }
if ($Smoke) { return }

# 1) absolute score on the 30 held-out test tiles (v2: 2.62 m)
& $python scripts\evaluate_gamus_h5.py --root $data --models $out --mode both `
    --out (Join-Path $runRoot "evaluation\$Name") 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 45
# 2) resolution stress test, v2 vs v3, 0.33-10 m
& $python scripts\resolution_sweep.py --root $data `
    --models 'D:\DepthWizard\checkpoints\da2-base-gamus-v2' $out `
    --out (Join-Path $runRoot "evaluation\res-sweep-v3") 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 20
