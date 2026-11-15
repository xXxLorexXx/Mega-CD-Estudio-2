# -*- coding: utf-8 -*-
"""Disco de integración con pulsaciones programadas; no altera la aplicación.

Las pulsaciones se inyectan en comando 5 en el Main, DESPUÉS de leer el mando.
Esto verifica menú/Word RAM/VDP/PCM de forma reproducible, sin depender de la
duración de las teclas que envía la herramienta de automatización de Windows.
"""
from pathlib import Path
import struct
import sys
import json

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s
from make_repeat import repeat_cin

def inject_script(ip, main):
    base=len(main)
    code=bytearray()
    def emit(h):code.extend(bytes.fromhex(h))
    # D2 ya está guardado por el lector de mando.
    code.extend(struct.pack('>Hh',0x243A,4-(base+2)))  # MOVE.L Ticks(PC),D2
    ranges=[(250,265,0x8080),(2250,2265,0x8080)]  # abrir a 5 s; cerrar a 45 s PAL
    ends=[]
    for begin,end,value in ranges:
        emit('0c82'); code.extend(struct.pack('>I',begin))
        emit('65000000'); a=len(code)-2
        emit('0c82'); code.extend(struct.pack('>I',end))
        emit('64000000'); b=len(code)-2
        emit('303c');code.extend(struct.pack('>H',value))
        emit('60000000');ends.append(len(code)-2)
        struct.pack_into('>h',code,a,len(code)-a)
        struct.pack_into('>h',code,b,len(code)-b)
    done=len(code)
    for pos in ends:struct.pack_into('>h',code,pos,done-pos)
    emit('33c000a1201a4e75')
    site=main.find(bytes.fromhex('33c000a1201a'),0x13FC)
    assert site>=0
    struct.pack_into('>Hh',main,site,0x6100,base-(site+2))
    main[site+4:site+6]=bytes.fromhex('4e71')
    main.extend(code)
    ip[:]=ip[:48]+main
    assert 0x784+len(ip)<0x2000
    return {'aviso':'SOLO DISCO DE PRUEBA. START automático a 5 y 45 s desde el arranque del reproductor.',
            'offset':base,'codigo':code.hex()}

def main():
    source=Path(sys.argv[1]).resolve()
    dest=Path(sys.argv[2]).resolve()
    dest.mkdir(parents=True,exist_ok=True)
    cin=dest/'patron180.cin'
    repeat_cin(source,cin,180,24)
    original=s.patch_main_controller
    trace={}
    def patch(ip,main):
        result=original(ip,main)
        trace.update(inject_script(ip,main))
        return result
    s.patch_main_controller=patch
    result=s.build_megacd_disc([str(cin)],str(dest),disc_title='ASTRA PRUEBA MENU AUTO',loop_playback=False)
    s.write_safety_report(str(dest),[str(cin)],24,[1])
    (dest/'Mando_programado.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
    cin.unlink()
    print(result)

if __name__=='__main__':main()
