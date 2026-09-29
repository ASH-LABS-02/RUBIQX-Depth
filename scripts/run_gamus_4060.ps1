param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runRoot = 'D:\DepthWizard'
$datasetRoot = Join-Path $runRoot 'GAMUS'
$checkpointRoot = Join-Path $runRoot 'checkpoints\da2-gamus-full'
$pythonPath = Join-Path $runRoot 'venv\Scripts\python.exe'
$statusPath = Join-Path $runRoot 'training-status.json'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "CUDA Python environment missing: $pythonPath"
}
New-Item -ItemType Directory -Path (Join-Path $runRoot 'tmp') -Force | Out-Null
$env:TEMP = Join-Path $runRoot 'tmp'
$env:TMP = $env:TEMP
$env:HF_HOME = Join-Path $runRoot 'hf-cache'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $projectRoot

function Set-Stage([string]$stage) {
    @{ stage = $stage; updated = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath
    Write-Output "[$(Get-Date -Format o)] $stage"
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $checkpointRoot 'last.pt'))) {
        Set-Stage 'downloading train and validation RGB/AGL data'
        & $pythonPath scripts\download_gamus_training.py --root $datasetRoot
        if ($LASTEXITCODE -ne 0) { throw "GAMUS download exited with code $LASTEXITCODE" }
    }

    Set-Stage 'training 10 epochs'
    $arguments = @(
        'scripts\finetune_gamus.py',
        '--rgb', (Join-Path $datasetRoot 'images\train\*.h5'),
        '--height', (Join-Path $datasetRoot 'heights\train\*.h5'),
        '--val-rgb', (Join-Path $datasetRoot 'images\val\*.h5'),
        '--val-height', (Join-Path $datasetRoot 'heights\val\*.h5'),
        '--out', $checkpointRoot,
        '--epochs', '10', '--batch', '4', '--grad-accum', '1',
        '--size', '518', '--amp-dtype', 'bf16', '--require-cuda'
    )
    if (Test-Path -LiteralPath (Join-Path $checkpointRoot 'last.pt')) {
        $arguments += '--resume'
    }
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "Training exited with code $LASTEXITCODE" }

    Set-Stage 'evaluating held-out GAMUS tiles'
    & $pythonPath scripts\evaluate_batch.py samples\gamus\manifest.csv `
        --model $checkpointRoot --gsd 0.33 `
        -o (Join-Path $runRoot 'evaluation\gamus-test')
    if ($LASTEXITCODE -ne 0) { throw "Evaluation exited with code $LASTEXITCODE" }
    Set-Stage 'complete'
}
catch {
    Set-Stage "failed: $($_.Exception.Message)"
    throw
}
