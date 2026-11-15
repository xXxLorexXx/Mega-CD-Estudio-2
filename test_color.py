# -*- coding: utf-8 -*-
"""Codifica fotogramas FILM y mide sus paletas CRAM reales."""
from pathlib import Path
import csv
import struct
import subprocess
import sys
import tempfile
import numpy as np

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s

def main():
    out=ROOT/'Pruebas'; out.mkdir(exist_ok=True)
    rows=[]
    with tempfile.TemporaryDirectory(prefix='megacd_color_') as tmp:
        p=Path(tmp)
        gray=np.repeat(np.arange(256,dtype=np.uint8)[None,:,None],192,axis=0)
        gray=np.repeat(gray,3,axis=2)
        src=p/'grises.ppm'
        src.write_bytes(b'P6\n256 192\n255\n'+gray.tobytes())
        video=p/'grises.mkv'
        subprocess.run([s.find_ffmpeg(),'-v','error','-y','-loop','1','-i',str(src),
                        '-t','1','-c:v','ffv1',str(video)],check=True,creationflags=s.CREATE_NO_WINDOW)
        for profile in ('svideo_hdmi','standard','high_contrast'):
            for aspect in ('keep','crop','stretch'):
                for tint in ('off','auto','strong'):
                    target=p/'gris.cin'
                    # Un fotograma, escrito por el mismo codificador FILM que usa la GUI.
                    s._encode_segment_pass(str(video),0,0.125,8,aspect,4,str(target),tmp,
                                           video_eq_mode=profile,tint_fix=tint)
                    data=target.read_bytes()
                    hs=struct.unpack_from('>I',data,4)[0]
                    n=struct.unpack_from('>I',data,48)[0]
                    for i in range(n):
                        offset,length,t,d=struct.unpack_from('>IIII',data,52+16*i)
                        if t==0xFFFFFFFF:
                            continue
                        frame=data[hs+offset:hs+offset+length]
                        assert frame[:2]==b'SM'
                        count=(frame[3]&3)+1
                        words=np.frombuffer(frame[12:12+count*32],dtype='>u2')
                        rgb=np.column_stack(((words>>1)&7,(words>>5)&7,(words>>9)&7)).astype(float)
                        bias=float(np.max(np.ptp(rgb,axis=1)))
                        assert bias==0,(profile,aspect,tint,rgb)
                        rows.append({'perfil':profile,'pantalla':aspect,'antirosa':tint,
                                     'sesgo_max_niveles_VDP':bias,'media_R':rgb[:,0].mean(),
                                     'media_G':rgb[:,1].mean(),'media_B':rgb[:,2].mean()})
        with (out/'Tabla_grises.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        # Los controles antirosa alteran expresamente los colores. Medir y
        # declarar su efecto en una carta de piel, sin afirmar identidad.
        skin=np.zeros((192,256,3),dtype=np.uint8)
        skin[:]=[190,135,105]
        src.write_bytes(b'P6\n256 192\n255\n'+skin.tobytes())
        skin_rows=[]
        for profile in ('svideo_hdmi','standard','high_contrast'):
            for tint in ('off','auto','strong'):
                vf=s.build_video_filter(8,profile,'stretch',(256,192,0,0),'bt601',tint)
                raw=subprocess.run([s.find_ffmpeg(),'-v','error','-loop','1','-i',str(src),'-vf',vf,
                                    '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],
                                    stdout=subprocess.PIPE,check=True,creationflags=s.CREATE_NO_WINDOW).stdout
                image=s.protect_neutrals(np.frombuffer(raw,dtype=np.uint8).reshape(192,256,3),tint)
                skin_rows.append({'perfil':profile,'antirosa':tint,'R':image[:,:,0].mean(),
                                  'G':image[:,:,1].mean(),'B':image[:,:,2].mean()})
        with (out/'Tabla_piel.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(skin_rows[0]));w.writeheader();w.writerows(skin_rows)
    print('27 combinaciones de grises sin sesgo de canal; 9 medidas de carta de piel.')

if __name__=='__main__':main()
