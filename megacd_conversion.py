# -*- coding: utf-8 -*-
"""Planificación real de capacidad, caché reanudable y conversión paralela."""
import concurrent.futures
from functools import lru_cache
import hashlib
import importlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import time
import threading
from megacd_streaming import stream_limits, STREAM_REVISION

SECTOR=2048
CAPACITY=700*1024*1024
TARGET=CAPACITY-512*1024
CREATE_NO_WINDOW=0x08000000 if os.name=='nt' else 0
_studio=None
_events=None
_cancel=None

class ConversionCancelled(RuntimeError):pass

def protected_frame_budget(fps,frames):
    """Incluye la precarga de PCM y ambos bancos, también en clips cortos."""
    audio_chunks=max(1,math.ceil((max(0,frames-1)/fps)*16276/16384+.4))
    header=68+16*(frames+audio_chunks)
    remaining=math.floor(stream_limits(fps)['average']*frames/fps)-audio_chunks*16384-header-min(2,frames)*8000
    if frames<=2:return 0
    return min((96000 if fps>15 else 124000)//fps,remaining//(frames-2))

def run_ffmpeg_quiet(command,cancel_cb=None,timeout=120):
    """Audio cancelable y con tiempo máximo; mantiene el código de salida."""
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
    started=time.monotonic()
    try:
        while process.poll() is None:
            if cancel_cb and cancel_cb():raise ConversionCancelled('Conversión cancelada durante el audio.')
            if time.monotonic()-started>timeout:raise RuntimeError('FFmpeg no terminó el audio dentro del tiempo máximo.')
            try:process.wait(timeout=.2)
            except subprocess.TimeoutExpired:pass
        return process.returncode
    finally:
        if process.poll() is None:process.kill()
        process.wait()

class FFmpegReadGuard:
    """Desbloquea la tubería si FFmpeg deja de entregar imagen o se cancela."""
    def __init__(self,process,cancel_cb=None,timeout=60):
        self.process=process;self.cancel_cb=cancel_cb;self.timeout=timeout
        self.pending=None;self.error=None;self.stop=threading.Event()
        self.thread=threading.Thread(target=self._watch,daemon=True);self.thread.start()
    def _watch(self):
        while not self.stop.wait(.1):
            if self.cancel_cb and self.cancel_cb():self.error=ConversionCancelled('Conversión cancelada.');break
            pending=self.pending
            if pending is not None and time.monotonic()-pending>self.timeout:
                self.error=RuntimeError('FFmpeg dejó de entregar fotogramas; se conserva el trabajo terminado.');break
        if self.error is not None and self.process.poll() is None:
            try:self.process.kill()
            except OSError:pass
    def read(self,size):
        self.pending=time.monotonic()
        try:data=self.process.stdout.read(size)
        finally:self.pending=None
        if self.error:raise self.error
        return data
    def close(self):self.stop.set();self.thread.join(timeout=1)

def encode_library():
    global _studio
    if _studio is None:
        active=sys.modules.get('__main__')
        if active is not None and hasattr(active,'encode_segment_to_cin'):_studio=active
        else:
            import Mega_Cd_Grabador_de_Video_Beta as library
            _studio=library
    return _studio

def _file_key(path):
    p=Path(path).resolve();stat=p.stat()
    return str(p),stat.st_size,stat.st_mtime_ns

@lru_cache(maxsize=128)
def _profile(key,ffmpeg):
    path=key[0]
    bundled=Path(ffmpeg).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe')
    ffprobe=str(bundled) if bundled.is_file() else shutil.which('ffprobe')
    if not ffprobe:return None
    proc=subprocess.run([ffprobe,'-v','error','-show_format','-show_streams','-of','json',path],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,creationflags=CREATE_NO_WINDOW)
    if proc.returncode:return None
    data=json.loads(proc.stdout)
    streams=[x for x in data.get('streams',[]) if x.get('codec_type')=='video' and not x.get('disposition',{}).get('attached_pic')]
    if not streams:return None
    default=[x for x in streams if x.get('disposition',{}).get('default')]
    video=max(default or streams,key=lambda x:x.get('width',0)*x.get('height',0))
    duration=float(data.get('format',{}).get('duration') or video.get('duration') or 0)
    width,height=int(video['width']),int(video['height'])
    rotation=float(video.get('tags',{}).get('rotate',0))
    for side in video.get('side_data_list',[]):rotation=float(side.get('rotation',rotation))
    if round(rotation)%180:width,height=height,width
    if duration<=0:return None
    return {'duration':duration,'width':width,'height':height,'codec':video.get('codec_name'),'pix_fmt':video.get('pix_fmt'),
            'color_space':video.get('color_space'),'audio':any(x.get('codec_type')=='audio' for x in data.get('streams',[])),
            'source_bytes':key[1]}

def stream_profile(path,ffmpeg):
    try:return _profile(_file_key(path),ffmpeg)
    except (ValueError,KeyError,OSError,subprocess.SubprocessError):return None

@lru_cache(maxsize=32)
def _cuda_works(key,ffmpeg):
    if shutil.which('nvidia-smi') is None:return False
    try:
        meta=_profile(key,ffmpeg)
        # Estos códecs tienen transformadas enteras y salida de referencia estable.
        # MJPEG/formatos de archivo sin NVDEC mantienen lectura CPU y Cinepak CUDA.
        if not meta or meta['codec'] not in ('h264','hevc','vp8','vp9','av1'):return False
        download={'yuv420p':'nv12','nv12':'nv12','yuv420p10le':'p010le','p010le':'p010le',
                  'yuv444p':'yuv444p','yuv444p16le':'yuv444p16le'}.get(meta['pix_fmt'])
        if not download:return False
        test=subprocess.run([ffmpeg,'-v','error','-hwaccel','cuda','-hwaccel_output_format','cuda','-threads','2','-i',key[0],
                             '-an','-sn','-dn','-frames:v','2','-vf',f'hwdownload,format={download},scale=256:192,format=rgb24','-f','null','-'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30,creationflags=CREATE_NO_WINDOW)
        return test.returncode==0
    except (OSError,subprocess.SubprocessError):return False

def decode_options(path,ffmpeg,mode='auto'):
    if mode not in ('cpu','off','encode_only') and _cuda_works(_file_key(path),ffmpeg):return ['-hwaccel','cuda']
    return []

def _layout_fixed(studio,count,chapter_ids,settings):
    from megacd_runtime import root_directory_sectors
    names=['VIDEO.CIN' if count==1 else f'VID{i+1:03d}.CIN' for i in range(count)]
    root=root_directory_sectors(names+['ABS.TXT','BIB.TXT','CPY.TXT','IP.BIN','MAINPLAY.BIN','SP.BIN','SUBPLAY.BIN'])
    ip,main,sub=map(bytearray,(studio.IP_BIN,studio.MAINPLAY_BIN,studio.SUBPLAY_BIN))
    studio.patch_cinepak_players(ip,main,sub);studio.patch_main_controller(ip,main)
    sp=studio.build_multi_sp_bin(bytes(sub),[25]*count,loop_flag=settings.get('loop_playback',True),
                               allow_skip=settings.get('allow_skip',True),enable_osd=settings.get('enable_osd',True),chapter_ids=chapter_ids)
    if 0x2000+max(0x2000,math.ceil(len(sp)/SECTOR)*SECTOR)>0x8000:
        raise ValueError('Demasiados archivos CIN para el área de arranque; elige capítulos lógicos más largos.')
    aux=3+sum(math.ceil(len(x)/SECTOR) for x in (ip,main,sub,sp))
    return 23+2*root+aux+150

def make_capacity_plan(sources,settings,studio=None):
    studio=studio or encode_library()
    if settings.get('quality_mode')=='sharp15':
        return make_15fps_plan(sources,settings,studio)
    if settings.get('quality_mode')=='sharp24':
        return make_24fps_plan(sources,settings,studio)
    if settings.get('quality_mode')=='sharp':
        requested=int(settings.get('fps',24));failures=[]
        for candidate in (24,20,15,12,10,8):
            if candidate>requested:continue
            try:
                chosen=make_capacity_plan(sources,{**settings,'fps':candidate,'quality_mode':'protected'},studio)
            except ValueError as error:
                failures.append(str(error));continue
            chosen.update(requested_fps=requested,quality_mode='sharp')
            return chosen
        raise ValueError('No cabe en un CD preservando la nitidez ni siquiera a 8 FPS. Divide la lista en varios discos. '+(failures[-1] if failures else ''))
    fps=int(settings.get('fps',24));requested=float(settings.get('chapter_seconds',180))
    protected=settings.get('quality_mode')=='protected'
    jobs=[];logical=0
    for source_index,source in enumerate(sources):
        duration=float(source['duration'])
        if not math.isfinite(duration) or duration<=0:raise ValueError('La duración del vídeo no es válida.')
        parts=studio.chapter_plan(duration,requested,fps)
        for part in parts:
            part=dict(part)
            if settings.get('_minimum_segment_seconds') and part['duration']<settings['_minimum_segment_seconds']:
                part['padded_seconds']=settings['_minimum_segment_seconds']-part['duration']
                part['duration']=settings['_minimum_segment_seconds']
            frames=round(part['duration']*fps)
            box=settings.get('_active_boxes',{}).get(source_index) or studio.compute_active_box(source['width'],source['height'],settings.get('aspect_mode','keep'))
            border=768-(box[0]//8)*(box[1]//8)
            # Estimación superior del PCM de 7.0, incluida su cola de silencio.
            audio_chunks=math.ceil((part['duration']+.75)*16276/16384)+1
            header=68+16*(frames+audio_chunks)
            refresh=sum(1 for i in range(2,frames) if i%48 in (0,1))*border*8
            keyframes=sum(1 for i in range(2,frames) if i%48 in (0,1))
            if protected:keyframes=0
            palette_count=4 if settings.get('num_palettes',4)>=4 else 2
            quality_floor=12+palette_count*32+192+8+1024+192+(768-border)*8
            frame_budget=min(studio.default_frame_budget(fps),protected_frame_budget(fps,frames)) if protected else None
            if protected and frame_budget<quality_floor:
                raise ValueError('Estos FPS requieren reutilizar bloques cambiados para cumplir el caudal del lector.')
            regular_min=quality_floor if protected else 600
            minimum=header+audio_chunks*16384+(min(frames,2)+keyframes)*8000+max(0,frames-2-keyframes)*regular_min
            job={**part,'source_index':source_index,'logical':logical+part['logical'],'path':source['path'],'name':source.get('name',Path(source['path']).name),
                 'frames':frames,'minimum_sectors':math.ceil(minimum/SECTOR),'quality_floor':quality_floor if protected else None,'header_reserved':header,
                 'audio_reserved':audio_chunks*16384,'refresh_reserved':refresh,'keyframes':keyframes,'source_key':_file_key(source['path'])}
            if source_index in settings.get('_active_boxes',{}):job['active_box']=list(box)
            if protected:job['frame_budget']=frame_budget
            jobs.append(job)
        logical+=max((x['logical'] for x in parts),default=0)
    if not jobs:raise ValueError('Añade un vídeo antes de convertir.')
    if logical>99 and not settings.get('_multidisc_base'):raise ValueError('El menú admite 99 capítulos lógicos. Selecciona una duración de capítulo mayor.')
    ids=[j['logical'] for j in jobs]
    fixed=0 if settings.get('_multidisc_base') else _layout_fixed(studio,len(jobs),ids,settings)
    fit=bool(settings.get('fit_cd',True))
    minimum=sum(j['minimum_sectors'] for j in jobs)
    available=TARGET//SECTOR-fixed
    if fit and minimum>available:
        raise ValueError('La duración no cabe en un CD con el audio y los FPS elegidos. Reduce los FPS o divide la lista en varios discos; se ha comprobado antes de codificar.')
    frames_total=sum(j['frames'] for j in jobs)
    extra=max(0,available-minimum)
    fractions=[];assigned=0
    for i,job in enumerate(jobs):
        portion=extra*job['frames']/frames_total
        sectors=job['minimum_sectors']+int(portion)
        job['max_cin_bytes']=sectors*SECTOR if fit else None
        assigned+=sectors;fractions.append((portion-int(portion),i))
    if fit:
        for _,i in sorted(fractions,reverse=True)[:available-assigned]:jobs[i]['max_cin_bytes']+=SECTOR
    # Cota de tamaño realista: nunca se recorta artificialmente la cifra mostrada.
    estimated=[]
    for job in jobs:
        default=studio.default_frame_budget(fps)
        uncapped=job['audio_reserved']+job['header_reserved']+(min(2,job['frames'])+job['keyframes'])*8000+max(0,job['frames']-2-job['keyframes'])*default
        job['estimated_bytes']=min(uncapped,job['max_cin_bytes']) if fit else uncapped
        estimated.append(math.ceil(job['estimated_bytes']/SECTOR))
    estimate=max(300,fixed+sum(estimated))*SECTOR
    return {'jobs':jobs,'chapter_ids':ids,'logical_chapters':logical,'fps':fps,'frames':frames_total,
            'capacity_bytes':CAPACITY,'target_bytes':TARGET,'fixed_sectors':fixed,'estimated_iso_bytes':estimate,
            'duration_seconds':frames_total/fps,'fit_cd':fit,'requested_fps':fps,'quality_mode':'sharp' if protected else 'fixed'}

def make_15fps_plan(sources,settings,studio):
    """15 FPS fijos: maximizar el área sin recortar duración ni reutilizar bloques con pérdida."""
    originals=[studio.compute_active_box(x['width'],x['height'],settings.get('aspect_mode','keep')) for x in sources]
    candidates=[]
    for numerator in range(32,3,-1):
        boxes={}
        for i,(ow,oh,_,_) in enumerate(originals):
            w=max(8,(ow*numerator//32)//8*8)
            h=max(8,round(w*oh/ow/8)*8)
            if settings.get('aspect_mode','keep') in ('stretch','crop'):
                # Imagen 4:3 exacta y tiles completos.
                w=(w//32)*32;h=w*3//4
                if not w:break
            boxes[i]=(w,h,((256-w)//16)*8,((192-h)//16)*8)
        if len(boxes)==len(sources) and boxes not in candidates:candidates.append(boxes)
    for boxes in candidates:
        try:
            plan=make_capacity_plan(sources,{**settings,'fps':15,'quality_mode':'protected','fit_cd':True,'_active_boxes':boxes},studio)
        except ValueError:continue
        plan.update(quality_mode='sharp15',requested_fps=15,fps=15,disc_count=1,
                    active_boxes=[{'source_index':i,'original_box':list(originals[i]),'active_box':list(boxes[i]),'adapted':tuple(originals[i])!=boxes[i]} for i in boxes])
        return plan
    raise ValueError('La película no cabe en un CD a 15 FPS con audio y nitidez protegida. Divida la película en varios discos.')


def make_24fps_plan(sources,settings,studio):
    """24 FPS sin copia con pérdida: área sostenible y tantos CD como hagan falta."""
    boxes={};changes=[]
    palettes=4 if settings.get('num_palettes',4)>=4 else 2
    overhead=12+palettes*32+192+8+1024+192
    requested=max(2,float(settings.get('chapter_seconds',180)))
    for i,source in enumerate(sources):
        parts=studio.chapter_plan(float(source['duration']),requested,24)
        budget=min(protected_frame_budget(24,max(48,round(p['duration']*24))) for p in parts)
        tile_limit=(budget-overhead)//8
        if tile_limit<1:raise ValueError('El clip es demasiado corto para inicializar el vídeo y el audio.')
        original=studio.compute_active_box(source['width'],source['height'],settings.get('aspect_mode','keep'))
        w,h,px,py=original
        if (w//8)*(h//8)>tile_limit:
            scale=math.sqrt(tile_limit/((w//8)*(h//8)))
            w=max(8,int(w*scale)//8*8);h=max(8,int(h*scale)//8*8)
            px=((256-w)//16)*8;py=((192-h)//16)*8
        boxes[i]=(w,h,px,py)
        changes.append({'source_index':i,'original_box':list(original),'active_box':list(boxes[i]),'adapted':tuple(original)!=boxes[i]})
    plan=make_capacity_plan(sources,{**settings,'fps':24,'quality_mode':'protected','fit_cd':False,'chapter_seconds':requested,
                                   '_active_boxes':boxes,'_multidisc_base':True,'_minimum_segment_seconds':2},studio)
    for job in plan['jobs']:
        budget=job['frame_budget']
        upper=job['header_reserved']+job['audio_reserved']+min(2,job['frames'])*8000+max(0,job['frames']-2)*budget
        job['max_cin_bytes']=math.ceil(upper/SECTOR)*SECTOR
        job['estimated_bytes']=job['max_cin_bytes']
    def descriptor(indices):
        originals=[plan['jobs'][i]['logical'] for i in indices];mapping={}
        ids=[mapping.setdefault(value,len(mapping)+1) for value in originals]
        if len(mapping)>99:raise ValueError('Más de 99 capítulos en un disco.')
        fixed=_layout_fixed(studio,len(indices),ids,settings)
        size=max(300,fixed+sum(plan['jobs'][i]['max_cin_bytes']//SECTOR for i in indices))*SECTOR
        return {'job_indices':list(indices),'chapter_ids':ids,'global_chapter_ids':originals,
                'fixed_sectors':fixed,'estimated_iso_bytes':size,'budget_bytes':TARGET}
    discs=[];current=[]
    for index in range(len(plan['jobs'])):
        try:
            possible=descriptor(current+[index])
            fits=possible['estimated_iso_bytes']<=TARGET
        except ValueError:fits=False
        if not fits and current:
            discs.append(descriptor(current));current=[]
            possible=descriptor([index]);fits=possible['estimated_iso_bytes']<=TARGET
        if not fits:raise ValueError('Un capítulo técnico no cabe en un CD; revisa la duración de capítulo.')
        current.append(index)
    if current:discs.append(descriptor(current))
    for i,disc in enumerate(discs):disc['number']=i+1
    plan.update(quality_mode='sharp24',requested_fps=24,fps=24,fit_cd=True,disc_count=len(discs),
                discs=discs,active_boxes=changes,estimated_iso_bytes=sum(d['estimated_iso_bytes'] for d in discs),
                capacity_bytes=CAPACITY*len(discs),target_bytes=TARGET*len(discs))
    return plan

def _init_worker(events,cancel):
    global _events,_cancel
    _events=events;_cancel=cancel
    events.cancel_join_thread()
    studio=encode_library()
    studio.fast.initialize()

def _post(event):
    if _events is not None:
        try:_events.put_nowait(event)
        except queue.Full:pass

def _encode_job(index,job,settings,folder):
    studio=encode_library()
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    out=folder/f'VID{index+1:03d}.CIN';partial=out.with_suffix('.partial')
    record=folder/'completo.json'
    key={'version':STREAM_REVISION,'job':{k:v for k,v in job.items() if k!='source_index'},'settings':{k:v for k,v in settings.items() if k not in ('workers',)}}
    fingerprint=hashlib.sha256(json.dumps(key,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if out.is_file() and record.is_file():
        try:
            prior=json.loads(record.read_text(encoding='utf-8'))
            if prior['fingerprint']==fingerprint and prior['sha256']==hashlib.sha256(out.read_bytes()).hexdigest():
                checked=studio.inspect_cin(out,settings['fps'])
                if checked['seguro'] and (job['max_cin_bytes'] is None or out.stat().st_size<=job['max_cin_bytes']):
                    _post(('progress',index,job['frames'],job['frames'],0))
                    return {**prior,'cached':True,'path':str(out)}
        except (ValueError,KeyError,OSError):pass
    last=[0]
    def progress(cur,total,size):
        if cur-last[0]>=settings['fps'] or cur==total:
            last[0]=cur;_post(('progress',index,cur,total,size))
        if _cancel is not None and _cancel.is_set():raise ConversionCancelled('Conversión cancelada.')
    def note(message):_post(('note',index,message))
    start=time.perf_counter()
    mode=settings.get('acceleration','auto')
    used=bool(decode_options(job['path'],studio.find_ffmpeg(),mode))
    gpu_before=studio.gpu.stats()
    try:
        try:
            value=studio.encode_segment_to_cin(job['path'],job['start'],job['duration'],settings['fps'],
                settings.get('aspect_mode','keep'),settings.get('num_palettes',4),str(partial),str(folder),
                progress_cb=progress,note_cb=note,max_cin_bytes=job['max_cin_bytes'],max_frame_budget=job.get('frame_budget'),
                audio_mode=settings.get('audio_mode','cinema_boost'),vocal_clarity=settings.get('vocal_clarity',True),
                video_eq_mode=settings.get('video_eq_mode','svideo_hdmi'),tint_fix=settings.get('tint_fix','auto'),
                acceleration=mode,frame_active_box=job.get('active_box'),allow_lossy_reuse=settings.get('quality_mode') not in ('sharp','sharp24','sharp15'),cancel_cb=lambda:_cancel is not None and _cancel.is_set())
        except ConversionCancelled:raise
        except RuntimeError:
            if not used:raise
            note('La decodificación GPU no completó este capítulo; reintentando automáticamente con CPU.')
            used=False
            value=studio.encode_segment_to_cin(job['path'],job['start'],job['duration'],settings['fps'],
                settings.get('aspect_mode','keep'),settings.get('num_palettes',4),str(partial),str(folder),
                progress_cb=progress,note_cb=note,max_cin_bytes=job['max_cin_bytes'],max_frame_budget=job.get('frame_budget'),
                audio_mode=settings.get('audio_mode','cinema_boost'),vocal_clarity=settings.get('vocal_clarity',True),
                video_eq_mode=settings.get('video_eq_mode','svideo_hdmi'),tint_fix=settings.get('tint_fix','auto'),
                acceleration='encode_only',frame_active_box=job.get('active_box'),allow_lossy_reuse=settings.get('quality_mode') not in ('sharp','sharp24','sharp15'),cancel_cb=lambda:_cancel is not None and _cancel.is_set())
        checked=studio.inspect_cin(partial,settings['fps'])
        if not checked['seguro']:raise RuntimeError('El capítulo no cumple el límite del lector.')
        os.replace(partial,out)
        result={'fingerprint':fingerprint,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
                'path':str(out),'frames':value[1],'bytes':value[0],'seconds':time.perf_counter()-start,
                'gpu_decode':used,'jit':studio.fast.AVAILABLE,'checked':checked,'cached':False}
        gpu_after=studio.gpu.stats()
        result['gpu_encode']={**gpu_after,'nearest_calls':gpu_after['nearest_calls']-gpu_before['nearest_calls'],
                              'previous_calls':gpu_after['previous_calls']-gpu_before['previous_calls'],
                              'vectors':gpu_after['vectors']-gpu_before['vectors']}
        temporary=record.with_suffix('.tmp');temporary.write_text(json.dumps(result,indent=2),encoding='utf-8');os.replace(temporary,record)
        return result
    finally:
        if partial.exists():partial.unlink()

def convert_playlist(sources,out_dir,settings,progress_cb=None,note_cb=None,cancel_event=None):
    studio=encode_library();settings=dict(settings)
    plan=make_capacity_plan(sources,settings,studio)
    if not plan['fit_cd'] and plan['estimated_iso_bytes']>CAPACITY:
        raise ValueError('La estimación supera un CD sin ajuste. Activa el ajuste a 1 CD o divide la lista antes de convertir; no se inicia una conversión que acabaría fuera de capacidad.')
    settings['fps']=plan['fps']
    out_dir=Path(out_dir).resolve();out_dir.mkdir(parents=True,exist_ok=True)
    identity={'sources':[_file_key(x['path']) for x in sources],'settings':{k:v for k,v in settings.items() if k!='workers'}}
    tag=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    cache=out_dir/'.astra2_trabajo'/tag;cache.mkdir(parents=True,exist_ok=True)
    (cache/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    workers=int(settings.get('workers') or min(6,max(1,(os.cpu_count() or 2)//3)))
    workers=max(1,min(workers,len(plan['jobs']),8))
    if note_cb:note_cb(f'Plan calculado: {plan["estimated_iso_bytes"]/1048576:.1f} MiB como cota estimada, {len(plan["jobs"])} CIN, {workers} procesos. FPS solicitados: {plan["requested_fps"]}; efectivos: {plan["fps"]}. Nitidez: {plan["quality_mode"]}.')
    context=multiprocessing.get_context('spawn');events=context.Queue(maxsize=512);cancel=context.Event()
    done_frames={};results=[None]*len(plan['jobs']);started=time.perf_counter()
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers,mp_context=context,initializer=_init_worker,initargs=(events,cancel)) as pool:
            futures={pool.submit(_encode_job,i,job,settings,str(cache/f'cap_{i+1:03d}')):i for i,job in enumerate(plan['jobs'])}
            pending=set(futures)
            while pending:
                if cancel_event is not None and cancel_event.is_set():cancel.set()
                completed,pending=concurrent.futures.wait(pending,timeout=.15,return_when=concurrent.futures.FIRST_COMPLETED)
                while True:
                    try:event=events.get_nowait()
                    except queue.Empty:break
                    if event[0]=='progress':
                        _,i,cur,total,last=event;done_frames[i]=max(done_frames.get(i,0),min(cur,plan['jobs'][i]['frames']))
                        if progress_cb:progress_cb(sum(done_frames.values()),plan['frames'],i,cur,total,last)
                    elif note_cb:note_cb(event[2])
                for future in completed:
                    i=futures[future]
                    try:results[i]=future.result()
                    except BaseException:
                        cancel.set()
                        for other in pending:other.cancel()
                        raise
                    done_frames[i]=plan['jobs'][i]['frames']
                    if progress_cb:progress_cb(sum(done_frames.values()),plan['frames'],i,done_frames[i],done_frames[i],0)
            if cancel.is_set():raise ConversionCancelled('Conversión cancelada. Los capítulos terminados se conservan para reanudar.')
    finally:
        events.close();events.join_thread()
    definitions=plan.get('discs') or [{'number':1,'job_indices':list(range(len(results))),
        'chapter_ids':plan['chapter_ids'],'fixed_sectors':plan['fixed_sectors'],'budget_bytes':CAPACITY}]
    multiple=len(definitions)>1
    prepared=[]
    for definition in definitions:
        if cancel_event is not None and cancel_event.is_set():raise ConversionCancelled('Conversión cancelada antes de publicar los discos.')
        indices=definition['job_indices'];subset=[results[i] for i in indices]
        paths=[x['path'] for x in subset]
        predicted=max(300,definition['fixed_sectors']+sum(math.ceil(Path(p).stat().st_size/SECTOR) for p in paths))*SECTOR
        if predicted>min(CAPACITY,definition.get('budget_bytes',CAPACITY)):
            raise RuntimeError('Error interno de capacidad: no se publicará un ISO demasiado grande.')
        number=definition['number']
        if note_cb:note_cb(f'Comprobando disco {number}/{len(definitions)}: {predicted/1048576:.2f} MiB...')
        stage=cache/(f'disco_{number:02d}' if multiple else 'disco');stage.mkdir(exist_ok=True)
        title=settings.get('disc_title','VIDEO FMV MEGA CD PAL')
        if multiple:title=f'{title[:20]} CD {number}'
        iso,cue,readme=studio.build_megacd_disc(paths,str(stage),region=settings.get('region','E'),
            disc_title=title,loop_playback=settings.get('loop_playback',True),
            allow_skip=settings.get('allow_skip',True),enable_osd=settings.get('enable_osd',True),chapter_ids=definition['chapter_ids'])
        actual=Path(iso).stat().st_size
        if actual!=predicted:raise RuntimeError('El tamaño real del ISO no coincide con el plan.')
        extents=studio.validate_iso(iso,paths)['archivos']
        with open(iso,'rb') as image:
            for extent,result in zip(extents,subset):
                image.seek(extent['sector']*SECTOR);remaining=extent['bytes'];digest=hashlib.sha256()
                while remaining:
                    if cancel_event is not None and cancel_event.is_set():raise ConversionCancelled('Verificación cancelada; capítulos conservados.')
                    block=image.read(min(1048576,remaining))
                    if not block:raise RuntimeError('ISO truncado al comprobar sus datos.')
                    digest.update(block);remaining-=len(block)
                if digest.hexdigest()!=result['sha256']:raise RuntimeError('Datos del ISO distintos del capítulo verificado.')
        studio.write_safety_report(str(stage),paths,settings['fps'],definition['chapter_ids'])
        destination=out_dir/f'CD_{number:02d}' if multiple else out_dir
        disc={'number':number,'iso_bytes':actual,'capacity_bytes':CAPACITY,'remaining_bytes':CAPACITY-actual,
              'iso_payload_sha256_verified':True,'job_indices':indices,'chapter_ids':definition['chapter_ids'],
              'frames':sum(r['frames'] for r in subset),'fps':plan['fps'],
              'iso_path':str(destination/Path(iso).name),'cue_path':str(destination/Path(cue).name)}
        (stage/'Informe_conversion_disco.json').write_text(json.dumps(disc,indent=2),encoding='utf-8')
        prepared.append((stage,iso,cue,destination,disc))
    actual=sum(item[4]['iso_bytes'] for item in prepared)
    report={'plan':plan,'jobs':results,'discs':[item[4] for item in prepared],'disc_count':len(prepared),
            'iso_bytes':actual,'capacity_bytes':CAPACITY*len(prepared),'remaining_bytes':CAPACITY*len(prepared)-actual,
            'iso_payload_sha256_verified':True,'elapsed_seconds':time.perf_counter()-started,'workers':workers,
            'frames':sum(x['frames'] for x in results),'fps':plan['fps'],'requested_fps':plan['requested_fps'],
            'lossy_block_reuse':settings.get('quality_mode') not in ('sharp','sharp24','sharp15'),
            'gpu_jobs':sum(x['gpu_decode'] for x in results),'jit_jobs':sum(x['jit'] for x in results),
            'gpu_encode_jobs':sum(x.get('gpu_encode',{}).get('previous_calls',0)>0 or x.get('gpu_encode',{}).get('nearest_calls',0)>0 for x in results),
            'hardware':'No verificado en consola real. Se conserva el reproductor de Astra.'}
    # Todos los discos se comprueban antes de publicar el conjunto.
    for stage,iso,cue,destination,disc in prepared:
        destination.mkdir(exist_ok=True)
        for item in stage.iterdir():
            dest=destination/item.name
            if item.is_dir():
                dest.mkdir(exist_ok=True)
                for child in item.iterdir():
                    if child.is_file():shutil.copy2(child,dest/child.name)
            elif item.suffix.upper() not in ('.ISO','.CUE'):shutil.copy2(item,dest)
        os.replace(iso,disc['iso_path']);os.replace(cue,disc['cue_path'])
    report['iso_path']=report['discs'][0]['iso_path'];report['cue_path']=report['discs'][0]['cue_path']
    report['elapsed_seconds']=time.perf_counter()-started
    temporary=out_dir/'Informe_conversion_Astra_2.tmp'
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,out_dir/'Informe_conversion_Astra_2.json')
    if multiple:
        lines=[f"MEGA CD ESTUDIO ASTRA 2: {len(prepared)} discos, {plan['fps']} FPS",'Cada CUE corresponde a un CD físico independiente.',
               'Use Elegir disco para probar o grabar cada CD. El cambio de CD es manual.',
               'Se mantienen las pausas nativas entre CIN; no se promete reproducción continua entre discos.','']
        for disc in report['discs']:lines.append(f"CD {disc['number']}: {disc['cue_path']} | {disc['iso_bytes']/1048576:.2f} MiB")
        (out_dir/'LEEME_VARIOS_DISCOS.txt').write_text('\n'.join(lines),encoding='utf-8')
    return report
