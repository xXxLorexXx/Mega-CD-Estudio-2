@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Grabador Automatico Sega Mega CD (ASUS + ImgBurn)
color 0B

echo ===============================================================================
echo    GRABADOR AUTOMATICO SEGA MEGA CD - IMGBURN + UNIDAD ASUS
echo ===============================================================================
echo.

:: 1. Localizar ImgBurn.exe
set "IMGBURN="
for %%P in (
    "S:\ImgBurn.v2.5.8.0.Multilingual-PORTABLE\ImgBurn\ImgBurn.exe"
    "%ProgramFiles(x86)%\ImgBurn\ImgBurn.exe"
    "%ProgramFiles%\ImgBurn\ImgBurn.exe"
    "%LOCALAPPDATA%\Programs\ImgBurn\ImgBurn.exe"
) do (
    if exist "%%~P" (
        set "IMGBURN=%%~P"
        goto :FoundImgBurn
    )
)

:: Buscar en todas las unidades si se movio la carpeta portable
for %%D in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do (
    if exist "%%D:\ImgBurn.v2.5.8.0.Multilingual-PORTABLE\ImgBurn\ImgBurn.exe" (
        set "IMGBURN=%%D:\ImgBurn.v2.5.8.0.Multilingual-PORTABLE\ImgBurn\ImgBurn.exe"
        goto :FoundImgBurn
    )
    if exist "%%D:\ImgBurn\ImgBurn.exe" (
        set "IMGBURN=%%D:\ImgBurn\ImgBurn.exe"
        goto :FoundImgBurn
    )
)

echo [ERROR] No se encontro ImgBurn.exe en el sistema.
pause
exit /b 1

:FoundImgBurn
echo [OK] ImgBurn detectado: "%IMGBURN%"

:: 2. Detectar unidad grabadora ASUS automaticamente (o cualquier grabadora CD/DVD disponible)
set "ASUS_DRIVE="
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$d = Get-CimInstance Win32_CDROMDrive | Where-Object { $_.Name -match 'ASUS' -or $_.Caption -match 'ASUS' } | Select-Object -First 1 -ExpandProperty Drive; if (-not $d) { $d = Get-CimInstance Win32_CDROMDrive | Select-Object -First 1 -ExpandProperty Drive }; Write-Output $d"`) do (
    set "ASUS_DRIVE=%%A"
)

if "%ASUS_DRIVE%"=="" (
    set "ASUS_DRIVE=F:"
    echo [AVISO] No se detecto unidad ASUS por WMI. Se usara por defecto: %ASUS_DRIVE%
) else (
    echo [OK] Unidad grabadora ASUS detectada en: %ASUS_DRIVE%
)

:: 3. Obtener carpeta o archivo .CUE (soporta arrastrar carpeta/archivo o cuadro de dialogo)
set "SELECTED_PATH=%~1"

if not "%SELECTED_PATH%"=="" goto :CheckInput

echo.
echo Abriendo ventana para seleccionar la carpeta del video Mega CD...
for /f "usebackq tokens=*" %%F in (`powershell -NoProfile -Sta -Command "Add-Type -AssemblyName System.Windows.Forms; $dlg = New-Object System.Windows.Forms.FolderBrowserDialog; $dlg.Description = 'Selecciona la carpeta que contiene el archivo .CUE del video Mega CD'; $dlg.ShowNewFolderButton = $false; if ($dlg.ShowDialog() -eq 'OK') { Write-Output $dlg.SelectedPath }"`) do (
    set "SELECTED_PATH=%%F"
)

if "%SELECTED_PATH%"=="" (
    echo.
    echo [CANCELADO] No se selecciono ninguna carpeta.
    timeout /t 3
    exit /b 0
)

:CheckInput
set "CUE_FILE="

:: Comprobar si se paso directamente un archivo .cue o .iso
if /i "%SELECTED_PATH:~-4%"==".cue" (
    if exist "%SELECTED_PATH%" set "CUE_FILE=%SELECTED_PATH%"
)
if /i "%SELECTED_PATH:~-4%"==".iso" (
    if exist "%SELECTED_PATH%" set "CUE_FILE=%SELECTED_PATH%"
)

if not "%CUE_FILE%"=="" goto :FoundCue

:: Buscar prioridad VIDEO_MEGACD_PAL_EUR.CUE o cualquier .CUE en la carpeta seleccionada
if exist "%SELECTED_PATH%\VIDEO_MEGACD_PAL_EUR.CUE" (
    set "CUE_FILE=%SELECTED_PATH%\VIDEO_MEGACD_PAL_EUR.CUE"
    goto :FoundCue
)

for %%C in ("%SELECTED_PATH%\*.cue") do (
    set "CUE_FILE=%%~fC"
    goto :FoundCue
)

:: Si no hay en la raiz de la carpeta, buscar en subcarpetas inmediatas
for /f "delims=" %%C in ('dir /b /s "%SELECTED_PATH%\*.cue" 2^>nul') do (
    set "CUE_FILE=%%~fC"
    goto :FoundCue
)

:: Respaldo por si solo esta el .ISO
if exist "%SELECTED_PATH%\VIDEO_MEGACD_PAL_EUR.ISO" (
    set "CUE_FILE=%SELECTED_PATH%\VIDEO_MEGACD_PAL_EUR.ISO"
    goto :FoundCue
)

echo.
echo [ERROR] No se encontro ningun archivo .CUE ni .ISO en:
echo "%SELECTED_PATH%"
echo.
pause
exit /b 1

:FoundCue
echo [OK] Imagen seleccionada: "%CUE_FILE%"
echo.
echo ===============================================================================
echo  Configuracion aplicada automaticamente:
echo   - Modo:        Grabar imagen en disco (Write Mode / DAO)
echo   - Destino:     Grabadora ASUS (%ASUS_DRIVE%)
echo   - Velocidad:   10x / Minima soportada por el CD-R (Optimo para Mega CD)
echo   - Verificar:   Activado contra la imagen original
echo   - Expulsar CD: Si, al finalizar
echo ===============================================================================
echo.
echo Iniciando grabacion en ImgBurn... (Si aun no has metido el CD-R virgen,
echo ImgBurn esperara automaticamente a que lo insertes y empezara solo).
echo.

:: Crear configuracion temporal para forzar WriteType=0 (DAO/SAO) sin alterar el registro
set "TEMP_INI=%TEMP%\ImgBurn_MegaCD_Astra3_%RANDOM%.ini"
set "MEGACD_VERIFY_LOG=%~dp0Registro_grabacion_%RANDOM%_%RANDOM%.log"
if not exist "%TEMP_INI%" (
    (
        echo [Settings]
        echo WRITE_WriteType=0
        echo WRITE_Verify=1
        echo WRITE_TestMode=0
        echo WRITE_DeleteImage=0
        echo WRITE_EjectTray=1
    ) > "%TEMP_INI%"
)

start "" /wait "%IMGBURN%" /MODE WRITE /SRC "%CUE_FILE%" /DEST %ASUS_DRIVE% /SPEED 10x /VERIFY YES /TESTMODE NO /EJECT YES /START /WAITFORMEDIA /CLOSESUCCESS /PORTABLE /NOSAVESETTINGS /SETTINGS "%TEMP_INI%" /LOG "%MEGACD_VERIFY_LOG%"

powershell -NoProfile -Command "$t = Get-Content -LiteralPath $env:MEGACD_VERIFY_LOG -Raw -ErrorAction SilentlyContinue; $v = $t.LastIndexOf('Verifying Session '); $ok = $t.LastIndexOf('Operation Successfully Completed!'); if ($v -lt 0 -or $ok -lt $v -or $t -match '(?m)^E\s+\d\d:|Operation Aborted!') { exit 1 }"
if errorlevel 1 (
    echo [AVISO] El CD no ha quedado verificado. No dar por correcta la copia.
    echo Registro: "%MEGACD_VERIFY_LOG%"
    pause
    exit /b 1
)
echo [EXITO] CD grabado y verificado contra la imagen.
echo Registro: "%MEGACD_VERIFY_LOG%"
timeout /t 4
exit /b 0
