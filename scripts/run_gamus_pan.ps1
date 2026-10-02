# Short panchromatic fine-tune: continue from v2 with the same recipe, but a
# third of the training crops are greyscale (Cartosat-2S 0.6 m is single-band).
# Validates on colour AND greyscale every epoch; keeps the best average.
# Writes to a NEW folder; the app's model is never touched.
param(
    [string]$Init = 'D:\DepthWizard\checkpoints\da2-base-gamus-v2',
    [string]$Name = 'da2-base-gamus-v2-pan',
    [int]$Epochs = 6,
    [int]$Batch = 4,
    [int]$GradAccum = 1,
    [double]$GrayProb = 0.33,
    [double]$Lr = 3e-6,
    [switch]$Smoke
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runRoot = 'D:\DepthWizard'
$data = Join-Path $runRoot 'GAMUS'
$python = Join-Path $runRoot 'venv\Scripts\python.exe'
if ($Smoke) { $Name = 'gamus-pan-smoke' }
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
    '--target', 'metric', '--tall-weight', '0.5',
    '--net-gsd', '0.65', '--scale-jitter', '0.15', '--sat-aug', '--gray-prob', "$GrayProb",
    '--select', 'absolute', '--lr', "$Lr",
    '--epochs', "$Epochs", '--batch', "$Batch", '--grad-accum', "$GradAccum",
    '--size', '518', '--amp-dtype', 'bf16', '--require-cuda')
if ($Smoke) { $trainArgs += @('--epochs', '1', '--max-steps', '20', '--max-train-samples', '80', '--max-val-samples', '20') }
$ErrorActionPreference = 'Continue'
& $python @trainArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $runRoot "$Name.log")
if ($LASTEXITCODE -ne 0) { throw "Training exited with code $LASTEXITCODE" }
if ($Smoke) { return }

# colour + greyscale + panchromatic on the 30 test tiles (v2: 2.48 colour / 3.35 pan at 0.6 m)
& $python scripts\pan_check.py --model $out --out (Join-Path $runRoot "evaluation\$Name") 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 8
# blind DC, colour and panchromatic (v2: 4.35 / 2.86 colour, 4.92 / 3.17 pan)
foreach ($s in 'glover_park', 'capitol_hill_east') {
    foreach ($kind in 'rgb', 'pan') {
        $img = if ($kind -eq 'rgb') { "samples\dc_lidar\$s\rgb.tif" } else { "$runRoot\evaluation\pan\${s}_pan.tif" }
        $o = Join-Path $runRoot "evaluation\$Name\dc_${kind}_$s"
        & $python -m depthwizard $img --dem "samples\dc_lidar\$s\dtm_2018_32m.tif" --ref "samples\dc_lidar\$s\lidar_dsm_2024.tif" `
            --model $out --scene urban -o $o 2>&1 | Out-Null
        & $python -c "import json;a=json.load(open(r'$o\metrics.json'))['absolute'];print('$s $kind', round(a['rmse'],2), round(a['mae'],2), round(a['r'],3))"
    }
}
