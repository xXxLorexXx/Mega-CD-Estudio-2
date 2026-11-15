# -*- coding: utf-8 -*-
"""Parches auditables y validación FILM/ISO de Mega CD Estudio Astra."""
import hashlib
import json
import math
import struct
from pathlib import Path
from megacd_streaming import stream_limits


def patch_main_controller(ip, main):
    """Hook anterior al aviso de VBlank al Sub; secuencia TH de ocho fases.

    Se conservan D0-D2 y A0. El handler original conserva el resto y el SR
    mediante su MOVEM/RTE. La lectura de botones procede exclusivamente de
    las dos primeras fases; las fases de identificación/XYZ jamás se mezclan.
    El mando 1 se repite en los dos bytes del comando 5.
    """
    if hashlib.sha256(bytes(main)).hexdigest() != '87b3ed1b91185446806bae093a1519462b637bf3d5b9a491dc1e3ed2603bb4fe':
        # Los parches A/V anteriores ya han modificado MAINPLAY: comprobar
        # los puntos de anclaje y la longitud, sin aceptar otra revisión.
        if len(main) != 5116:
            raise ValueError('Revisión de MAINPLAY no compatible con el parche del mando.')
    if bytes(ip[48:]) != bytes(main):
        raise ValueError('IP y MAINPLAY no contienen el mismo reproductor.')
    if main[0xA0:0xA4] != bytes.fromhex('41fa0056') or main[0x1B8:0x1BE] != bytes.fromhex('207c00a12000'):
        raise ValueError('Anclajes del mando distintos de la revisión analizada.')
    original = bytes(main)
    code = bytearray()
    base = len(main)
    def emit(h):
        code.extend(bytes.fromhex(h))
    def pc(op, target):
        pos = base + len(code)
        code.extend(struct.pack('>Hh', op, target - (pos + 2)))
    init = base
    emit('13fc004000a10009')  # control: TH salida, una sola vez
    emit('13fc004000a10003')  # TH alto en reposo
    emit('427900a1201a')      # ningún botón al arrancar
    pc(0x41FA, 0xF8)         # LEA original sustituido por el hook
    emit('4e75')
    read = base + len(code)
    emit('48e7e080')         # MOVEM.L D0-D2/A0,-(SP)
    emit('207c00a10003')
    emit('42104e714e711010') # TH=0, dos NOP, D0.B=Start/A
    emit('02' '400030' 'e548')  # ANDI.W #$30,D0; LSL.W #2,D0
    emit('10bc00404e714e711210') # TH=1; D1.B=C/B/R/L/D/U
    emit('0241003f8041')      # enmascarar bits y reunir
    # Completar las otras seis fases de SGDK. Ningún dato de XYZ/Mode
    # se utiliza como dirección o botón clásico.
    for _ in range(3):
        emit('42104e714e711410')       # TH=0; lectura ID
        emit('10bc00404e714e711410')   # TH=1; lectura extendida
    emit('0a4000ff024000ff')   # activo alto; 8 bits
    emit('3200e1498041')       # repetir en byte alto
    emit('33c000a1201a')       # comando 5
    mirror_fixup = len(code)
    pc(0x41FA, 0)             # diagnóstico persistente, resuelto debajo
    emit('8150')              # OR.W D0,(A0): botones vistos desde el arranque
    emit('4cdf0107')           # restaurar D0-D2/A0
    emit('207c00a120004e75')   # instrucción original; RTS
    mirror = base + len(code)
    emit('0000')
    struct.pack_into('>h',code,mirror_fixup+2,mirror-(base+mirror_fixup+2))
    main.extend(code)
    struct.pack_into('>Hh', main, 0xA0, 0x6100, init - 0xA2)
    struct.pack_into('>Hh', main, 0x1B8, 0x6100, read - 0x1BA)
    main[0x1BC:0x1BE] = bytes.fromhex('4e71')
    ip[:] = ip[:48] + main
    if 0x784 + len(ip) > 0x2000:
        raise ValueError('El IP ampliado solapa el SP en el área de arranque.')
    return {'init': init, 'read': read, 'pad_ever': mirror, 'extra_bytes': len(code),
            'hooks': [{'offset': a, 'antes': original[a:b].hex(), 'despues': bytes(main[a:b]).hex()}
                      for a, b in [(0xA0, 0xA4), (0x1B8, 0x1BE)]],
            'rutina_hex': code.hex()}


def chapter_plan(duration, requested, fps, limit=5400):
    # Menos STAB deja más heap disponible al búfer de lectura del player.
    # Se conservan capítulos lógicos, duración total y todos los fotogramas.
    if fps > 15:
        limit = min(limit, 3200)
    """Capítulos lógicos exactos; trozos técnicos alineados a fotogramas."""
    frames = int(round(duration * fps))
    logical_frames = max(1, int(round(requested * fps)))
    # Margen de dos entradas de audio y un fotograma por redondeos.
    safe_frames = int((limit - 3) / (1 + 16276 / 16384 / fps))
    result = []
    for start in range(0, frames, logical_frames):
        end = min(frames, start + logical_frames)
        count = math.ceil((end - start) / safe_frames)
        for part in range(count):
            a = start + (end - start) * part // count
            b = start + (end - start) * (part + 1) // count
            result.append({'start': a / fps, 'duration': (b - a) / fps,
                           'logical': start // logical_frames + 1,
                           'part': part + 1, 'parts': count})
    return result


def inspect_cin(path, fps=None):
    """Valida muestras y mide caudal del archivo real, incluida cabecera."""
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as f:
        header = f.read(52)
        if len(header) != 52 or header[:4] != b'FILM' or header[36:40] != b'STAB':
            raise ValueError('Cabecera FILM/STAB no válida.')
        header_size = struct.unpack_from('>I', header, 4)[0]
        timescale, count = struct.unpack_from('>II', header, 44)
        if timescale == 0 or count > 5400 or header_size != 68 + 16 * count:
            raise ValueError('Tabla STAB fuera del límite seguro.')
        entries = [struct.unpack('>IIII', f.read(16)) for _ in range(count)]
        if f.read(16) != bytes(16):
            raise ValueError('Falta el terminador STAB.')
    prev = 0
    video = []
    audio = 0
    timed = []
    audio_time = 0.0
    for offset, length, timestamp, ticks in entries:
        if offset != prev or length == 0 or length % 2 or header_size + offset + length > size:
            raise ValueError('Muestra inválida o datos discontinuos en FILM.')
        prev = offset + length
        if timestamp == 0xFFFFFFFF:
            audio += length
            # El codificador adelanta el PCM un 40% de bloque. Contarlo en su
            # fecha de lectura, no en la de escucha, evita ocultar ráfagas.
            timed.append((max(0.0, audio_time - (0.4 * 16384 / 16276 if fps and fps > 15 else 0)), length))
            audio_time += length / 16276
        else:
            start = timestamp / timescale * 16000 / 16276
            end = (timestamp + ticks) / timescale * 16000 / 16276
            video.append((start, end))
            timed.append((start, length))
    if not video or header_size + prev != size:
        raise ValueError('FILM sin imagen o con tamaño incoherente.')
    duration = len(video) / fps if fps else video[-1][1]
    window = min(6.0, duration)
    events = sorted(timed)
    left = 0
    total = peak = 0
    for right, (t, length) in enumerate(events):
        total += length
        while left <= right and events[left][0] <= t - window:
            total -= events[left][1]
            left += 1
        peak = max(peak, total)
    avg = size / duration
    peak_bps = max(avg, peak / window)
    effective_fps = fps or len(video) / max(duration, 0.001)
    limits = stream_limits(effective_fps)
    left = 0
    total = peak_one = 0
    one_window = min(1.0, duration)
    for t, length in events:
        total += length
        while left < len(events) and events[left][0] <= t - one_window:
            total -= events[left][1]
            left += 1
        peak_one = max(peak_one, total)
    peak_one_bps = max(avg, peak_one / one_window)
    return {'archivo': path.name, 'bytes': size, 'fotogramas': len(video),
            'entradas_stab': count, 'cabecera_bytes': header_size,
            'duracion_s': duration, 'audio_s': audio / 16276,
            'media_B_s': avg, 'pico_6s_B_s': peak_bps,
            'pico_1s_B_s': peak_one_bps, 'perfil_lectura': limits['label'],
            'limite_media_B_s': limits['average'], 'limite_pico_6s_B_s': limits['peak_6s'],
            'limite_pico_1s_B_s': limits['peak_1s'], 'limite_lector_B_s': 153600,
            'seguro': avg <= limits['average'] and peak_bps <= limits['peak_6s']
                      and (limits['peak_1s'] is None or peak_one_bps <= limits['peak_1s'])}


def write_safety_report(out_dir, cin_paths, fps=None, chapters=None):
    rows = [inspect_cin(p, fps) for p in cin_paths]
    result = {'capitulos': rows, 'mapa_capitulos_logicos': chapters,
              'hardware': 'No verificado en consola real. Las transiciones entre CIN pueden tener pausa.'}
    dest = Path(out_dir)
    (dest / 'Informe_de_seguridad.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['INFORME DE SEGURIDAD — MEGA CD ESTUDIO ASTRA',
             'Lector 1x: 153600 B/s = 150 KiB/s. A >15 FPS: media 120000, pico 6 s 126000, pico 1 s 144000 B/s.',
             'Hasta 15 FPS se conserva el perfil anterior: media 142000 y pico 6 s 150000 B/s.',
             result['hardware'], '']
    for i, row in enumerate(rows):
        group = chapters[i] if chapters else i + 1
        lines.append(f"{row['archivo']} | Cap. lógico {group} | {row['duracion_s']:.3f} s | "
                     f"STAB {row['entradas_stab']} | media {row['media_B_s']:.0f} B/s | "
                     f"pico {row['pico_6s_B_s']:.0f} B/s | "
                     + ('CERCA DEL LÍMITE' if row['pico_6s_B_s'] > 142000 else 'DENTRO DEL LÍMITE'))
    (dest / 'Informe_de_seguridad.txt').write_text('\n'.join(lines), encoding='utf-8')
    return rows


def root_directory_sectors(names):
    def needed(joliet):
        pos = 68  # registros . y ..
        for name in sorted(names):
            n = len(name.encode('utf-16-be')) if joliet else len((name+';1').encode('ascii'))
            length = 33+n+(1 if n % 2 == 0 else 0)
            if pos % 2048 + length > 2048:
                pos = (pos+2047)//2048*2048
            pos += length
        return (pos+2047)//2048
    return max(needed(False),needed(True))


def validate_iso(path, expected_cins):
    """Inspección independiente del directorio ISO y de las extensiones CIN."""
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as f:
        f.seek(16*2048)
        pvd=f.read(2048)
        if pvd[:7] != b'\x01CD001\x01':
            raise ValueError('ISO sin descriptor primario válido.')
        root=struct.unpack_from('<I',pvd,158)[0]
        root_size=struct.unpack_from('<I',pvd,166)[0]
        f.seek(root*2048)
        directory=f.read(root_size)
        records={}
        pos=0
        while pos < len(directory):
            n=directory[pos]
            if not n:
                pos=(pos//2048+1)*2048
                continue
            rec=directory[pos:pos+n]
            sector=struct.unpack_from('<I',rec,2)[0]
            length=struct.unpack_from('<I',rec,10)[0]
            if sector != struct.unpack_from('>I',rec,6)[0] or length != struct.unpack_from('>I',rec,14)[0]:
                raise ValueError('Extensión ISO con endian inconsistente.')
            name=rec[33:33+rec[32]].decode('ascii')
            records[name]=(sector,length)
            pos+=n
        cins=[]
        for i, source in enumerate(expected_cins):
            name='VIDEO.CIN' if len(expected_cins)==1 else f'VID{i+1:03d}.CIN'
            if name+';1' not in records:
                raise ValueError('CIN ausente del directorio ISO: '+name)
            sector,length=records[name+';1']
            if length != Path(source).stat().st_size or sector*2048+length > size:
                raise ValueError('Tamaño o ubicación CIN no válido: '+name)
            if cins and sector != cins[-1]['sector_fin_exclusivo']:
                raise ValueError('Los CIN no son contiguos y ordenados.')
            f.seek(sector*2048)
            if f.read(4) != b'FILM':
                raise ValueError('La extensión CIN no apunta a un FILM.')
            cins.append({'archivo':name,'sector':sector,'bytes':length,
                         'sector_fin_exclusivo':sector+(length+2047)//2048})
    report={'iso':path.name,'sectores':size//2048,'cin_contiguos':True,'archivos':cins}
    (path.parent/'Verificacion_ISO.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
