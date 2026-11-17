@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 exit /b 1
set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
"%PY%" compilar.py
if errorlevel 1 exit /b 1
echo Listo: Mega CD Estudio 14.1.exe
popd
pause
