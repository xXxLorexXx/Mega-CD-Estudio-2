# -*- coding: utf-8 -*-
"""Prueba real de GPU Cinepak con contenedores y códecs diferentes."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s
from megacd_conversion import decode_options

def main():
    records=[]
    with tempfile.TemporaryDirectory(prefix='astra2_formatos_') as folder:
        root=Path(folder);ff=s.find_ffmpeg()
        for extension,codec,extra in [('avi','ffv1',[]),('mp4','libx264',['-preset','ultrafast','-crf','20']),
                                      ('mov','mjpeg',['-q:v','3']),('webm','libvpx-vp9',['-deadline','realtime','-cpu-used','8'])]:
            source=root/f'patron.{extension}'
            audio='libopus' if extension=='webm' else ('aac' if extension in ('mov','mp4') else 'pcm_s16le')
            subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=s=320x240:r=24:d=3',
                            '-f','lavfi','-i','sine=f=997:r=48000:d=3','-c:v',codec,*extra,'-c:a',audio,str(source)],
                           check=True,creationflags=s.CREATE_NO_WINDOW)
            outputs=[]
            for mode in ('cpu','auto'):
                work=root/(extension+'_'+mode);work.mkdir()
                target=work/'patron.cin';before=s.gpu.stats();start=time.perf_counter()
                size,frames=s.encode_segment_to_cin(str(source),0,3,24,'keep',4,str(target),str(work),
                    max_cin_bytes=300000,acceleration=mode,video_eq_mode='standard')
                after=s.gpu.stats();check=s.inspect_cin(target,24)
                assert size<=300000 and frames==72 and check['seguro']
                record={'formato':extension,'codec':codec,'modo':mode,'bytes_cin':size,'frames':frames,
                        'seconds':time.perf_counter()-start,'gpu_decode':bool(decode_options(str(source),ff,mode)),
                        'gpu_vectors':after['vectors']-before['vectors'],
                        'gpu_previous':after['previous_calls']-before['previous_calls'],
                        'gpu_available':after['available'],'gpu_reason':after['reason']}
                if mode=='auto':assert record['gpu_vectors']>0 and record['gpu_previous']>0,record
                outputs.append(target.read_bytes());records.append(record)
            # La ruta CUDA conserva el mismo resultado en estas muestras válidas.
            assert outputs[0]==outputs[1],f'Diferencia GPU/CPU en {extension}'
            print(extension,records[-1],flush=True)
    out=ROOT/'Pruebas_Astra_2';out.mkdir(exist_ok=True)
    (out/'Formatos_GPU.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print('4 contenedores, 4 códecs; GPU Cinepak activa incluso cuando no hay NVDEC.',flush=True)

if __name__=='__main__':main()
