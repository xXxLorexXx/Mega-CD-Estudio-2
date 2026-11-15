# -*- coding: utf-8 -*-
"""Compilación reproducible. Ejecutar con el entorno de requirements.txt."""
from pathlib import Path
import importlib.util
import os
import shutil
import sys
import PyInstaller.__main__

ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
scratch=Path(os.environ.get('MEGACD_BUILD_DIR',str(ROOT/'compilacion'))).resolve()
scratch.mkdir(parents=True,exist_ok=True)
arguments=['--noconfirm','--clean','--onefile','--windowed','--name','Mega CD Estudio 13',
           '--distpath',str(ROOT),'--workpath',str(scratch/'build'),'--specpath',str(scratch),
           '--collect-submodules','numba','--collect-submodules','llvmlite',
           '--hidden-import','Mega_Cd_Grabador_de_Video_Beta',
           '--add-data',f'{ROOT / "megacd_fast.py"};.',
           '--icon',str(ROOT/'astra.ico'),'--add-data',f'{ROOT / "astra.ico"};.']
for excluded in ('numba.tests','numba.cuda','numba.pycc','scipy','IPython','matplotlib','torch','pytest'):
    arguments+=['--exclude-module',excluded]
for name in ('ffmpeg.exe','ffprobe.exe'):
    binary=ROOT/name
    if not binary.is_file():
        found=shutil.which(name)
        if not found:raise SystemExit(f'Falta {name}; colóquelo junto al código o en PATH.')
        binary=Path(found)
    arguments+=['--add-binary',f'{binary};.']
spec=importlib.util.find_spec('nvidia.cuda_nvrtc')
if not spec:raise SystemExit('Instale primero requirements.txt: falta NVRTC.')
nvrtc=Path(next(iter(spec.submodule_search_locations)))/'bin'
for pattern in ('nvrtc64_120_0.dll','nvrtc-builtins64_*.dll'):
    files=list(nvrtc.glob(pattern))
    if not files:raise SystemExit(f'Falta la biblioteca CUDA {pattern}.')
    for binary in files:arguments+=['--add-binary',f'{binary};.']
arguments+=['--add-data',f'{ROOT / "Kega" / "FusionDefaults.ini"};Kega']
arguments.append(str(ROOT/'Mega_Cd_Grabador_de_Video_Beta.py'))
PyInstaller.__main__.run(arguments)
compiler=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if compiler.is_file() and (ROOT/'Grabar_MegaCD_ImgBurn.cs').is_file():
    import subprocess
    subprocess.run([str(compiler),'/nologo','/target:winexe','/reference:System.Windows.Forms.dll',
                    '/out:Grabar_MegaCD_ImgBurn.exe','Grabar_MegaCD_ImgBurn.cs'],check=True,cwd=str(ROOT))
