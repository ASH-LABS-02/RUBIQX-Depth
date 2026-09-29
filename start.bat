@echo off
REM One-command launcher (Windows)
cd /d %~dp0
REM Use a configured Python, or the CUDA environment prepared on this laptop.
if defined DEPTHWIZARD_PYTHON goto configured_python
if exist "D:\DepthWizard\venv\Scripts\python.exe" goto cuda_python
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
python run.py %*
exit /b %errorlevel%

:configured_python
"%DEPTHWIZARD_PYTHON%" run.py %*
exit /b %errorlevel%

:cuda_python
"D:\DepthWizard\venv\Scripts\python.exe" run.py %*
exit /b %errorlevel%
