@echo off
setlocal
set "DISTRO=Ubuntu-24.04"
if defined MEGACD_CAVC_DISTRO set "DISTRO=%MEGACD_CAVC_DISTRO%"
pushd "%~dp0cavc"
wsl.exe -d "%DISTRO%" -u root --exec apt-get update
if errorlevel 1 goto fail
wsl.exe -d "%DISTRO%" -u root --exec apt-get install -y --no-install-recommends ffmpeg genisoimage binutils-m68k-linux-gnu gcc-m68k-linux-gnu make python3-venv ca-certificates
if errorlevel 1 goto fail
wsl.exe -d "%DISTRO%" --exec bash tools/astra_setup.sh
if errorlevel 1 goto fail
popd
echo CAVC listo.
pause
exit /b 0
:fail
popd
echo No se pudo preparar CAVC. Comprueba que Ubuntu-24.04 este instalado en WSL.
pause
exit /b 1
