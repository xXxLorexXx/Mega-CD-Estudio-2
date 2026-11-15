"""Control de una copia privada de Kega 3.64, sin teclado ni ratón globales.

Usa los comandos de menú declarados por el propio ejecutable y sus estados GST.
No modifica los vídeos, el ISO ni la instalación de Kega del usuario.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path
import pefile
from megacd_integrity import IntegrityError,PlaybackMonitor,read_state

KEGA_SHA256='66d3205aae58fe8a038c84588a8d008a9dfe5d8f72385979019fdd1c476521a9'


def commands(exe):
    if hashlib.sha256(Path(exe).read_bytes()).hexdigest()!=KEGA_SHA256:
        raise IntegrityError('Esta comprobación necesita la copia compatible de Kega Fusion 3.64 incluida con Astra 5.')
    result={};pe=pefile.PE(str(exe))
    try:
        for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id!=9:continue
            for name in resource.directory.entries:
                for language in name.directory.entries:
                    s=language.data.struct;data=pe.get_data(s.OffsetToData,s.Size)
                    for i in range(0,len(data)-7,8):
                        flags,key,command,_=struct.unpack_from('<HHHH',data,i)
                        if flags&0x1c:continue  # no Shift/Ctrl/Alt
                        if key==8:result['fast']=command
                        if key==0x74:result['save']=command
    finally:pe.close()
    if set(result)!=set(('fast','save')):raise IntegrityError('No se encuentran los comandos necesarios de Kega.')
    return result


def make_ini(source,work,region):
    text=Path(source).read_text(encoding='utf-8-sig',errors='replace')
    keys={'E':'SCDEURBIOS','U':'SCDUSABIOS','J':'SCDJAPBIOS'}
    match=re.search(r'^'+keys[region]+r'=(.*)$',text,re.M)
    if not match or not Path(match.group(1).strip()).is_file():
        raise IntegrityError('No se encuentra la BIOS '+region+' configurada para Kega. Configure su BIOS en Opciones de comprobación.')
    values={'StateFiles':str(work),'SCDStateFiles':str(work),'SCDSaveFiles':str(work),
            'PerfectSync':'1','VSyncEnabled':'0','AllowSleeping':'0','CountryAutoDetect':'0',
            'CurrentCountry':{'E':'2','U':'1','J':'0'}[region],
            'DisableKeyShortcuts':'1'}
    # El verificador no admite pulsaciones de mando del teclado del usuario.
    for i in range(1,9):values['Player'+str(i)+'Keys']=','.join(['0']*12)
    for key,value in values.items():
        pattern=r'^'+re.escape(key)+r'=.*$'
        if re.search(pattern,text,re.M):text=re.sub(pattern,lambda m:key+'='+value,text,flags=re.M)
        else:text+='\n'+key+'='+value
    (work/'Fusion.ini').write_text(text,encoding='utf-8')


def verify_with_kega(image,exe,ini,work,cancel,progress):
    ids=commands(exe);work=Path(work).resolve();work.mkdir(parents=True,exist_ok=True)
    make_ini(ini,work,image['region']);target=work/'Fusion.exe';shutil.copy2(exe,target)
    u=C.WinDLL('user32',use_last_error=True)
    CALLBACK=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    u.EnumWindows.argtypes=[CALLBACK,W.LPARAM]
    u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    u.GetMenu.argtypes=[W.HWND];u.GetMenu.restype=W.HMENU
    u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];u.PostMessageW.restype=W.BOOL
    si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
    proc=subprocess.Popen([str(target),image['cue'],'-mcd',{'E':'-eur','U':'-usa','J':'-jap'}[image['region']]],cwd=str(work),startupinfo=si)
    monitor=PlaybackMonitor(image);started=time.monotonic();hwnd=None;fast=False;last_state=None;last_ticks=None
    boot_deadline=started+45;last_file=started;rows=0;initial_ticks=None
    try:
        while time.monotonic()-started<max(180,image['duration']*2+120):
            if Path(cancel).exists():raise IntegrityError('Comprobación cancelada.')
            if proc.poll() is not None:raise IntegrityError('Kega se ha cerrado antes de completar la película.')
            if hwnd is None:
                found=[]
                @CALLBACK
                def enum(window,unused):
                    pid=W.DWORD();u.GetWindowThreadProcessId(window,C.byref(pid))
                    if pid.value==proc.pid and u.GetMenu(window):found.append(window)
                    return True
                u.EnumWindows(enum,0)
                if found:
                    hwnd=found[0]
                    if min(x['duration'] for x in image['cins'])>=30:
                        u.PostMessageW(hwnd,0x111,ids['fast'],0);fast=True
                elif time.monotonic()>boot_deadline:raise IntegrityError('Kega no pudo iniciar la comprobación.')
                time.sleep(.1);continue
            u.PostMessageW(hwnd,0x111,ids['save'],0)
            time.sleep(.12 if not fast else .2)
            states=sorted(work.glob('*.gs?'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
            if not states:
                if time.monotonic()>boot_deadline:raise IntegrityError('Kega no pudo guardar el estado de la reproducción.')
                continue
            state=states[0];stamp=state.stat().st_mtime_ns
            if stamp==last_state:
                if time.monotonic()-last_file>10:raise IntegrityError('Kega ha dejado de responder.')
                continue
            last_state=stamp;last_file=time.monotonic()
            try:row=read_state(state.read_bytes(),image)
            except (OSError,struct.error,IntegrityError):
                # Puede coincidir una lectura con la escritura del GST; nunca aprobarla.
                continue
            if row is None:
                if time.monotonic()>boot_deadline:raise IntegrityError('El reproductor no llegó a iniciar el vídeo.')
                continue
            if initial_ticks is None:initial_ticks=row['ticks']
            fraction=monitor.update(row);rows+=1;progress(fraction,row)
            # Una única CIN se vigila sin aceleración cerca del final para no perder el EOF.
            if fast and len(image['cins'])==1 and fraction>.85:
                u.PostMessageW(hwnd,0x111,ids['fast'],0);fast=False
            if monitor.done:
                return {'iso':image['iso'],'cue':image['cue'],'sha256':image['iso_sha256'],
                        'cue_sha256':image['cue_sha256'],'passed':True,'engine':'Kega Fusion 3.64',
                        'cins':len(image['cins']),'seen':sorted(monitor.seen),'frames':sum(x['frames'] for x in image['cins']),
                        'seconds':time.monotonic()-started,'observations':rows,
                        'iso_stat':[Path(image['iso']).stat().st_size,Path(image['iso']).stat().st_mtime_ns]}
        raise IntegrityError('La reproducción no llegó al final dentro del tiempo previsto.')
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
