"""InspecciÃ³n de imÃ¡genes propias y vigilancia de reproducciÃ³n completa en Kega."""
import hashlib
import json
import re
import statistics
import struct
import time
import zlib
from pathlib import Path


class IntegrityError(RuntimeError):
    pass


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return h.hexdigest()


def resolve_image(path):
    path=Path(path).resolve()
    if path.suffix.lower()=='.cue':
        text=path.read_text(encoding='utf-8-sig',errors='replace')
        names=re.findall(r'^\s*FILE\s+"([^"]+)"\s+BINARY\s*$',text,re.I|re.M)
        if len(names)!=1 or not re.search(r'TRACK\s+01\s+MODE1/2048',text,re.I):
            raise IntegrityError('Se necesita un CUE de una pista MODE1/2048 creado por Mega CD Estudio.')
        iso=(path.parent/names[0]).resolve()
        if iso.parent!=path.parent:raise IntegrityError('El ISO debe estar junto al CUE.')
        cue=path
    else:
        iso=path;cue=next((x for x in (path.with_suffix('.CUE'),path.with_suffix('.cue')) if x.is_file()),None)
    if not iso.is_file() or cue is None:raise IntegrityError('Falta el ISO o su CUE.')
    return iso,cue


def is_stream_disc(path):
    """Read the volume identifier without depending on the adjacent manifest."""
    try:
        iso, _ = resolve_image(path)
        with iso.open("rb") as stream:
            stream.seek(16 * 2048 + 40)
            return stream.read(32).strip() in (b"SCFMV_DLT", b"CAVC_MULTIPART")
    except (OSError, IntegrityError):
        return False


def inspect_stream_disc(path, info, cancel=lambda: False):
    iso, cue = resolve_image(path)
    if resolve_image(cue)[0] != iso:
        raise IntegrityError("El CUE apunta a otra imagen.")
    size = iso.stat().st_size
    if size % 2048 or not 18 * 2048 <= size <= 360_000 * 2048:
        raise IntegrityError("Tamaño o número de sectores del disco incorrecto.")
    with iso.open("rb") as stream:
        if stream.read(14) != b"SEGADISCSYSTEM":
            raise IntegrityError("Falta la cabecera de arranque Mega CD.")
        stream.seek(16 * 2048)
        pvd = stream.read(2048)
        if pvd[:7] != b"\x01CD001\x01":
            raise IntegrityError("Falta el descriptor principal ISO 9660.")
        lba, length = struct.unpack_from("<I", pvd, 158)[0], struct.unpack_from("<I", pvd, 166)[0]
        if not 0 < length <= 65536 or lba * 2048 + length > size:
            raise IntegrityError("El directorio ISO está fuera de la imagen.")
        stream.seek(lba * 2048)
        directory = stream.read(length)
        files = {}
        offset = 0
        while offset < length:
            count = directory[offset]
            if not count:
                offset = (offset // 2048 + 1) * 2048
                continue
            rec = directory[offset:offset+count]
            if count < 34 or len(rec) != count or 33 + rec[32] > count:
                raise IntegrityError("Entrada ISO incompleta.")
            if not rec[25] & 2:
                name = rec[33:33+rec[32]].decode("ascii").split(";")[0]
                sector, nbytes = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
                if name in files or sector * 2048 + nbytes > size:
                    raise IntegrityError("Entrada ISO duplicada o fuera de la imagen.")
                files[name] = (sector, nbytes)
            offset += count
        if "HEADER.DAT" in files:
            pairs = [("HEADER.DAT", "BODY.DAT")]
        else:
            headers = sorted(name for name in files if re.fullmatch(r"P[0-9]{4}HDR.DAT", name))
            pairs = [(name, name[:5] + "BOD.DAT") for name in headers]
            expected = [f"P{i:04d}HDR.DAT" for i in range(1, len(headers) + 1)]
            if headers != expected or not {"PARTIP.BIN", "PARTSP.BIN"} <= files.keys():
                raise IntegrityError("Faltan partes o módulos del reproductor.")
        if not pairs or any(b not in files for _, b in pairs):
            raise IntegrityError("Faltan datos de vídeo.")
        frame_count = 0
        for header, body in pairs:
            sector, nbytes = files[header]
            body_bytes = files[body][1]
            if nbytes < 2048 or nbytes % 2048 or body_bytes < 2048 or body_bytes % 2048:
                raise IntegrityError(f"{header}: datos sin alinear o incompletos.")
            stream.seek(sector * 2048)
            first = stream.read(2048)
            if first[:4] != b"CAVC" or zlib.crc32(first[4:62]) != int.from_bytes(first[192:196], "big"):
                raise IntegrityError(f"{header}: la firma del reproductor no coincide.")
            frames = int.from_bytes(first[4:6], "big") | first[37] << 16
            if not 1 <= frames <= 83520:
                raise IntegrityError(f"{header}: número de fotogramas no válido.")
            frame_count += frames
        digest = hashlib.sha256()
        stream.seek(0)
        for block in iter(lambda: stream.read(1024**2), b""):
            if cancel():
                raise IntegrityError("Verificación cancelada.")
            digest.update(block)
        iso_hash = digest.hexdigest()
    cue_hash = sha256(cue)
    receipt = bool(info.get("iso_sha256") and info.get("cue_sha256"))
    if receipt and (iso_hash != info["iso_sha256"] or cue_hash != info["cue_sha256"]):
        raise IntegrityError("El ISO o su CUE han cambiado desde la conversión.")
    stat = iso.stat()
    return {"iso": str(iso), "cue": str(cue), "sha256": iso_hash,
            "cue_sha256": cue_hash, "iso_stat": [stat.st_size, stat.st_mtime_ns],
            "frames": frame_count, "parts": len(pairs), "receipt": receipt}


def project_images(path):
    iso,cue=resolve_image(path)
    parent=iso.parent.parent
    report=parent/'Informe_conversion_Astra_2.json'
    if iso.parent.name.startswith('CD_') and report.is_file():
        data=json.loads(report.read_text(encoding='utf-8-sig'))
        images=[]
        for d in data.get('discs',[]):
            other=parent/f"CD_{int(d['number']):02d}"/cue.name
            if not other.is_file():raise IntegrityError('Falta uno de los discos de la pelÃ­cula.')
            images.append(str(other))
        if images and str(cue) in [str(Path(x).resolve()) for x in images]:return images
    return [str(cue)]


def inspect_image(path):
    iso,cue=resolve_image(path)
    size=iso.stat().st_size
    if size%2048 or size>700*1024*1024:raise IntegrityError('TamaÃ±o de imagen no vÃ¡lido para un CD.')
    files={}
    with iso.open('rb') as f:
        head=f.read(2048)
        f.seek(16*2048);pvd=f.read(2048)
        if pvd[1:6]!=b'CD001':raise IntegrityError('No se encuentra el sistema de archivos del disco.')
        sector,length=struct.unpack_from('<I',pvd,158)[0],struct.unpack_from('<I',pvd,166)[0]
        if length>65536:raise IntegrityError('Directorio de disco demasiado grande.')
        f.seek(sector*2048);directory=f.read(length);pos=0
        while pos<len(directory):
            n=directory[pos]
            if not n:pos=(pos//2048+1)*2048;continue
            record=directory[pos:pos+n]
            if len(record)<34:raise IntegrityError('Directorio truncado.')
            name=record[33:33+record[32]].decode('ascii',errors='replace').split(';')[0]
            if not record[25]&2:
                start,nbytes=struct.unpack_from('<I',record,2)[0],struct.unpack_from('<I',record,10)[0]
                if start*2048+nbytes>size:raise IntegrityError('Archivo fuera de la imagen.')
                files[name]=(start,nbytes)
            pos+=n
        if 'SP.BIN' not in files:raise IntegrityError('No se encuentra el reproductor del disco.')
        start,nbytes=files['SP.BIN'];f.seek(start*2048);sp=f.read(nbytes)
        f.seek(0x2000)
        if f.read(len(sp))!=sp:raise IntegrityError('El reproductor de arranque no coincide con SP.BIN.')
        native=sp.find(bytes.fromhex('0001000000060000'))-8
        cins=[]
        for name,(start,nbytes) in sorted(files.items()):
            if not name.endswith('.CIN'):continue
            f.seek(start*2048);h=f.read(52)
            if len(h)!=52 or h[:4]!=b'FILM' or h[36:40]!=b'STAB':raise IntegrityError(f'{name}: cabecera de vÃ­deo incorrecta.')
            hs,scale,count=struct.unpack_from('>I',h,4)[0],struct.unpack_from('>I',h,44)[0],struct.unpack_from('>I',h,48)[0]
            if not 0<count<=5400 or hs!=68+16*count or scale!=600:raise IntegrityError(f'{name}: tabla de vÃ­deo no vÃ¡lida.')
            entries=[struct.unpack('>IIII',f.read(16)) for _ in range(count)]
            if f.read(16)!=bytes(16):raise IntegrityError(f'{name}: tabla truncada.')
            frames=0;times=[];previous=0
            for offset,n,t,d in entries:
                if offset!=previous or n<1 or hs+offset+n>nbytes:raise IntegrityError(f'{name}: muestras incompletas o discontinuas.')
                previous=offset+n
                if t!=0xffffffff:
                    f.seek(start*2048+hs+offset);sample=f.read(12)
                    if sample[:2]!=b'SM' or sample[8:12]!=bytes.fromhex('00200018'):raise IntegrityError(f'{name}: fotograma no vÃ¡lido.')
                    frames+=1;times.append(t)
            if hs+previous!=nbytes or frames<1:raise IntegrityError(f'{name}: falta parte del vÃ­deo.')
            step=statistics.median(b-a for a,b in zip(times,times[1:])) if frames>1 else entries[0][3]
            fps=min((8,10,12,15,20,24,30),key=lambda x:abs(x-600*16276/16000/max(1,step)))
            cins.append({'name':name,'sector':start,'bytes':nbytes,'entries':count,'frames':frames,'duration':frames/fps,'fps':fps})
        if not cins or native<0 or native+0x84>len(sp):raise IntegrityError('Reproductor no compatible con la comprobaciÃ³n automÃ¡tica.')
        cur=native-8-4*len(cins)
        if cur<0 or list(struct.unpack_from('>'+str(len(cins))+'I',sp,cur+4))!=[x['sector'] for x in cins]:
            raise IntegrityError('La lista de reproducciÃ³n no coincide con los vÃ­deos del ISO.')
        if sp[cur:cur+2]!=bytes(2) or int.from_bytes(sp[cur+2:cur+4],'big') not in (0,1):raise IntegrityError('Estado inicial del reproductor incorrecto.')
        manifest=iso.parent/'Informe_conversion_Astra_2.json'
        if not manifest.is_file():manifest=iso.parent.parent/'Informe_conversion_Astra_2.json'
        if manifest.is_file():
            report=json.loads(manifest.read_text(encoding='utf-8-sig'))
            number=int(iso.parent.name[3:]) if re.fullmatch(r'CD_\d+',iso.parent.name) else 1
            definition=next((d for d in report.get('discs',[]) if d['number']==number),None)
            if not definition or len(definition['job_indices'])!=len(cins):raise IntegrityError('El informe no corresponde a los vídeos de este CD.')
            for cin,index in zip(cins,definition['job_indices']):
                expected=report['jobs'][index]
                if expected['frames']!=cin['frames']:raise IntegrityError('Faltan fotogramas respecto a la conversión original.')
                f.seek(cin['sector']*2048);remaining=cin['bytes'];digest=hashlib.sha256()
                while remaining:
                    block=f.read(min(2**20,remaining))
                    if not block:raise IntegrityError('ISO truncado durante la comprobación.')
                    digest.update(block);remaining-=len(block)
                if digest.hexdigest()!=expected['sha256']:raise IntegrityError(cin['name']+': los datos han cambiado respecto a la conversión original.')
    return {'iso':str(iso),'cue':str(cue),'iso_sha256':sha256(iso),'cue_sha256':sha256(cue),
            'sp':sp,'native':native,'cur':cur,'loop':bool(int.from_bytes(sp[cur+2:cur+4],'big')),
            'cins':cins,'duration':sum(x['duration'] for x in cins),
            'region':next((x for x in ('E','U','J') if x.encode() in head[0x1f0:0x200]),'E')}


def read_state(data,image):
    if not data.startswith(b'GST\0') or len(data)<500000:raise IntegrityError('Kega no ha guardado un estado completo.')
    sp=image['sp'];p=data.find(sp[:80],0x228c2,0xa28c2)
    if p<0:return None
    base=p+image['native']
    u32=lambda o:struct.unpack_from('>I',data,base+o)[0]
    if not u32(0x54):return None
    return {'cur':int.from_bytes(data[p+image['cur']:p+image['cur']+2],'big')//4,
            'index':u32(0x80),'ticks':u32(4),'cdc_error':u32(0x38),
            'read_failures':int.from_bytes(data[p+image['cur']-16*len(image['cins'])-8:p+image['cur']-16*len(image['cins'])-6],'big'),
            'audio':u32(0x70)}


class PlaybackMonitor:
    def __init__(self,image):
        self.image=image;self.seen=set();self.last=None;self.stalled_ticks=0;self.wall_progress=time.monotonic();self.done=False
    def update(self,row):
        n=len(self.image['cins']);cur=row['cur'];index=row['index']
        if row['cdc_error'] or row['read_failures']:raise IntegrityError(f'Error de lectura durante el capÃ­tulo interno {min(cur+1,n)}.')
        if cur>n:raise IntegrityError('El reproductor saliÃ³ de la lista de vÃ­deos.')
        if cur<n and index!=0xffffffff:
            if index>=self.image['cins'][cur]['entries']:raise IntegrityError('El reproductor saliÃ³ de la tabla del vÃ­deo.')
            self.seen.add(cur)
        if self.last:
            delta=(row['ticks']-self.last['ticks'])&0xffffffff
            same=(cur,index)==(self.last['cur'],self.last['index'])
            self.stalled_ticks=self.stalled_ticks+delta if same else 0
            if not same:self.wall_progress=time.monotonic()
            if self.stalled_ticks>=(50 if self.image['region']=='E' else 60)*25:
                raise IntegrityError(f'La reproducciÃ³n se ha detenido en el capÃ­tulo interno {min(cur+1,n)}.')
            end=(not self.image['loop'] and cur==n and index==0xffffffff)
            wrapped=(self.image['loop'] and self.last['cur']==n-1 and cur==0 and (n>1 or index<self.last['index']))
            if (end or wrapped) and self.seen==set(range(n)):self.done=True
            if cur!=self.last['cur'] and not (cur==self.last['cur']+1 or wrapped or end):raise IntegrityError('Se ha saltado un tramo sin comprobarlo.')
        self.last=row
        if time.monotonic()-self.wall_progress>20:raise IntegrityError('El emulador ha dejado de avanzar.')
        fraction=(sum(x['duration'] for x in self.image['cins'][:min(cur,n)])+
                  (self.image['cins'][cur]['duration']*index/self.image['cins'][cur]['entries'] if cur<n and index!=0xffffffff else 0))/self.image['duration']
        return min(1,max(0,fraction))


def worker_main(config_path):
    from megacd_kega import verify_with_kega
    config=json.loads(Path(config_path).read_text(encoding='utf-8'));status=Path(config['status']);cancel=Path(config['cancel'])
    results=[]
    def emit(data):
        tmp=status.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        for attempt in range(100):
            try:tmp.replace(status);return
            except PermissionError:
                if attempt==99:raise
                time.sleep(.02)
    try:
        for i,path in enumerate(config['images']):
            if cancel.exists():raise IntegrityError('ComprobaciÃ³n cancelada.')
            emit({'state':'running','progress':i/len(config['images']),'message':f'Comprobando estructura del CD {i+1}...'})
            image=inspect_image(path)
            def progress(fraction,row):
                emit({'state':'running','progress':(i+fraction)/len(config['images']),
                      'message':f"Comprobando CD {i+1}/{len(config['images'])}, tramo {min(row['cur']+1,len(image['cins']))}/{len(image['cins'])}..."})
            result=verify_with_kega(image,config['kega'],config['ini'],Path(config['work'])/f'cd_{i+1}',cancel,progress)
            if sha256(image['iso'])!=image['iso_sha256'] or sha256(image['cue'])!=image['cue_sha256']:
                raise IntegrityError('La imagen ha cambiado durante la prueba. Vuelva a comprobarla.')
            results.append(result)
        emit({'state':'passed','progress':1,'message':'No se detectaron bloqueos en la reproducciÃ³n completa. Puede grabar.','results':results})
    except BaseException as exc:
        emit({'state':'cancelled' if cancel.exists() else 'failed','progress':0,'message':str(exc),'results':results})
        raise SystemExit(1)
