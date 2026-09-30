@echo off
REM Build a portable, offline DepthWizard folder (dist\DepthWizard\DepthWizard.exe)
cd /d %~dp0\..
if not exist models\da2-gamus-full (
  echo Copying the fine-tuned checkpoint into models\da2-gamus-full ...
  xcopy /E /I /Y "D:\DepthWizard\checkpoints\da2-gamus-full" models\da2-gamus-full
)
python -m pip install pyinstaller
set HF_HUB_OFFLINE=1
pyinstaller packaging\depthwizard.spec --noconfirm --distpath dist --workpath build
echo.
echo Built dist\DepthWizard\DepthWizard.exe  (copy the whole folder; no Python needed)
