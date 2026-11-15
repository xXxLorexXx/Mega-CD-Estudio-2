# -*- coding: utf-8 -*-
"""Validación de datos con patrones repetidos; no sustituye una prueba de consola.

Codifica ocho segundos reales por FPS y repite esos fotogramas ya codificados
para verificar archivos de 1/2/3/5/10 minutos. Se declara esa metodología en CSV.
"""
import argparse
import csv
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as studio
from megacd_runtime import chapter_plan, inspect_cin, write_safety_report
from make_repeat import repeat_cin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'Pruebas')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    video = args.work/'patrones.mkv'
    ffmpeg = studio.find_ffmpeg()
    patterns = [
        'testsrc2=s=256x192:r=24:d=2',
        'nullsrc=s=256x192:r=24:d=2,noise=alls=100:allf=t+u',
        'testsrc2=s=256x192:r=24:d=2,eq=brightness=-0.3:gamma=0.7',
        "testsrc2=s=256x192:r=96:d=0.5,setpts=4*PTS",
    ]
    # El cuarto patrón cambia rápidamente de contenido espacial.
    patterns[3] = 'testsrc2=s=256x192:r=24:d=2,scroll=horizontal=0.15:vertical=0.1'
    cmd = [ffmpeg, '-hide_banner','-loglevel','error','-y']
    for pattern in patterns:
        cmd += ['-f','lavfi','-i',pattern]
    cmd += ['-f','lavfi','-i','sine=f=997:r=48000:d=8',
            '-filter_complex','[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]',
            '-map','[v]','-map','4:a','-c:v','ffv1','-c:a','pcm_s16le',str(video)]
    subprocess.run(cmd, check=True, creationflags=studio.CREATE_NO_WINDOW)
    rows = []
    for fps in (8,10,12,15,20,24):
        short = args.work/f'patrones_{fps}.cin'
        studio.encode_segment_to_cin(str(video),0,8,fps,'keep',4,str(short),str(args.work),note_cb=lambda m: print(m,flush=True))
        for minutes in (1,2,3,5,10):
            plan = chapter_plan(minutes*60,minutes*60,fps)
            checks=[]
            for idx, part in enumerate(plan):
                target=args.work/f'fixture_{fps}_{minutes}_{idx}.cin'
                repeat_cin(short,target,part['duration'],fps)
                check=inspect_cin(target,fps)
                assert check['seguro'], check
                checks.append(check)
                target.unlink()
            assert abs(sum(c['duracion_s'] for c in checks)-minutes*60) < 1e-8
            row={'fps':fps,'capitulo_min':minutes,'cin_fisicos':len(checks),
                 'media_max_B_s':round(max(c['media_B_s'] for c in checks),2),
                 'pico_max_B_s':round(max(c['pico_6s_B_s'] for c in checks),2),
                 'stab_max':max(c['entradas_stab'] for c in checks),
                 'duracion_total_s':sum(c['duracion_s'] for c in checks),
                 'resultado':'APTO_DATOS',
                 'metodo':'8 s codificados reales: testsrc2/ruido/oscuro/movimiento; repetición de patrones para duración larga'}
            rows.append(row)
            print(row,flush=True)
        with (args.output/'Tabla_caudal.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    # Comparación del procesado PCM con la versión 7.0 original del usuario.
    old_path=ROOT/'Codigo_Fuente_MegaCD_Video_Studio_7_0.py'
    spec=importlib.util.spec_from_file_location('studio70',old_path)
    old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    audio=[]
    for mode in ('cinema_boost','max_boost','standard','original'):
        for clarity in (False,True):
            a=old.extract_segment_pcm(str(video),0,8,str(args.work),audio_mode=mode,vocal_clarity=clarity)
            b=studio.extract_segment_pcm(str(video),0,8,str(args.work),audio_mode=mode,vocal_clarity=clarity)
            assert a == b
            audio.append({'modo':mode,'claridad':clarity,'bytes':len(a),'bytes_distintos':sum(x!=y for x,y in zip(a,b))})
    (args.output/'Comparacion_audio.json').write_text(json.dumps(audio,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VALIDACIÓN DE MEDIOS COMPLETA',flush=True)


if __name__ == '__main__':
    main()
