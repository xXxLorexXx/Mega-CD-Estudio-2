#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEGA CD GRABADOR DE VIDEO (BETA) - basado en SEGA MEGA CD VIDEO STUDIO 7.0
===========================================================================
Conversor profesional de vídeo FMV (Cinepak for Sega FILM/SM), optimizador de audio
de alta potencia sin siseo y grabador directo de CD con ImgBurn Portable integrado
para consolas SEGA Mega CD PAL (Europa), Sega CD NTSC-U (EE.UU.) y Mega CD NTSC-J (Japón).

Soporta:
  - Cualquier formato de vídeo de entrada (MP4, MKV, AVI, MOV, WEBM, WMV, etc.)
  - Un único vídeo o múltiples vídeos en lista de reproducción (Playlist)
  - Vídeos de larga duración con selector de duración de capítulo (1, 2, 3 o 5 min)
  - Ajuste automático a la capacidad máxima de 1 CD (700 MB grabables)
  - Codificación VDP Multi-Paleta (4 paletas = hasta 60 colores simultáneos por fotograma)
    y perfil de luminancia/contraste optimizado para cable S-Video y conversores HDMI
  - Audio PCM sign-magnitude de 8 bits a 16.276 Hz con el chip Ricoh RF5C164 al 100% ($FF),
    procesado en 16 bits con Normalizador Dinámico de Diálogos, Claridad Vocal Mega CD (2.6 kHz),
    limitador suave tanh y puerta de silencio digital (cero siseo de fondo)
  - Menú Multimedia en pantalla al pulsar START (Play, Pausa, Capítulo Anterior/Siguiente,
    Rebobinado, Avance Rápido y Stop + indicador en vivo CAP XX/YY)
  - Grabación directa de CD autoarrancable con ImgBurn Portable integrado en el propio .exe
"""

import os
import sys
import glob

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
import re
import time
import math
import wave
import struct
import shutil
import threading
import subprocess
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox
from megacd_studio_ui import BG, StudioLayout
from megacd_cavc import read_cavc_info
from megacd_cavc_ui import CavcStudio
# Evitar que cada proceso abra 20 hilos BLAS para matrices pequeñas.
for _thread_env in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_thread_env] = "1"
import numpy as np
import megacd_fast as fast
import megacd_gpu as gpu
from functools import lru_cache
from pathlib import Path
from megacd_conversion import decode_options, stream_profile, encode_library, make_capacity_plan, convert_playlist, ConversionCancelled, run_ffmpeg_quiet, FFmpegReadGuard, protected_frame_budget
from megacd_streaming import stream_limits
from megacd_clock import patch_audio_clock
from megacd_burn import confirm_imgburn_verification, verify_iso_against_device
from megacd_runtime import patch_main_controller, chapter_plan, inspect_cin, write_safety_report, root_directory_sectors, validate_iso

from megacd_assets import (
    IP_BIN, MAINPLAY_BIN, SUBPLAY_BIN,
    HEAD_EU, HEAD_US, HEAD_JP, MAINFONT_BIN
)

# Constantes de hardware Mega CD
PCM_RATE_HW = 16276
PCM_RATE_SUBPLAY = 16000
BASE_RATE = 600
PCM_CHUNK_SIZE = 0x4000
FRAME_WIDTH = 256
FRAME_HEIGHT = 192
MAX_SEGMENT_SEC = 180.0  # Máximo 3 minutos por archivo .CIN para garantizar holgura en el heap del Sub-CPU
CD_CAPACITY_MB = 700.0

# --- v7.1: límites de seguridad para el lector 1x del Mega CD ---------------------------------
CD_1X_BPS = 153600               # 75 sectores/s x 2.048 bytes: lo máximo que entrega el lector del Mega CD
VIDEO_BPS_BUDGET = 128000        # caudal de vídeo sostenido (el mismo que ya funcionaba a <=16 FPS)
SAFE_TOTAL_BPS = 142000          # media máxima verificada por capítulo (vídeo + audio PCM)
SAFE_WINDOW_BPS = 150000         # máximo admitido en cualquier ventana de 6 s
MAX_STAB_ENTRIES = 5400          # entradas máximas de la tabla STAB por .CIN (~86 KB de cabecera)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Matriz Bayer 4x4 para dithering en espacio 9-bit VDP
BAYER4X4 = np.array([
    [ 0,  8,  2, 10],
    [12,  4, 14,  6],
    [ 3, 11,  1,  9],
    [15,  7, 13,  5]
], dtype=np.int16) * 2 - 15


def find_ffmpeg():
    """Localiza el ejecutable ffmpeg en el sistema o junto al .exe."""
    exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    exe_dir = getattr(sys, "_MEIPASS", exe_dir)
    local_ff = os.path.join(exe_dir, "ffmpeg.exe")
    if os.path.isfile(local_ff):
        return local_ff
    path_ff = shutil.which("ffmpeg")
    if path_ff:
        return path_ff
    # Buscar en rutas habituales de WinGet / Program Files
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    candidates = [
        os.path.join(user_profile, r"AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    winget_glob = glob.glob(os.path.join(user_profile, r"AppData\Local\Microsoft\WinGet\Packages\*FFmpeg*\*\bin\ffmpeg.exe"))
    candidates.extend(winget_glob)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "ffmpeg"


def find_imgburn():
    """
    Localiza ImgBurn.exe integrado dentro del propio ejecutable (_MEIPASS),
    junto al .exe o en las rutas habituales del sistema.
    """
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "ImgBurn.exe"))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates.extend([
        os.path.join(exe_dir, "ImgBurn.exe"),
        r"S:\ImgBurn.v2.5.8.0.Multilingual-PORTABLE\ImgBurn\ImgBurn.exe",
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"ImgBurn\ImgBurn.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"ImgBurn\ImgBurn.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\ImgBurn\ImgBurn.exe"),
    ])
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _probe_video_info_legacy(video_path):
    """
    Obtiene la duración (segundos) y las dimensiones reales tras aplicar rotación (width, height)
    decodificando 1 fotograma de prueba y leyendo la salida de ffmpeg.
    """
    ffmpeg_bin = find_ffmpeg()
    cmd = [ffmpeg_bin, "-hide_banner", "-i", video_path]
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW
    )
    info_text = proc.stderr.decode("utf-8", errors="replace")

    # Duración
    duration = 0.0
    m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info_text)
    if m_dur:
        h, m, s = int(m_dur.group(1)), int(m_dur.group(2)), float(m_dur.group(3))
        duration = h * 3600 + m * 60 + s

    # Dimensiones del stream de vídeo
    width, height = 320, 240
    m_dim = re.search(r"Stream #.*Video:.*?\s(\d{2,5})x(\d{2,5})(?:[\s,]|$)", info_text)
    if m_dim:
        width, height = int(m_dim.group(1)), int(m_dim.group(2))

    # Detectar si tiene metadatos de rotación de 90 o -90 grados
    if re.search(r"rotation of -?90(?:\.0+)? degrees", info_text, re.I) or re.search(r"rotate\s*:\s*(?:90|270)", info_text, re.I):
        width, height = height, width

    return duration, width, height


def probe_video_info(video_path):
    meta=stream_profile(video_path,find_ffmpeg())
    if meta:
        return meta["duration"],meta["width"],meta["height"]
    return _probe_video_info_legacy(video_path)


def render_string_to_tiles(text, font_bytes=MAINFONT_BIN, fg=15, bg=0):
    """Convierte una cadena de 32 caracteres ASCII a 32 tiles 4bpp (1024 bytes) para el VDP de Mega Drive."""
    text = text.ljust(32)[:32]
    out = bytearray()
    for ch in text:
        idx = max(0, min(95, ord(ch) - 32))
        glyph = font_bytes[idx * 8 : (idx + 1) * 8]
        for b in glyph:
            for p in range(4):
                left_bit = (b >> (7 - p * 2)) & 1
                right_bit = (b >> (7 - p * 2 - 1)) & 1
                left_col = fg if left_bit else bg
                right_col = fg if right_bit else bg
                out.append((left_col << 4) | right_col)
    return bytes(out)


def compute_active_box(src_w, src_h, aspect_mode):
    """
    Calcula (act_w, act_h, pad_x, pad_y) dentro del lienzo 256x192 alineado a tiles de 8x8.
    """
    if aspect_mode == "stretch":
        return 256, 192, 0, 0
    if aspect_mode == "crop":
        return 256, 192, 0, 0

    # Modo "keep" (mantener proporción original alineada a bloques de 8 píxeles)
    target_ratio = 256.0 / 192.0
    src_ratio = float(src_w) / max(1.0, float(src_h))

    if abs(src_ratio - target_ratio) < 0.05:
        return 256, 192, 0, 0
    elif src_ratio < target_ratio:
        # Vídeo vertical o más estrecho que 4:3 -> pillarbox (bandas laterales negras)
        raw_w = 192.0 * src_ratio
        # Redondear a múltiplo de 16 para que (256 - act_w) // 2 sea múltiplo exacto de 8 (1 tile)
        act_w = max(32, min(256, int(round(raw_w / 16.0)) * 16))
        pad_x = (256 - act_w) // 2
        return act_w, 192, pad_x, 0
    else:
        # Vídeo panorámico 16:9 -> letterbox (bandas superior e inferior negras)
        raw_h = 256.0 / src_ratio
        act_h = max(32, min(192, int(round(raw_h / 16.0)) * 16))
        pad_y = (192 - act_h) // 2
        return 256, act_h, 0, pad_y


# ----------------------------------------------------------------------------------------
# v7.1 - Seguridad de caudal (evita cuelgues del CD), imagen neutra (sin tinte rosa)
# ----------------------------------------------------------------------------------------
def default_frame_budget(fps):
    """Bytes por fotograma que el lector 1x del Mega CD puede sostener (sin suelo artificial)."""
    return int(min(13500, max(1600, stream_limits(fps)['video'] // max(1, int(fps)))))


def max_chapter_seconds(fps):
    """Duración máxima de un .CIN para que su tabla STAB (16 bytes/entrada) quepa con holgura en el heap."""
    entries_per_sec = float(fps) + PCM_RATE_HW / float(PCM_CHUNK_SIZE)
    return MAX_STAB_ENTRIES / entries_per_sec


def effective_chapter_seconds(requested_sec, fps):
    return float(max(30.0, min(float(requested_sec), max_chapter_seconds(fps))))


def format_mmss(sec):
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


_KNOWN_MATRICES = {
    "bt709": "bt709", "bt2020nc": "bt2020", "bt2020c": "bt2020", "bt2020": "bt2020",
    "smpte170m": "bt601", "bt470bg": "bt601", "bt470m": "bt601", "fcc": "fcc", "smpte240m": "smpte240m",
}


def _probe_color_matrix_legacy(video_path, width, height):
    """
    Detecta la matriz de color (YUV->RGB) del vídeo de origen. Si el fichero no la declara, usa
    BT.709 para HD (>=720p) y BT.601 para SD. Convertir mal un vídeo HD (BT.709) como si fuera
    BT.601 desplaza los colores hacia el rojo/magenta: era una de las causas del 'filtro rosa'.
    """
    try:
        proc = subprocess.run(
            [find_ffmpeg(), "-hide_banner", "-i", video_path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW
        )
        text = proc.stderr.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "Video:" not in line:
                continue
            for group in re.findall(r"\(([^()]*)\)", line):
                for tok in group.split(","):
                    cand = tok.strip().lower().split("/")[0]
                    if cand in _KNOWN_MATRICES:
                        return _KNOWN_MATRICES[cand]
            break
    except Exception:
        pass
    return "bt709" if (height >= 720 or width >= 1200) else "bt601"


def probe_color_matrix(video_path,width,height):
    meta=stream_profile(video_path,find_ffmpeg())
    if meta and meta.get("color_space") in _KNOWN_MATRICES:
        return _KNOWN_MATRICES[meta["color_space"]]
    return _probe_color_matrix_legacy(video_path,width,height)


TINT_PRESETS = {
    "off": None,
    "auto": (0.97, 1.03, 0.97),      # compensación suave rosa/magenta -> neutro
    "strong": (0.93, 1.06, 0.94),    # compensación fuerte
}

EQ_PROFILES = {
    "svideo_hdmi": "eq=gamma=1.20:contrast=1.04:brightness=0.02:saturation=0.94",
    "crt": "eq=gamma=1.15:contrast=1.08:saturation=1.08",
    "standard": "eq=gamma=1.12:saturation=1.0",
}


def build_video_filter(fps, video_eq_mode, aspect_mode, act_box, color_matrix, tint_fix="auto"):
    """Cadena de filtros ffmpeg: fps -> perfil de brillo -> escalado con matriz explícita -> RGB24 -> tinte."""
    act_w, act_h, pad_x, pad_y = act_box
    if video_eq_mode == "svideo_hdmi":
        eq = EQ_PROFILES["svideo_hdmi"]
    elif video_eq_mode in ("high_contrast", "crt"):
        eq = EQ_PROFILES["crt"]
    else:
        eq = EQ_PROFILES["standard"]
    cm = f"in_color_matrix={color_matrix}"
    if aspect_mode == "crop":
        scale = f"scale={act_w}:{act_h}:force_original_aspect_ratio=increase:flags=lanczos:{cm},format=rgb24,crop={act_w}:{act_h},pad=256:192:{pad_x}:{pad_y}:black"
    elif aspect_mode == "stretch":
        scale = f"scale={act_w}:{act_h}:flags=lanczos:{cm},format=rgb24,pad=256:192:{pad_x}:{pad_y}:black"
    else:
        scale = f"scale={act_w}:{act_h}:force_original_aspect_ratio=decrease:flags=lanczos:{cm},format=rgb24,pad=256:192:{pad_x}+({act_w}-iw)/2:{pad_y}+({act_h}-ih)/2:black"
    chain = f"fps={fps},{eq},{scale}"
    preset = TINT_PRESETS.get(tint_fix)
    if preset:
        chain += f",colorchannelmixer=rr={preset[0]}:gg={preset[1]}:bb={preset[2]}"
    return chain


def protect_neutrals(rgb, tint_fix="off"):
    """
    Protege los grises: los píxeles casi neutros (poca saturación) se convierten en gris exacto
    antes de cuantizar a la paleta del VDP. Así ninguna paleta de 15 colores 'tiñe' de rosa las
    zonas grises, paredes, sombras o cielos nublados.
    """
    if fast.AVAILABLE:
        return fast.neutral_pixels(rgb,np.array(TINT_PRESETS.get(tint_fix) or (1.,1.,1.),dtype=np.float32))
    f = rgb.astype(np.float32)
    gains = np.array(TINT_PRESETS.get(tint_fix) or (1.0, 1.0, 1.0), dtype=np.float32)
    restored = f / gains
    gray_level = np.median(restored, axis=2)
    expected_gray = np.clip(gray_level[..., None] * gains, 0, 255)
    exact_neutral = np.max(np.abs(f - expected_gray), axis=2) <= 1.5
    chroma = restored.max(axis=2) - restored.min(axis=2)
    w = np.clip((30.0 - chroma) / 22.0, 0.0, 1.0)
    luma = f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114
    out = f + (luma[..., None] - f) * w[..., None]
    out[exact_neutral] = gray_level[exact_neutral, None]
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def extract_segment_pcm(
    video_path, start_sec, dur_sec, work_dir,
    audio_mode="cinema_boost", vocal_clarity=True,cancel_cb=None
):
    """
    Extrae el audio de un segmento en precisión de 16 bits (pcm_s16le), aplica masterización
    dinámica de diálogos, claridad vocal y puerta de silencio digital, y lo convierte al
    formato sign-magnitude de 8 bits del chip Ricoh RF5C164 con volumen al máximo y cero siseo:
      1) Filtra subgraves (<65 Hz) y anti-aliasing (>7.200 Hz) en 16 bits SIN dithering triangular
         para eliminar por completo el ruido blanco de fondo ("shhhh").
      2) Si vocal_clarity=True, aplica realce de presencia vocal en 2.6 kHz para máxima nitidez
         a través del circuito analógico de Mega CD / Mega Drive y conversores S-Video/HDMI.
      3) Normaliza el nivel RMS activo y los picos en coma flotante de 32 bits con limitador
         suave (tanh) y puerta de silencio digital (muestras en silencio = 0x80 exacto).
      4) Jamás inserta bytes 0xFF y alinea a múltiplo exacto de PCM_CHUNK_SIZE (16.384 bytes).
    """
    ffmpeg_bin = find_ffmpeg()
    wav_path = os.path.join(work_dir, "seg_audio_s16.wav")
    if os.path.exists(wav_path):
        os.remove(wav_path)

    af_parts = ["highpass=f=65:poles=2"]
    if vocal_clarity:
        af_parts.append("equalizer=f=2600:width_type=o:width=1.5:g=3.5")
    af_parts.append("lowpass=f=7200:poles=2")

    if audio_mode in ("max_boost", "max"):
        af_parts.append("dynaudnorm=f=80:g=5:p=0.96:m=18.0:s=14.0")
        target_rms = 0.35
        min_gain = 1.75
        max_scale = 8.0
    elif audio_mode in ("cinema_boost", "cinema"):
        af_parts.append("dynaudnorm=f=100:g=7:p=0.95:m=12.0:s=10.0")
        target_rms = 0.28
        min_gain = 1.35
        max_scale = 5.5
    elif audio_mode == "standard":
        af_parts.append("dynaudnorm=f=150:g=11:p=0.94:m=6.0:s=8.0")
        target_rms = 0.21
        min_gain = 1.15
        max_scale = 3.5
    else:
        target_rms = None
        min_gain = 1.0
        max_scale = 1.0

    af_parts.append(f"aresample={PCM_RATE_HW}:filter_size=64")
    af_chain = ",".join(af_parts)

    cmd = [ffmpeg_bin, "-y"]
    if start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    if dur_sec is not None:
        cmd += ["-t", f"{dur_sec:.3f}"]
    cmd += [
        "-i", video_path,
        "-vn", "-af", af_chain,
        "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(PCM_RATE_HW),
        wav_path
    ]
    run_ffmpeg_quiet(cmd,cancel_cb=cancel_cb,timeout=max(120,(dur_sec or 300)*2))

    min_samples = int(math.ceil(((dur_sec if dur_sec else 1.0) + 0.75) * PCM_RATE_HW))
    padded_len = int(math.ceil(max(min_samples, PCM_CHUNK_SIZE) / float(PCM_CHUNK_SIZE))) * PCM_CHUNK_SIZE

    if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
        return b"\x80" * padded_len

    with wave.open(wav_path, "rb") as wf:
        raw_s16 = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    if len(raw_s16) == 0:
        return b"\x80" * padded_len

    samples = raw_s16.astype(np.float32) / 32768.0
    samples = samples - float(np.mean(samples))

    if target_rms is not None:
        active_mask = np.abs(samples) > 0.006
        if np.any(active_mask):
            cur_rms = float(np.sqrt(np.mean(samples[active_mask] ** 2)))
            rms_gain = min(max_scale, max(1.0, target_rms / max(cur_rms, 0.01)))
            gain = max(min_gain, rms_gain)
            samples = samples * gain
        else:
            samples = samples * min_gain

        # Limitador suave de rodilla redondeada (tanh) para evitar distorsión áspera en picos
        drive = 1.20 if audio_mode in ("max_boost", "max") else 1.12
        samples = np.tanh(samples * drive) / float(np.tanh(drive))

        # Aprovechar el 97% del rango dinámico de 8 bits del chip RF5C164
        p998 = float(np.percentile(np.abs(samples), 99.8))
        if 0.015 < p998 < 0.96:
            samples = samples * min(2.2, 0.97 / p998)

    samples = np.clip(samples, -1.0, 1.0)

    # Puerta de silencio digital suave: elimina cualquier fluctuación residual de ±1 nivel en silencios
    gate_thresh = 1.15 / 126.0
    samples[np.abs(samples) < gate_thresh] = 0.0

    # Cuantizar directamente a magnitud signada (-126..+126)
    signed_q = np.round(samples * 126.0).astype(np.int16)
    signed_q = np.clip(signed_q, -126, 126)

    total_len = int(math.ceil(max(len(signed_q) + PCM_RATE_HW // 2, padded_len) / float(PCM_CHUNK_SIZE))) * PCM_CHUNK_SIZE
    out = np.full(total_len, 0x80, dtype=np.uint8)

    pos_mask = signed_q > 0
    neg_mask = signed_q < 0

    out_body = out[:len(signed_q)]
    out_body[pos_mask] = signed_q[pos_mask].astype(np.uint8)
    out_body[neg_mask] = (0x80 | (-signed_q[neg_mask])).astype(np.uint8)

    return out.tobytes()


VDP512_RGB = np.array(
    [[(c >> 6) & 7, (c >> 3) & 7, c & 7] for c in range(512)], dtype=np.int32
)


def optimize_palette_for_tiles(t_sub, global_counts=None):
    """
    Calcula una paleta óptima de 15 colores VDP (+ índice 0 negro) en espacio RGB 3D
    preservando todos los matices cromáticos (azules, naranjas, piel, blancos) y ordena
    los índices 1..15 por luminancia.
    """
    if fast.AVAILABLE:
        _flat=t_sub.reshape(-1,3)
        _ids=(_flat[:,0]<<6)|(_flat[:,1]<<3)|_flat[:,2]
        _hist=np.bincount(_ids,minlength=512).astype(np.float64)
        if global_counts is not None:_hist+=0.06*global_counts
        _hist[0]=0
        return fast.palette_counts(_hist)
    flat = t_sub.reshape(-1, 3)
    c_int = (flat[:, 0] << 6) | (flat[:, 1] << 3) | flat[:, 2]
    counts = np.bincount(c_int, minlength=512).astype(np.float64)
    if global_counts is not None:
        # Mezclar un 6% del histograma global para que las 4 paletas compartan tonos base
        # comunes (sombras/grises) y no existan saltos de matiz entre tiles 8x8 vecinos
        counts = counts + 0.06 * global_counts
    counts[0] = 0.0
    nonzero = np.where(counts > 0)[0]
    pal = np.zeros((16, 3), dtype=np.int32)
    if len(nonzero) == 0:
        return pal

    u_colors = VDP512_RGB[nonzero]
    u_weights = counts[nonzero]
    if len(nonzero) <= 15:
        luma_u = u_colors[:, 0] * 299 + u_colors[:, 1] * 587 + u_colors[:, 2] * 114
        u_sorted = u_colors[np.argsort(luma_u, kind="mergesort")]
        pal[1 : 1 + len(nonzero)] = u_sorted
        if len(nonzero) < 15:
            pal[1 + len(nonzero) : 16] = u_sorted[-1]
        return pal

    # Farthest-point k-means++ en espacio RGB 3D ponderado por raíz cuadrada de frecuencia
    # para no sacrificar matices de color distintos (ej. azul vs naranja)
    chosen = [u_colors[np.argmax(u_weights)].astype(np.float64)]
    min_sq = np.minimum(
        ((u_colors - chosen[0]) ** 2).sum(axis=1),
        (u_colors ** 2).sum(axis=1)
    ).astype(np.float64)

    w_sqrt = np.sqrt(u_weights)
    for _ in range(14):
        nxt = np.argmax(min_sq * w_sqrt)
        c_new = u_colors[nxt].astype(np.float64)
        chosen.append(c_new)
        min_sq = np.minimum(min_sq, ((u_colors - c_new) ** 2).sum(axis=1))

    all_ctrs = np.vstack([np.zeros((1, 3)), np.array(chosen)])
    for _ in range(6):
        d = ((u_colors[:, None, :] - all_ctrs[None, :, :]) ** 2).sum(axis=2)
        lbl = np.argmin(d, axis=1)
        for k in range(1, 16):
            msk = (lbl == k)
            if msk.any():
                w = u_weights[msk]
                all_ctrs[k] = np.round((u_colors[msk] * w[:, None]).sum(axis=0) / w.sum())

    pal[1:16] = np.clip(np.round(all_ctrs[1:16]), 0, 7).astype(np.int32)
    luma = pal[1:16, 0] * 299 + pal[1:16, 1] * 587 + pal[1:16, 2] * 114
    pal[1:16] = pal[1:16][np.argsort(luma, kind="mergesort")]
    return pal


def _cluster_tiles_3d_rgb(active_tiles, active_id, active_idx, n_pals):
    """Agrupa los tiles activos en n_pals paletas usando k-means++ en espacio RGB 3D con coherencia espacial."""
    if fast.AVAILABLE:
        return fast.cluster_tiles(active_tiles,active_id,active_idx,n_pals)
    global_counts = np.bincount(active_id.ravel(), minlength=512).astype(np.float64)
    global_counts[0] = 0.0

    tile_means = active_tiles.mean(axis=1).astype(np.float32)
    first_idx = int(np.argmin(((tile_means - tile_means.mean(axis=0)) ** 2).sum(axis=1)))
    centroids = [tile_means[first_idx].copy()]
    min_d2 = ((tile_means - centroids[0]) ** 2).sum(axis=1)
    for _ in range(1, n_pals):
        nxt = int(np.argmax(min_d2))
        centroids.append(tile_means[nxt].copy())
        min_d2 = np.minimum(min_d2, ((tile_means - centroids[-1]) ** 2).sum(axis=1))
    centroids = np.array(centroids, dtype=np.float32)

    for _ in range(5):
        dists = ((tile_means[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        assign = np.argmin(dists, axis=1)
        for p in range(n_pals):
            msk = assign == p
            if msk.any():
                centroids[p] = tile_means[msk].mean(axis=0)

    c_order = np.argsort(centroids @ np.array([0.299, 0.587, 0.114], dtype=np.float32))
    centroids = centroids[c_order]
    dists = ((tile_means[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    assign = np.argmin(dists, axis=1)

    pals = np.zeros((n_pals, 16, 3), dtype=np.int32)
    for p in range(n_pals):
        msk = assign == p
        pals[p] = optimize_palette_for_tiles(
            active_tiles[msk] if msk.any() else active_tiles, global_counts
        )

    for it in range(2):
        d512 = ((VDP512_RGB[None, :, None, :] - pals[:, None, :, :]) ** 2).sum(axis=3)
        lut_err = np.min(d512, axis=2)
        tile_err_p = lut_err[:, active_id].sum(axis=2)
        assign = np.argmin(tile_err_p, axis=0)

        # Regularización espacial suave en la rejilla 24x32 para evitar saltos de paleta en paredes/fondos
        grid_p = np.full((24, 32), -1, dtype=np.int32)
        grid_p.ravel()[active_idx] = assign
        err_grid = np.zeros((n_pals, 24, 32), dtype=np.float32)
        err_grid.reshape(n_pals, 768)[:, active_idx] = tile_err_p

        neigh_same = np.zeros((n_pals, 24, 32), dtype=np.float32)
        for p in range(n_pals):
            pm = (grid_p == p).astype(np.float32)
            neigh_same[p, 1:, :] += pm[:-1, :]
            neigh_same[p, :-1, :] += pm[1:, :]
            neigh_same[p, :, 1:] += pm[:, :-1]
            neigh_same[p, :, :-1] += pm[:, 1:]

        reg_score = err_grid - 2.5 * neigh_same
        assign = np.argmin(reg_score.reshape(n_pals, 768)[:, active_idx], axis=0)

        if it == 0:
            for p in range(n_pals):
                msk = assign == p
                if msk.any():
                    pals[p] = optimize_palette_for_tiles(active_tiles[msk], global_counts)

    d512 = ((VDP512_RGB[None, :, None, :] - pals[:, None, :, :]) ** 2).sum(axis=3)
    lut_idx = np.argmin(d512, axis=2).astype(np.uint8)
    return pals, assign, lut_idx


def _pack_indices_to_bytes(idx_arr):
    """Empaqueta un array (..., 2*K) de índices 4bpp (0..15) en bytes uint8 (..., K)."""
    hi = (idx_arr[..., 0::2] & 0x0F) << 4
    lo = idx_arr[..., 1::2] & 0x0F
    return (hi | lo).astype(np.uint8)


def quantize_and_encode_frame(
    rgb,
    act_box,
    num_palettes=4,
    prev2_tinds=None,
    target_frame_bytes=8800,
    force_border_refresh=False,
    hard_frame_bytes=None,allow_lossy_reuse=True,
):
    """
    Cuantizador VDP 3D RGB + Codificador Vectorial True-RGB Cinepak for Sega libre de artefactos:
      - Method 0 (0B) se aplica ÚNICAMENTE a tiles inactivos o visualmente idénticos sin pérdidas
        (jamás fuerza tiles cambiados ni arrastra cuadraditos de fotogramas/escenas anteriores).
      - Los diccionarios cb32 (4x2) y cb16 (2x2) usan exclusivamente vectores reales (medoides)
        y buscan coincidencias en el espacio de color RGB VDP real de cada paleta (nunca promedian
        números de índice 0..15).
      - El presupuesto disponible promueve los tiles con detalle fino a Method 3 (2x2, 16B) y
        Method 1 (8x8 sin pérdida, 32B).
    Devuelve: (pal_count_out, pal_bytes, pal_map_bytes, encoded_tiles_payload, new_tinds)
    """
    act_w, act_h, pad_x, pad_y = act_box
    H, W, _ = rgb.shape
    dither = (np.tile(BAYER4X4, (H // 4, W // 4))[:, :, None] * 3) // 4
    mask_active = np.zeros((H, W, 1), dtype=np.int16)
    mask_active[pad_y : pad_y + act_h, pad_x : pad_x + act_w, :] = 1

    # Máscara geométrica exacta de los tiles de borde (bandas negras laterales o superior/inferior)
    tile_active_grid = np.zeros((24, 32), dtype=bool)
    tile_active_grid[pad_y // 8 : (pad_y + act_h) // 8, pad_x // 8 : (pad_x + act_w) // 8] = True
    border_mask = ~tile_active_grid.ravel()

    rgb_i16 = rgb.astype(np.int16)
    rgb_d = np.clip(rgb_i16 + dither * mask_active, 0, 255)
    vdp3 = ((rgb_d * 7 + 127) // 255).astype(np.int32) * mask_active
    tiles_vdp = vdp3.reshape(24, 8, 32, 8, 3).transpose(0, 2, 1, 3, 4).reshape(768, 64, 3)
    tiles_id = (tiles_vdp[:, :, 0] << 6) | (tiles_vdp[:, :, 1] << 3) | (tiles_vdp[:, :, 2])
    active_idx = np.where((~border_mask) & (tiles_id.max(axis=1) > 0))[0]

    if num_palettes == 1:
        pals = np.zeros((2, 16, 3), dtype=np.int32)
        tpals = np.zeros(768, dtype=np.int32)
        tinds = np.zeros((768, 64), dtype=np.uint8)
        if len(active_idx) > 0:
            active_tiles = tiles_vdp[active_idx]
            pals[0] = optimize_palette_for_tiles(active_tiles)
            d512 = ((VDP512_RGB[:, None, :] - pals[0][None, :, :]) ** 2).sum(axis=2)
            lut_idx = np.argmin(d512, axis=1).astype(np.uint8)
            tinds[active_idx] = lut_idx[tiles_id[active_idx]]
        pal_count_out = 2
        num_usable_pals = 1
    elif num_palettes == 2:
        pals = np.zeros((2, 16, 3), dtype=np.int32)
        tpals = np.zeros(768, dtype=np.int32)
        tinds = np.zeros((768, 64), dtype=np.uint8)
        if len(active_idx) > 0:
            pals, assign, lut_idx = _cluster_tiles_3d_rgb(tiles_vdp[active_idx], tiles_id[active_idx], active_idx, 2)
            tpals[active_idx] = assign
            tinds[active_idx] = lut_idx[assign[:, None], tiles_id[active_idx]]
        pal_count_out = 2
        num_usable_pals = 2
    else:
        pals = np.zeros((4, 16, 3), dtype=np.int32)
        tpals = np.zeros(768, dtype=np.int32)
        tinds = np.zeros((768, 64), dtype=np.uint8)
        if len(active_idx) > 0:
            pals, assign, lut_idx = _cluster_tiles_3d_rgb(tiles_vdp[active_idx], tiles_id[active_idx], active_idx, 4)
            tpals[active_idx] = assign
            tinds[active_idx] = lut_idx[assign[:, None], tiles_id[active_idx]]
        pal_count_out = 4
        num_usable_pals = 4

    pals_f = pals[:num_usable_pals].astype(np.float32)
    target_tile_rgb = pals_f[tpals[:, None], tinds]

    # 1. Method 0 (0 bytes):
    #    - En los 2 primeros fotogramas de cada vídeo (prev2_tinds is None -> Bancos 0 y 1 de Word RAM),
    #      JAMÁS se usa Method 0 en ningún tile (is_m0 = False para los 768 tiles). Así se sobreescriben
    #      con 0x00 (negro puro) todas las bandas laterales/horizontales en ambos bancos de Word RAM,
    #      eliminando cualquier resto de basura o ruido de la BIOS en los bordes del vídeo.
    #    - A partir del fotograma 2 (prev2_tinds is not None), ambos bancos de Word RAM ya tienen 0x00
    #      en las bandas de borde, por lo que border_mask y lossless_m0 usan Method 0 (0 bytes) limpiamente.
    is_m0 = np.zeros(768, dtype=bool)
    best_m0_pal = tpals.copy()

    if prev2_tinds is not None:
        _gpu_previous=gpu.previous(target_tile_rgb,prev2_tinds,pals_f)
        if _gpu_previous is not None:
            mse_all,max_all,score_m0=_gpu_previous
        else:
            prev2_rgb_all = pals_f[:, prev2_tinds]
            diff_m0 = target_tile_rgb[None, :, :, :] - prev2_rgb_all
            pix_sq_err = (diff_m0 * diff_m0).sum(axis=3)
            mse_all = pix_sq_err.mean(axis=2)
            max_all = pix_sq_err.max(axis=2)
            score_m0 = mse_all + 0.15 * max_all
        best_m0_pal = np.argmin(score_m0, axis=0).astype(np.int32)
        idx_768 = np.arange(768)
        err_m0_mse = mse_all[best_m0_pal, idx_768]
        err_m0_max = max_all[best_m0_pal, idx_768]

        lossless_m0 = (err_m0_mse <= 0.015) & (err_m0_max <= 1.0)
        is_m0 = border_mask | lossless_m0
        tpals[is_m0] = best_m0_pal[is_m0]
        tpals[border_mask] = 0
        if force_border_refresh:
            # Refresco periódico de las bandas negras en ambos bancos de Word RAM (8 B/tile con el
            # bloque negro cb32[0]) para que nunca pueda reaparecer ruido residual en los bordes
            is_m0[border_mask] = False
            target_frame_bytes = min(15500, target_frame_bytes + int(np.sum(border_mask)) * 8)
        elif allow_lossy_reuse:
            # Si el presupuesto por fotograma está limitado para caber en 1 CD (700 MB),
            # promover a Method 0 (0 bytes) los tiles con menor diferencia inter-fotograma
            est_m2_base = 12 + pal_count_out * 32 + 192 + 192 + 8 + 1024 + int(np.sum(~is_m0)) * 8
            if est_m2_base > target_frame_bytes:
                need_m0_tiles = (est_m2_base - target_frame_bytes + 7) // 8
                cand_m0 = np.where(~is_m0)[0]
                if len(cand_m0) > 0:
                    err_m0_cand = score_m0[best_m0_pal[cand_m0], cand_m0]
                    promote_idx = cand_m0[np.argsort(err_m0_cand)[:need_m0_tiles]]
                    is_m0[promote_idx] = True
                    tpals[promote_idx] = best_m0_pal[promote_idx]
    else:
        # En los 2 primeros fotogramas compensamos en el presupuesto los 8B/tile necesarios
        # para limpiar las bandas negras en ambos bancos de Word RAM sin restar calidad al vídeo activo
        target_frame_bytes = min(15500, target_frame_bytes + int(np.sum(border_mask)) * 8)

    non_m0_idx = np.where(~is_m0)[0]

    # 2. Diccionario cb32 (bloques 4x2, 8B/tile) y búsqueda en espacio RGB real
    blocks32_all = (
        tinds.reshape(768, 4, 2, 2, 4)
        .transpose(0, 1, 3, 2, 4)
        .reshape(768, 8, 8)
    )
    blocks16_all = (
        tinds.reshape(768, 4, 2, 4, 2)
        .transpose(0, 1, 3, 2, 4)
        .reshape(768, 16, 4)
    )

    if len(non_m0_idx) > 0:
        b32_active = blocks32_all[non_m0_idx].reshape(-1, 8)
        b32_packed = np.ascontiguousarray(_pack_indices_to_bytes(b32_active)).view(np.uint32).ravel()
        _, u32_first, u32_counts = np.unique(b32_packed, return_index=True, return_counts=True)
        u32_blocks = b32_active[u32_first]

        if len(u32_blocks) <= 255:
            cb32_vecs = np.vstack([np.zeros((1, 8), dtype=np.uint8), u32_blocks.astype(np.uint8)])[:256]
        else:
            freq32_order = np.argsort(-u32_counts)
            top32_k = 180
            keep32 = list(freq32_order[:top32_k])
            rem32 = freq32_order[top32_k:]
            rem32_b = u32_blocks[rem32].astype(np.float32)
            score32 = (
                rem32_b.mean(axis=1) * 16.0
                + (rem32_b[:, :4].mean(axis=1) - rem32_b[:, 4:].mean(axis=1)) * 4.0
                + (rem32_b[:, 0::2].mean(axis=1) - rem32_b[:, 1::2].mean(axis=1))
            )
            s32_ord = np.argsort(score32)
            picks32 = np.linspace(0, len(s32_ord) - 1, 255 - top32_k, dtype=np.int32)
            keep32.extend(rem32[s32_ord[picks32]])
            # Fijar siempre el bloque negro puro [0]*8 en cb32_vecs[0] para limpieza exacta de bordes/sombras
            cb32_vecs = np.vstack([np.zeros((1, 8), dtype=np.uint8), u32_blocks[keep32].astype(np.uint8)])

        m2_codes = np.zeros((768, 8), dtype=np.uint8)
        for p in range(num_usable_pals):
            p_tiles = non_m0_idx[(tpals[non_m0_idx] == p) & (~border_mask[non_m0_idx])]
            if len(p_tiles) == 0:
                continue
            b_p = blocks32_all[p_tiles].reshape(-1, 8)
            b_rgb = pals_f[p, b_p].reshape(-1, 24)
            cb_rgb = pals_f[p, cb32_vecs].reshape(-1, 24)
            _nearest=gpu.nearest(b_rgb,cb_rgb)
            if _nearest is None:
                d_rgb = ((b_rgb*b_rgb).sum(axis=1,keepdims=True)
                         +(cb_rgb*cb_rgb).sum(axis=1,keepdims=True).T-2.0*(b_rgb@cb_rgb.T))
                _nearest=np.argmin(d_rgb,axis=1).astype(np.uint8)
            m2_codes[p_tiles]=_nearest.reshape(len(p_tiles),8)

        m2_blocks = cb32_vecs[m2_codes]
        m2_tinds = (
            m2_blocks.reshape(768, 4, 2, 2, 4)
            .transpose(0, 1, 3, 2, 4)
            .reshape(768, 64)
        )
        m2_rgb = pals_f[tpals[:, None], m2_tinds]
        diff_m2_pix = ((target_tile_rgb - m2_rgb) ** 2).sum(axis=2)
        err_m2_mse = diff_m2_pix.mean(axis=1)
        err_m2_max = diff_m2_pix.max(axis=1)
        err_m2_score = err_m2_mse + 0.12 * err_m2_max
    else:
        cb32_vecs = np.zeros((0, 8), dtype=np.uint8)
        m2_codes = np.zeros((768, 8), dtype=np.uint8)
        m2_tinds = tinds.copy()
        err_m2_score = np.zeros(768, dtype=np.float32)

    # 3. Asignación de métodos (Method 3 bloques 2x2 de 16B y Method 1 sin pérdida de 32B)
    tile_method = np.where(is_m0, 0, 2).astype(np.int32)
    m2_only_bytes = 12 + pal_count_out * 32 + 192 + 192 + 8 + len(cb32_vecs) * 4 + len(non_m0_idx) * 8
    spare_bytes = max(0, target_frame_bytes - m2_only_bytes)

    err_m3_score = np.zeros(768,dtype=np.float32)
    cb16_vecs = np.zeros((0, 4), dtype=np.uint8)
    m3_codes = np.zeros((768, 16), dtype=np.uint8)
    m3_tinds = m2_tinds.copy()

    if len(non_m0_idx) > 0 and spare_bytes >= 24:
        err_order = non_m0_idx[np.argsort(-err_m2_score[non_m0_idx])]
        need_up = err_order[err_m2_score[err_order] > 0.001]

        # Si el presupuesto sobrante permite actualizar directamente todos los tiles con error a Method 1
        # (o si spare_bytes < 800 donde el diccionario cb16 de 512B no compensa), usar Method 1 directo
        if len(need_up) * 24 <= spare_bytes or spare_bytes < 800:
            n_m1 = min(len(need_up), spare_bytes // 24)
            tile_method[need_up[:n_m1]] = 1
        else:
            # Construir cb16 (bloques 2x2) en espacio RGB real para los tiles con error en Method 2
            priority_tiles = need_up[: min(len(need_up), 260)]
            b16_active = blocks16_all[priority_tiles].reshape(-1, 4)
            b16_packed = np.ascontiguousarray(_pack_indices_to_bytes(b16_active)).view(np.uint16).ravel()
            _, u16_first, u16_counts = np.unique(b16_packed, return_index=True, return_counts=True)
            u16_blocks = b16_active[u16_first]

            if len(u16_blocks) <= 256:
                cb16_vecs = u16_blocks.astype(np.uint8)
            else:
                freq16_order = np.argsort(-u16_counts)
                top16_k = 192
                keep16 = list(freq16_order[:top16_k])
                rem16 = freq16_order[top16_k:]
                rem16_b = u16_blocks[rem16].astype(np.float32)
                score16 = (
                    rem16_b.mean(axis=1) * 16.0
                    + (rem16_b[:, 0] + rem16_b[:, 1] - rem16_b[:, 2] - rem16_b[:, 3]) * 2.0
                )
                s16_ord = np.argsort(score16)
                picks16 = np.linspace(0, len(s16_ord) - 1, 256 - top16_k, dtype=np.int32)
                keep16.extend(rem16[s16_ord[picks16]])
                cb16_vecs = u16_blocks[keep16].astype(np.uint8)

            for p in range(num_usable_pals):
                p_tiles = need_up[tpals[need_up] == p]
                if len(p_tiles) == 0:
                    continue
                b16_p = blocks16_all[p_tiles].reshape(-1, 4)
                b16_rgb = pals_f[p, b16_p].reshape(-1, 12)
                cb16_rgb = pals_f[p, cb16_vecs].reshape(-1, 12)
                _nearest=gpu.nearest(b16_rgb,cb16_rgb)
                if _nearest is None:
                    d16_rgb=((b16_rgb*b16_rgb).sum(axis=1,keepdims=True)
                             +(cb16_rgb*cb16_rgb).sum(axis=1,keepdims=True).T-2.0*(b16_rgb@cb16_rgb.T))
                    _nearest=np.argmin(d16_rgb,axis=1).astype(np.uint8)
                m3_codes[p_tiles]=_nearest.reshape(len(p_tiles),16)

            m3_blocks = cb16_vecs[m3_codes[need_up]]
            m3_tinds[need_up] = (
                m3_blocks.reshape(len(need_up), 4, 4, 2, 2)
                .transpose(0, 1, 3, 2, 4)
                .reshape(len(need_up), 64)
            )
            m3_rgb = pals_f[tpals[need_up][:, None], m3_tinds[need_up]]
            diff_m3_pix = ((target_tile_rgb[need_up] - m3_rgb) ** 2).sum(axis=2)
            err_m3_score = np.zeros(768, dtype=np.float32)
            err_m3_score[need_up] = diff_m3_pix.mean(axis=1) + 0.12 * diff_m3_pix.max(axis=1)

            rem_budget = spare_bytes - len(cb16_vecs) * 2
            for t in need_up:
                if rem_budget < 8:
                    break
                e2 = err_m2_score[t]
                e3 = err_m3_score[t]
                if e3 > 0.32 and rem_budget >= 24:
                    tile_method[t] = 1
                    rem_budget -= 24
                elif e3 < e2 and rem_budget >= 8:
                    tile_method[t] = 3
                    rem_budget -= 8
                elif rem_budget >= 24:
                    tile_method[t] = 1
                    rem_budget -= 24

            if rem_budget >= 16:
                rem_err = np.zeros(768, dtype=np.float32)
                m2_cur = tile_method == 2
                m3_cur = tile_method == 3
                rem_err[m2_cur] = err_m2_score[m2_cur]
                rem_err[m3_cur] = err_m3_score[m3_cur]
                up_order = need_up[np.argsort(-rem_err[need_up])]
                for t in up_order:
                    if rem_budget < 16 or rem_err[t] <= 0.001:
                        break
                    if tile_method[t] == 3 and rem_budget >= 16:
                        tile_method[t] = 1
                        rem_budget -= 16
                    elif tile_method[t] == 2 and rem_budget >= 24:
                        tile_method[t] = 1
                        rem_budget -= 24

            # Si quedaron pocos tiles en Method 3 (< 20), es más eficiente ahorrar los 512B de cb16
            # y promover esos tiles directamente a Method 1 (sin pérdida)
            m3_list = np.where(tile_method == 3)[0]
            if 0 < len(m3_list) < 20:
                tile_method[m3_list] = 2
                cb16_vecs = np.zeros((0, 4), dtype=np.uint8)
                rem_m1 = (spare_bytes - int(np.sum(tile_method == 1)) * 24) // 24
                if rem_m1 > 0:
                    m3_sorted = m3_list[np.argsort(-err_m2_score[m3_list])]
                    tile_method[m3_sorted[:rem_m1]] = 1

    # Medir el paquete que se va a escribir; no estimar su tamaño por método.
    cb32_vecs,cb16_vecs,_actual_frame_size=fast.fit_methods(
        tile_method,m2_codes,m3_codes,cb32_vecs,cb16_vecs,tpals,best_m0_pal,
        score_m0 if prev2_tinds is not None else np.zeros((num_usable_pals,768)),
        err_m2_score,err_m3_score,border_mask,prev2_tinds,force_border_refresh,
        pal_count_out,hard_frame_bytes,allow_lossy_reuse)

    if not np.any(tile_method == 3):
        cb16_vecs = np.zeros((0, 4), dtype=np.uint8)
    if not np.any(tile_method == 2):
        cb32_vecs = np.zeros((0, 8), dtype=np.uint8)

    new_tinds = tinds.copy()
    if prev2_tinds is not None:
        m0_mask = tile_method == 0
        new_tinds[m0_mask] = prev2_tinds[m0_mask]
    m2_mask = tile_method == 2
    new_tinds[m2_mask] = m2_tinds[m2_mask]
    m3_mask = tile_method == 3
    new_tinds[m3_mask] = m3_tinds[m3_mask]

    if fast.AVAILABLE:
        _pal,_map,_payload=fast.pack_output(pals,tpals,tile_method,tinds,m2_codes,m3_codes,cb32_vecs,cb16_vecs)
        return pal_count_out,_pal.tobytes(),_map.tobytes(),_payload.tobytes(),new_tinds

    pal_buf = bytearray(pal_count_out * 32)
    pos = 0
    for p in range(pal_count_out):
        for c in range(16):
            if c == 0:
                pal_buf[pos] = 0
                pal_buf[pos + 1] = 0
            else:
                r3, g3, b3 = int(pals[p, c, 0]), int(pals[p, c, 1]), int(pals[p, c, 2])
                pal_buf[pos] = (b3 << 1) & 0x0E
                pal_buf[pos + 1] = ((g3 << 5) & 0xE0) | ((r3 << 1) & 0x0E)
            pos += 2

    pal_map_buf = bytearray(192)
    for grp in range(48):
        lw = 0
        base = grp * 16
        for k in range(16):
            lw |= (int(tpals[base + k]) & 3) << (30 - 2 * k)
        struct.pack_into(">I", pal_map_buf, grp * 4, lw)

    cb32_bytes = _pack_indices_to_bytes(cb32_vecs).tobytes() if len(cb32_vecs) > 0 else b""
    cb16_bytes = _pack_indices_to_bytes(cb16_vecs).tobytes() if len(cb16_vecs) > 0 else b""
    raw_packed_tiles = _pack_indices_to_bytes(tinds)

    method_buf = bytearray(192)
    vector_data = bytearray()
    for grp in range(48):
        lw = 0
        base = grp * 16
        for k in range(16):
            t = base + k
            m_code = int(tile_method[t])
            lw |= (m_code & 3) << (30 - 2 * k)
            if m_code == 1:
                vector_data.extend(raw_packed_tiles[t].tobytes())
            elif m_code == 2:
                vector_data.extend(m2_codes[t].tobytes())
            elif m_code == 3:
                vector_data.extend(m3_codes[t].tobytes())
        struct.pack_into(">I", method_buf, grp * 4, lw)

    out = bytearray()
    out.extend(struct.pack(">I", len(cb32_bytes)))
    out.extend(cb32_bytes)
    out.extend(struct.pack(">I", len(cb16_bytes)))
    out.extend(cb16_bytes)
    out.extend(method_buf)
    out.extend(vector_data)

    return pal_count_out, bytes(pal_buf), bytes(pal_map_bytes := pal_map_buf), bytes(out), new_tinds


def _encode_segment_pass(
    video_path, start_sec, dur_sec, fps, aspect_mode, num_palettes,
    out_cin_path, work_dir, progress_cb=None, max_frame_budget=None,
    audio_mode="cinema_boost", vocal_clarity=True, video_eq_mode="svideo_hdmi",
    tint_fix="auto", budget_scale=1.0, max_cin_bytes=None,
    acceleration="auto", cancel_cb=None, cached_pcm=None,allow_lossy_reuse=True,frame_active_box=None
):
    """Una pasada de codificación de un segmento a .CIN. Devuelve (bytes, fotogramas, media B/s, pico B/s)."""
    gpu.configure(acceleration)
    ffmpeg_bin = find_ffmpeg()
    total_dur, src_w, src_h = probe_video_info(video_path)
    act_box = tuple(frame_active_box) if frame_active_box is not None else compute_active_box(src_w,src_h,aspect_mode)
    if any(int(v)!=v or v%8 for v in act_box) or act_box[0]<8 or act_box[1]<8 or act_box[2]<0 or act_box[3]<0 or act_box[0]+act_box[2]>256 or act_box[1]+act_box[3]>192:
        raise ValueError("Área activa no válida.")
    act_w, act_h, pad_x, pad_y = act_box

    pcm_bytes = cached_pcm if cached_pcm is not None else extract_segment_pcm(
        video_path, start_sec, dur_sec, work_dir,
        audio_mode=audio_mode, vocal_clarity=vocal_clarity
    )

    color_matrix = probe_color_matrix(video_path, src_w, src_h)
    vf_full = build_video_filter(fps, video_eq_mode, aspect_mode, act_box, color_matrix, tint_fix)

    expected_dur = dur_sec if dur_sec is not None else max(.01,total_dur-start_sec)
    expected_frames = max(1,int(round(expected_dur*fps)))
    cmd = [ffmpeg_bin,"-hide_banner","-loglevel","error","-y"]
    cmd += decode_options(video_path,ffmpeg_bin,acceleration)
    if start_sec > 0:cmd += ["-ss",f"{start_sec:.9f}"]
    # Un fotograma de margen al decodificar; el límite de salida es exacto.
    cmd += ["-t",f"{expected_frames/fps+1/fps:.9f}","-threads","2"]
    cmd += [
        "-i", video_path,
        "-an","-sn","-dn","-frames:v",str(expected_frames),
        "-vf", vf_full+",tpad=stop_mode=clone:stop_duration=2",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ]

    # Leer fotogramas por tubería en streaming para no consumir mucha RAM en vídeos largos


    frame_byte_size = FRAME_WIDTH * FRAME_HEIGHT * 3
    expected_dur = dur_sec if dur_sec is not None else max(1.0, total_dur - start_sec)
    expected_frames = max(1, int(round(expected_dur * fps)))

    pcm_chunks = [
        pcm_bytes[i : i + PCM_CHUNK_SIZE]
        for i in range(0, len(pcm_bytes), PCM_CHUNK_SIZE)
    ]
    pcm_chunk_count = len(pcm_chunks)

    frame_duration = (BASE_RATE / fps) * (PCM_RATE_HW / PCM_RATE_SUBPLAY)
    pcm_time_frame = BASE_RATE * PCM_CHUNK_SIZE / PCM_RATE_SUBPLAY
    pcm_time_current = -0.4 * pcm_time_frame
    img_time_current = 0.0
    pcm_idx = 0

    stab_entries = []
    sample_data = bytearray()
    prev1_tinds = None
    prev2_tinds = None
    f_idx = 0

    # Presupuesto de bytes por fotograma con depósito dinámico (bitrate reservoir):
    # - Aprovecha el caudal del lector CD-ROM 1x (~128 KB/s para vídeo + 16 KB/s PCM)
    # - Si max_frame_budget está definido (ajuste automático a 1 CD de 700 MB), limita el presupuesto medio
    #   por fotograma para garantizar que la totalidad de los vídeos quepa dentro de un único CD grabable.
    # v7.1: sin el antiguo suelo de 7.800 B/fotograma (a 24 FPS suponía ~187 KB/s: más de lo que lee el
    # Mega CD y causa el cuelgue). Ahora el caudal sostenido es SIEMPRE <= VIDEO_BPS_BUDGET.
    base_budget = default_frame_budget(fps)
    if not allow_lossy_reuse:base_budget=min(base_budget,protected_frame_budget(fps,expected_frames))
    if max_frame_budget is not None and max_frame_budget > 0:
        base_budget=min(base_budget,int(max_frame_budget))
    _border_tiles=768-(act_w//8)*(act_h//8)
    _refresh_extra=_border_tiles*8
    _refresh_count=sum(1 for i in range(2,expected_frames) if i%48 in (0,1)) if allow_lossy_reuse else 0
    _keyframe_cap=8000 if max_cin_bytes is not None else max(8000,base_budget)
    _last_time=(expected_frames-1)*frame_duration
    _audio_count=min(pcm_chunk_count,sum(1 for i in range(pcm_chunk_count) if _last_time>(i-.4)*pcm_time_frame))
    _audio_size=sum(len(x)+(len(x)%2) for x in pcm_chunks[:_audio_count])
    _header_size=68+16*(expected_frames+_audio_count)
    _video_quota=None
    if max_cin_bytes is not None:
        _video_quota=int(max_cin_bytes)-_audio_size-_header_size
        if expected_frames>2:
            _regular_frames=expected_frames-2-_refresh_count
            _quota_base=(_video_quota-16000-_refresh_count*_keyframe_cap)//max(1,_regular_frames)
            if _quota_base<540:raise RuntimeError("La duración no cabe en este CD conservando el audio y los FPS elegidos.")
            base_budget=min(base_budget,_quota_base)
        elif _video_quota<8000*expected_frames:
            raise RuntimeError("La cuota del capítulo no permite inicializar ambos bancos de imagen.")
    base_budget=max(540,int(base_budget*budget_scale))
    max_peak_budget = int(min(14500, max(2000, base_budget * 2.2)))
    byte_reservoir = 0
    frame_bytes_log = []

    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW
    )
    read_guard=FFmpegReadGuard(proc,cancel_cb)

    try:
        while True:
            if cancel_cb and cancel_cb():
                proc.kill();proc.wait()
                raise ConversionCancelled("Conversión cancelada.")
            f_raw = read_guard.read(frame_byte_size)
            if not f_raw or len(f_raw) < frame_byte_size:
                break
            bytes_before = len(sample_data)

            while pcm_idx < pcm_chunk_count and img_time_current > pcm_time_current:
                chunk = pcm_chunks[pcm_idx]
                offset = len(sample_data)
                length = len(chunk)
                if length % 2 == 1:
                    chunk = chunk + b"\x80"
                    length += 1
                sample_data.extend(chunk)
                stab_entries.append((offset, length, 0xFFFFFFFF, 1))
                pcm_idx += 1
                pcm_time_current += pcm_time_frame

            rgb = np.frombuffer(f_raw, dtype=np.uint8).reshape((FRAME_HEIGHT, FRAME_WIDTH, 3))
            rgb = protect_neutrals(rgb, tint_fix)
            if prev2_tinds is None:
                t_budget = min(14500, max(int(base_budget * 1.25), 8000))
            elif byte_reservoir >= 0:
                t_budget = min(max_peak_budget, base_budget + min(3800, byte_reservoir // 2))
            else:
                # Si venimos encadenando fotogramas por encima del presupuesto, se aprieta el siguiente
                t_budget = max(int(base_budget * 0.62), base_budget + byte_reservoir // 2)

            # Reservar renovaciones completas de ambos bancos cada 48 fotogramas.
            # El límite de CD no debe convertirlas en copias temporales de bloques.
            _nominal_cap=8000 if f_idx<2 else (_keyframe_cap if allow_lossy_reuse and f_idx%48 in (0,1) else base_budget)
            _hard_cap=min(14500,_nominal_cap+(max(0,byte_reservoir)//2 if fps<=15 else 0))
            t_budget=_hard_cap

            # Refrescar bandas negras en 2 fotogramas consecutivos (ambos bancos Word RAM) cada 48 fotogramas
            # En nitidez cada bloque cambiado ya se renueva. Ambos bancos se
            # inicializan completos y las bandas negras permanecen a índice 0.
            border_refresh = f_idx<2 or (allow_lossy_reuse and f_idx%48 in (0,1))
            pal_count_out, pal_bytes, pal_map_bytes, encoded_tiles_payload, cur_tinds = quantize_and_encode_frame(
                rgb, act_box, num_palettes=num_palettes, prev2_tinds=prev2_tinds, target_frame_bytes=t_budget,
                force_border_refresh=border_refresh,hard_frame_bytes=_hard_cap,allow_lossy_reuse=allow_lossy_reuse
            )

            prev2_tinds = prev1_tinds
            prev1_tinds = cur_tinds

            sm_flag = 0x20 | ((pal_count_out - 1) & 3)
            sm_header = struct.pack(
                ">2sBBIHH",
                b"SM", 0x00, sm_flag,
                12 + len(pal_bytes) + len(pal_map_bytes) + 32 * (FRAME_WIDTH // 8) * (FRAME_HEIGHT // 8),
                FRAME_WIDTH // 8,
                FRAME_HEIGHT // 8
            )
            sm_frame = bytearray()
            sm_frame.extend(sm_header)
            sm_frame.extend(pal_bytes)
            sm_frame.extend(pal_map_bytes)
            sm_frame.extend(encoded_tiles_payload)
            if len(sm_frame) % 2 == 1:
                sm_frame.append(0)

            img_time_int = int(img_time_current)
            img_dur_int = int(img_time_current + frame_duration) - img_time_int

            offset = len(sample_data)
            length = len(sm_frame)
            byte_reservoir = int(np.clip(byte_reservoir + (_nominal_cap - length), 0, 24000))
            if length>_hard_cap:raise RuntimeError("Error interno de presupuesto del fotograma.")
            sample_data.extend(sm_frame)
            stab_entries.append((offset, length, img_time_int, img_dur_int))
            frame_bytes_log.append(len(sample_data) - bytes_before)

            img_time_current += frame_duration
            f_idx += 1

            if progress_cb and (f_idx % 5 == 0 or f_idx == expected_frames):
                progress_cb(f_idx, max(f_idx, expected_frames), length)

    except BaseException:
        read_guard.close()
        if proc.poll() is None:proc.kill()
        proc.stdout.close()
        proc.wait()
        raise

    read_guard.close()
    proc.stdout.close()
    return_code = proc.wait()
    if return_code:
        raise RuntimeError(f"ffmpeg terminó con error {return_code}; se descarta el capítulo incompleto.")

    if f_idx != expected_frames:
        raise RuntimeError(f"Vídeo incompleto: ffmpeg devolvió {f_idx} fotogramas y se esperaban {expected_frames}.")
    if f_idx == 0:
        raise RuntimeError(
            "ffmpeg no ha devuelto ningún fotograma de este vídeo (archivo dañado, códec no soportado o ffmpeg.exe "
            "ausente). Se cancela para no crear un disco sin imagen que podría colgar el Mega CD."
        )

    # Verificación de caudal: media y pico en ventanas de 6 s (vídeo + audio PCM)
    arr = np.array(frame_bytes_log, dtype=np.float64)
    seg_seconds = f_idx / float(fps)
    avg_bps = float(arr.sum()) / seg_seconds
    win = int(min(f_idx, max(1, round(6 * fps))))
    csum = np.concatenate(([0.0], np.cumsum(arr)))
    peak_bps = float((csum[win:] - csum[:-win]).max() / (win / float(fps))) if f_idx >= win else avg_bps

    num_samples = len(stab_entries)
    if num_samples > MAX_STAB_ENTRIES:
        raise RuntimeError(f"La STAB tiene {num_samples} entradas; máximo seguro: {MAX_STAB_ENTRIES}.")
    stab_size = 16 + num_samples * 16 + 16
    fdsc_size = 20
    film_header_size = 16 + fdsc_size + stab_size

    film_header = bytearray()
    film_header.extend(struct.pack(">4sIII", b"FILM", film_header_size, 0, 0))
    film_header.extend(struct.pack(">4sI4sII", b"FDSC", fdsc_size, b"SEGA", FRAME_HEIGHT, FRAME_WIDTH))
    film_header.extend(struct.pack(">4sIII", b"STAB", stab_size, BASE_RATE, num_samples))
    for off, length, t_val, dur in stab_entries:
        film_header.extend(struct.pack(">IIII", off, length, t_val, dur))
    film_header.extend(struct.pack(">IIII", 0, 0, 0, 0))

    if max_cin_bytes is not None and len(film_header)+len(sample_data)>max_cin_bytes:
        raise RuntimeError("Error interno: el capítulo supera su cuota de CD.")
    with open(out_cin_path, "wb") as f:
        f.write(film_header)
        f.write(sample_data)

    return len(film_header) + len(sample_data), f_idx, avg_bps, peak_bps


def encode_segment_to_cin(
    video_path, start_sec, dur_sec, fps, aspect_mode, num_palettes,
    out_cin_path, work_dir, progress_cb=None, max_frame_budget=None,
    audio_mode="cinema_boost", vocal_clarity=True, video_eq_mode="svideo_hdmi",
    tint_fix="auto", note_cb=None, max_cin_bytes=None,
    acceleration="auto", cancel_cb=None,allow_lossy_reuse=True,frame_active_box=None
):
    """
    Codifica un vídeo (o segmento) a .CIN (Cinepak for Sega FILM/SM) comprobando que el caudal medio y
    el pico no superan lo que puede leer un Mega CD (1x). Si una pasada se pasa de caudal, se repite con
    un presupuesto menor (hasta 4 pasadas) o cancela la conversión si no cumple los límites medidos.
    """
    pcm_cache=extract_segment_pcm(video_path,start_sec,dur_sec,work_dir,audio_mode=audio_mode,vocal_clarity=vocal_clarity,cancel_cb=cancel_cb)
    scale = 1.0
    size = n_frames = 0
    for attempt in range(4):
        size, n_frames, avg_bps, peak_bps = _encode_segment_pass(
            video_path, start_sec, dur_sec, fps, aspect_mode, num_palettes,
            out_cin_path, work_dir, progress_cb=progress_cb, max_frame_budget=max_frame_budget,
            audio_mode=audio_mode, vocal_clarity=vocal_clarity, video_eq_mode=video_eq_mode,
            tint_fix=tint_fix, budget_scale=scale,max_cin_bytes=max_cin_bytes,
            acceleration=acceleration,cancel_cb=cancel_cb,cached_pcm=pcm_cache,allow_lossy_reuse=allow_lossy_reuse,frame_active_box=frame_active_box
        )
        measured = inspect_cin(out_cin_path, fps)
        avg_bps, peak_bps = measured["media_B_s"], measured["pico_6s_B_s"]
        if measured["seguro"]:
            break
        limits=stream_limits(fps)
        worst = max(avg_bps / limits['average'], peak_bps / limits['peak_6s'],
                    measured.get('pico_1s_B_s',0)/(limits['peak_1s'] or float('inf')))
        scale *= max(0.70, min(0.92, 0.97 / worst))
        if note_cb:
            note_cb(f"Caudal alto ({avg_bps / 1024:.0f} KB/s, pico {peak_bps / 1024:.0f} KB/s): "
                    f"reajustando calidad para que el Mega CD lo lea sin parones (pasada {attempt + 2})...")
    if not measured["seguro"]:
        os.remove(out_cin_path)
        raise RuntimeError("El vídeo supera el caudal seguro tras cuatro intentos. Reduce los FPS o las paletas.")
    return size, n_frames


def patch_cinepak_players(ip_bin, mainplay_bin, subplay_bin):
    """
    Aplica parches de sincronización A/V, audio sin interferencias y estabilidad de larga
    duración sobre el código 68000 de MAINPLAY.BIN (y su copia en IP.BIN + 0x30) y SUBPLAY.BIN:
      1) MAINPLAY.BIN: Elimina la condición de carrera entre el final de la segunda
         mitad del DMA al VDP (DrawFrameTileDMACopyHalf) y la interrupción VBlank
         (CinepakVint), activando NewFrameReady=1 ANTES de desenmascarar SR en la
         segunda transferencia DMA.
      2) SUBPLAY.BIN: Conserva AudioIdle y la ruta PCM de la versión 7.0.
      3) SUBPLAY.BIN (CinepakVintSub en 0x0796): En consolas PAL 50Hz, vacía todos los
         sectores pre-roll listos en el buffer del CDC dentro de la misma interrupción
         VBlank (BNE.S CinepakVintSubLoop), impidiendo que el buffer de 5 sectores del
         chip Sanyo LC8951 se desborde tras ~25-30 minutos de lecturas continuas.
      4) SUBPLAY.BIN (FilmOpen Wait en 0x0B9C): Reduce la pausa de transición entre
         capítulos .CIN de 60 ticks (1.2s PAL); la transición no es sin pausa.
      5) SUBPLAY.BIN (GetDataLoop y FilmTask): Mantiene el servicio de AudioIdle durante
         lecturas de CD y la sincronía A/V automática.
    """
    # --- 1) Parche MAINPLAY.BIN e IP.BIN (offset +0x30) ---
    assert mainplay_bin[0x0EA0:0x0EA2] == b"\x60\x2E"
    assert mainplay_bin[0x0F02:0x0F06] == b"\x61\x02\x4E\x75"

    mainplay_bin[0x0EA0:0x0EA2] = b"\x60\x36"

    stub_main = (
        b"\x40\xE7"              # 0x0EA2: MOVE.W SR, -(A7)
        b"\x00\x7C\x07\x00"      # 0x0EA4: ORI.W #$0700, SR
        b"\x61\x5C"              # 0x0EA8: BSR.S 0x0F06 (0x0EAA + 0x5C = 0x0F06)
        b"\x45\xFA\xF3\xD0"      # 0x0EAA: LEA 0x027C(PC), A2 (0x0EAC + (-0x0C30) = 0x027C)
        b"\x34\xBC\x00\x01"      # 0x0EAE: MOVE.W #1, (A2)
        b"\x46\xDF"              # 0x0EB2: MOVE.W (A7)+, SR
        b"\x4E\x75"              # 0x0EB4: RTS
    )
    mainplay_bin[0x0EA2 : 0x0EA2 + len(stub_main)] = stub_main
    mainplay_bin[0x0F02:0x0F06] = b"\x60\x9E\x4E\x71"

    ip_bin[0x30 + 0x0EA0 : 0x30 + 0x0EA2] = b"\x60\x36"
    ip_bin[0x30 + 0x0EA2 : 0x30 + 0x0EA2 + len(stub_main)] = stub_main
    ip_bin[0x30 + 0x0F02 : 0x30 + 0x0F06] = b"\x60\x9E\x4E\x71"

    # --- 2) Parche SUBPLAY.BIN ---
    assert subplay_bin[0x034C:0x0352] == b"\x11\x7C\x00\xB4\x00\x01"
    assert subplay_bin[0x13A6:0x13AA] == b"\x4E\xBA\xFF\x84"
    assert subplay_bin[0x0D50:0x0D5A] == b"\x70\x02\x2F\x00\x4E\xBA\x03\xCC\x58\x4F"
    assert subplay_bin[0x11DA:0x11DE] == b"\x00\x00\x40\x00"
    assert subplay_bin[0x1200:0x1204] == b"\x00\x00\x40\x00"

    # Subir el registro de volumen hardware (ENV en $00FF0001) del chip Ricoh RF5C164
    # de $B4 (180/255 = 70%) al máximo absoluto $FF (255/255 = 100% de ganancia analógica)
    subplay_bin[0x034E:0x0350] = b"\x00\xFF"

    # Ampliar EngineDataWrapAroundBufferSize de $4000 (16 KB) a $5800 (22 KB)
    subplay_bin[0x11DA:0x11DE] = b"\x00\x00\x58\x00"
    subplay_bin[0x1200:0x1204] = b"\x00\x00\x58\x00"

    if subplay_bin[0x0796:0x0798] != bytes.fromhex("667c"):
        raise ValueError("Revisión de CDC no compatible.")
    subplay_bin[0x0796:0x0798] = bytes.fromhex("668c")  # continuar preroll en el mismo VBlank
    # Helper 1 en 0x017E..0x018F: Llama a EngineIdle(A4) y AudioIdle(&A4->AudioData)
    stub_sub_idle = (
        b"\x2F\x0C"              # 0x017E: MOVE.L A4, -(A7)
        b"\x4E\xBA\x11\xAA"      # 0x0180: JSR 0x132C(PC) -> EngineIdle
        b"\x48\x6C\x00\x18"      # 0x0184: PEA $18(A4)    -> AudioData
        b"\x4E\xBA\x13\x50"      # 0x0188: JSR 0x14DA(PC) -> AudioIdle
        b"\x50\x4F"              # 0x018C: ADDQ.W #8, A7
        b"\x4E\x75"              # 0x018E: RTS
    )
    subplay_bin[0x017E : 0x017E + len(stub_sub_idle)] = stub_sub_idle
    subplay_bin[0x13A6:0x13AA] = b"\x4E\xBA\xED\xD6"

    # Helper 2 en 0x0190..0x01B5: Sincronía A/V en tiempo real
    stub_sub_sync = (
        b"\x90\x86"                      # 0x0190: SUB.L D6, D0
        b"\x0C\x80\x00\x00\x00\x34"      # 0x0192: CMPI.L #52, D0
        b"\x6D\x0C"                      # 0x0198: BLT.S 0x01A6
        b"\x0A\x6C\x00\x01\x00\x2C"      # 0x019A: EORI.W #$0001, $2C(A4)
        b"\x67\x04"                      # 0x01A0: BEQ.S 0x01A6
        b"\x4E\x75"                      # 0x01A2: RTS
        b"\x4E\x71"                      # 0x01A4: NOP
        b"\x42\x6C\x00\x2C"              # 0x01A6: CLR.W $2C(A4)
        b"\x70\x02"                      # 0x01AA: MOVEQ #2, D0
        b"\x2F\x00"                      # 0x01AC: MOVE.L D0, -(A7)
        b"\x4E\xBA\x0F\x72"              # 0x01AE: JSR 0x1122(PC) -> SendCommandToMain
        b"\x58\x4F"                      # 0x01B2: ADDQ.W #4, A7
        b"\x4E\x75"                      # 0x01B4: RTS
    )
    subplay_bin[0x0190 : 0x0190 + len(stub_sub_sync)] = stub_sub_sync
    subplay_bin[0x0D50:0x0D5A] = b"\x4E\xBA\xF4\x3E\x4E\x71\x4E\x71\x4E\x71"
    patch_audio_clock(subplay_bin)



def build_multi_sp_bin(subplay_bytes, sector_list, loop_flag=True, allow_skip=True, enable_osd=True, chapter_ids=None, debug_symbols=None):
    """
    Ensambla en código máquina Motorola 68000 el programa del Sub-CPU (SP.BIN)
    para reproducir secuencialmente cualquier lista de archivos .CIN (sector_list).
    En la versión 6.0:
      - Conserva el Heap de 300 KiB ($00010000..$0005B000).
      - Verifica el código de retorno de FilmOpen (TST.L D0) para reintentar limpiamente
        sin desincronizar MAINCODE si una búsqueda en el CD tarda más de lo esperado.
      - Incluye el Menú Multimedia en pantalla al pulsar START (si enable_osd=True):
          * Pausa el chip PCM Ricoh RF5C164 al entrar y lo reactiva ($FE -> $FF0011) al salir.
          * Usa las rutinas nativas de SUBPLAY.BIN (SwapMeg, SendCommandToMain, Wait) mediante
            DisplaySubWordRAM (doble SwapMeg simétrico) para preservar intacta la polaridad de
            los dos bancos de 1M de Word RAM y los tiles Method 0.
          * Fuerza temporalmente la paleta de las filas 22 y 23 a la Paleta 0 con color 15
            (blanco/brillo máximo garantizado) sobre fondo 0 (negro puro) y restaura tanto
            el mapa de paletas como los 2.048 bytes de tiles originales al cerrar con START.
    """
    chapter_ids = list(chapter_ids or range(1, len(sector_list) + 1))
    if len(chapter_ids) != len(sector_list) or not chapter_ids or chapter_ids != sorted(chapter_ids) or max(chapter_ids) > 99:
        raise ValueError("Mapa de capítulos no válido (máximo 99 capítulos lógicos).")
    code = bytearray()
    labels = {}
    fixups_16 = []

    def emit(b):
        code.extend(b)
    def emit_w(w):
        code.extend(struct.pack(">H", w & 0xFFFF))
    def emit_l(l):
        code.extend(struct.pack(">I", l & 0xFFFFFFFF))
    def mark(name):
        labels[name] = len(code)
    def emit_pc_rel16(opcode_w, target_label, offset_extra=0):
        emit_w(opcode_w)
        pc_base = len(code)
        fixups_16.append((len(code), target_label, pc_base, offset_extra))
        emit_w(0)

    # Cabecera Sub-CPU (0x00..0x23)
    emit(b"MAIN       \x00")
    emit_w(0x0000)
    emit_w(0x0000)
    emit_l(0x00000000)
    pos_total_len = len(code)
    emit_l(0)
    emit_l(0x00000020)
    emit_l(0x00000000)
    emit_w(0x0004)
    emit_w(0x0000)

    mark("StartUserCall0")
    emit(b"\x46\xFC\x20\x00")                  # MOVE #$2000, SR
    emit(b"\x00\x79\x00\x04\x00\xFF\x80\x02")  # ORI.W #4, ($00FF8002).L (1M/1M mode)
    emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
    emit_w(0x4251)                             # CLR.W (A1)

    mark("PlayCurrentVideo")
    emit(b"\x2E\x7C\x00\x01\x00\x00")          # MOVEA.L #$00010000, A7
    emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
    emit_w(0x3011)                             # MOVE.W (A1), D0
    emit_pc_rel16(0x45FA, "SectorList")        # LEA SectorList(PC), A2
    emit(b"\x2E\x32\x00\x00")                  # MOVE.L 0(A2, D0.W), D7
    emit_pc_rel16(0x6600, "HaveSector")        # BNE.W HaveSector
    emit_pc_rel16(0x323A, "LoopFlag")          # MOVE.W LoopFlag(PC), D1
    emit_pc_rel16(0x6700, "SubCPULoop")        # BEQ.W SubCPULoop
    emit_w(0x4251)                             # CLR.W (A1)
    emit_w(0x2E12)                             # MOVE.L (A2), D7
    emit_pc_rel16(0x6700, "SubCPULoop")        # BEQ.W SubCPULoop

    mark("HaveSector")
    mark("WaitBtnRelease")
    emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
    emit(b"\x02\x40\xF0\xF0")                  # ANDI.W #$F0F0, D0
    emit_pc_rel16(0x6600, "WaitBtnRelease")    # BNE.W WaitBtnRelease

    # Callback VBlank: captura el flanco START incluso dentro de FilmTask.
    emit_pc_rel16(0x487A, "PadVBlank")  # PEA PadVBlank(PC), primer parámetro
    # Heap conservado: $10000..$5B000, sin invadir BIOS/stack.
    for val in [ 0x1, 0x20, 0xE000, 0x4, 0x2, 0x4B000, 0x10000]:
        emit_w(0x2F3C)
        emit_l(val)
    emit_w(0x2F07)                             # MOVE.L D7, -(A7)
    emit(b"\x3F\x3C\x00\x00")                  # MOVE.W #0, -(A7) -> FilmOpen
    emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
    emit(b"\xDF\xFC\x00\x00\x00\x26")          # ADDA.L #$26, A7

    emit(b"\x4A\x80")  # FilmOpen: D0=0 éxito, -1 error (binario confirmado)
    emit_pc_rel16(0x6600, "OpenFailed")
    emit_pc_rel16(0x41FA, "OpenFailures")
    emit_w(0x4250)

    mark("TaskLoop")
    if enable_osd:
        emit_pc_rel16(0x303A, "MenuPending")
        emit_pc_rel16(0x6600, "OpenMenuMode")
        emit_pc_rel16(0x6100, "ReadPad")
        if allow_skip:
            # Si allow_skip también está activo, permitir saltar pista con A/B/C
            emit(b"\x02\x40\x70\x00")          # ANDI.W #$7000, D0 (A, B, C)
            emit_pc_rel16(0x6600, "FinishLogicalChapter")
    elif allow_skip:
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x02\x40\xF0\xF0")              # ANDI.W #$F0F0, D0
        emit_pc_rel16(0x6600, "FinishLogicalChapter") # BNE.W FinishCurrentVideo

    mark("DoFilmTask")
    emit(b"\x3F\x3C\x00\x01")                  # MOVE.W #1, -(A7) -> FilmTask
    emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
    emit_w(0x548F)                             # ADDQ.L #2, A7
    emit_pc_rel16(0x6C00, "TaskLoop")          # BGE.W TaskLoop
    # La API nativa devuelve -1 en EOF y -2 en error CDC/GetData.
    # Un error no debe avanzar al siguiente archivo y omitir película.
    emit(b"\x0C\x80\xFF\xFF\xFF\xFF")
    emit_pc_rel16(0x6600, "RecoverReadError")
    emit_pc_rel16(0x6000, "FinishCurrentVideo")  # EOF no es un fallo de FilmOpen

    mark("RecoverReadError")
    # También se entra desde avance rápido: restaurar frecuencia y silenciar
    # mientras se decide si reabrir o mostrar el error, sin dejar audio a 4x.
    emit(b"\x20\x7C\x00\xFF\x00\x00")
    emit(b"\x11\x7C\x00\xFF\x00\x11")
    emit(b"\x11\x7C\x00\xC0\x00\x0F")
    emit(b"\x11\x7C\x00\x04\x00\x07")
    emit(b"\x11\x7C\x00\xFF\x00\x01")
    emit_pc_rel16(0x41FA, "ReadFailures")
    emit_w(0x5250)  # ADDQ.W #1,(A0)
    emit(b"\x0C\x50\x00\x03")  # CMPI.W #3,(A0)
    emit_pc_rel16(0x6D00, "RetryCurrentVideo")
    emit_pc_rel16(0x41FA, "ReadErrorPending")
    emit(b"\x30\xBC\x00\x01")
    if enable_osd:
        emit_pc_rel16(0x6000, "OpenMenuMode")
    else:
        emit_pc_rel16(0x6000, "ReadErrorWait")

    mark("RetryCurrentVideo")
    emit(b"\x3F\x3C\x00\x02")  # cerrar sólo un FilmOpen válido
    emit_pc_rel16(0x4EBA, "Cinepak")
    emit_w(0x548F)
    # Esperar medio segundo aproximadamente antes de reabrir el MISMO CIN.
    emit_w(0x2F3C); emit_l(30)
    emit_pc_rel16(0x4EBA, "Cinepak", 0x095C)
    emit_w(0x588F)
    emit_pc_rel16(0x6000, "PlayCurrentVideo")

    mark("ReadErrorWait")
    emit(b"\x13\xFC\x00\xFF\x00\xFF\x00\x11")
    emit_pc_rel16(0x6100, "ReadPad")
    emit(b"\x02\x40\xF0\xF0")
    emit_pc_rel16(0x6700, "ReadErrorWait")
    emit_pc_rel16(0x6100, "ResetReadRecovery")
    emit_pc_rel16(0x6000, "RetryCurrentVideo")

    mark("OpenFailed")
    # FilmOpen ya contiene cuatro intentos. No llamar a FilmTask ni enviar
    # FilmClose: un Open fallido todavía no envió el comando 3 al Main.
    emit_pc_rel16(0x41FA, "OpenFailures")
    emit_w(0x5250)
    emit_w(0x3010)
    emit(b"\x0C\x40")
    emit_w(2)
    emit_pc_rel16(0x6C00, "OpenErrorWait")
    emit_pc_rel16(0x6000, "PlayCurrentVideo")

    mark("OpenErrorWait")
    # No hay contexto Film abierto: no enviar FilmClose al Main.
    # Mantener el índice para reintentar con START/A/B/C, sin saltar contenido.
    emit_pc_rel16(0x6100, "ReadPad")
    emit(b"\x02\x40\xF0\xF0")
    emit_pc_rel16(0x6700, "OpenErrorWait")
    emit_pc_rel16(0x41FA, "OpenFailures")
    emit_w(0x4250)
    emit_pc_rel16(0x6000, "PlayCurrentVideo")

    mark("FinishLogicalChapter")
    emit_pc_rel16(0x43FA, "CurOffset")
    emit_w(0x3011)
    emit_pc_rel16(0x45FA, "NextOffsets")
    emit(b"\x32\xB2\x00\x00")  # MOVE.W (A2,D0.W),(A1)
    emit_w(0x5951)  # compensar el incremento común de cuatro
    mark("FinishCurrentVideo")
    emit_pc_rel16(0x6100, "ResetReadRecovery")
    emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
    emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
    emit_w(0x548F)                             # ADDQ.L #2, A7
    emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
    emit(b"\x58\x51")                          # ADDQ.W #4, (A1)
    emit_pc_rel16(0x6000, "PlayCurrentVideo")  # BRA.W PlayCurrentVideo

    mark("ResetReadRecovery")
    emit_pc_rel16(0x41FA, "ReadFailures")
    emit_w(0x4250)
    emit_pc_rel16(0x41FA, "ReadErrorPending")
    emit_w(0x4250)
    emit_w(0x4E75)

    mark("SubCPULoop")
    emit_w(0x4E71)
    emit_w(0x4E71)
    emit_w(0x60FA)

    # Subrutina ReadPad (v7.1): lee el registro de comunicación $FF801A y pliega el byte bajo sobre el alto,
    # de modo que START/A/B/C/Izq/Der se detectan tanto si el Main-CPU deja el mando 1 en el byte alto
    # como en el bajo. D0 = pad | (pad << 8); destruye D1.
    mark("ReadPad")
    emit(b"\x30\x39\x00\xFF\x80\x1A")          # MOVE.W ($00FF801A).L, D0
    emit(b"\x32\x00")                              # MOVE.W D0, D1
    emit(b"\xE1\x49")                              # LSL.W #8, D1
    emit(b"\x80\x41")                              # OR.W D1, D0
    emit_w(0x4E75)                                 # RTS

    mark("PadVBlank")
    emit(b"\x48\xE7\xC0\x80")  # preservar D0-D1/A0
    emit_pc_rel16(0x6100, "ReadPad")
    emit(b"\x02\x40\x80\x00")
    emit_pc_rel16(0x41FA, "LastStart")
    emit_w(0x3210)
    emit_w(0x3080)
    emit_w(0x4A40)
    emit_pc_rel16(0x6700, "PadVBlankDone")
    emit_w(0x4A41)
    emit_pc_rel16(0x6600, "PadVBlankDone")
    emit_pc_rel16(0x41FA, "MenuPending")
    emit_w(0x30BC)
    emit_w(1)
    mark("PadVBlankDone")
    emit(b"\x4C\xDF\x01\x03\x4E\x75")

    if enable_osd:
        # Subrutina DisplaySubWordRAM:
        # Envía el banco actual de 1M ($000C0000) al Main-CPU para dibujarlo por DMA en el VDP,
        # espera 2 ticks de VBlank para que DrawFrame + CinepakVint terminen, y devuelve el
        # mismo banco de 1M al Sub-CPU para no alterar la paridad de bancos ni Method 0.
        mark("DisplaySubWordRAM")
        emit_pc_rel16(0x4EBA, "Cinepak", 0x0DEE)   # JSR SwapMeg(PC)
        emit(b"\x70\x02")                          # MOVEQ #2, D0
        emit(b"\x2F\x00")                          # MOVE.L D0, -(A7)
        emit_pc_rel16(0x4EBA, "Cinepak", 0x1122)   # JSR SendCommandToMain(PC)
        emit(b"\x58\x4F")                          # ADDQ.W #4, A7
        emit(b"\x70\x02")                          # MOVEQ #2, D0
        emit(b"\x2F\x00")                          # MOVE.L D0, -(A7)
        emit_pc_rel16(0x4EBA, "Cinepak", 0x095C)   # JSR Wait(PC)
        emit(b"\x58\x4F")                          # ADDQ.W #4, A7
        emit_pc_rel16(0x4EBA, "Cinepak", 0x0DEE)   # JSR SwapMeg(PC)
        emit_w(0x4E75)                             # RTS

        # Subrutina Render32CharsToA1:
        # Expande al vuelo glifos 1bpp de 8 bytes (MenuTileCharset) a tiles 4bpp de 32 bytes
        # (fg=15 blanco $F, bg=0 negro $0) ahorrando más de 700 bytes en SP.BIN.
        # Entrada: A0 = puntero a 32 bytes de índices de caracteres, A1 = destino en Word RAM
        # Salida: A1 avanza 1024 bytes (32 tiles * 32 bytes)
        mark("Render32CharsToA1")
        emit_pc_rel16(0x45FA, "MenuTileCharset")   # LEA MenuTileCharset(PC), A2
        emit(b"\x72\x1F")                          # MOVEQ #31, D1 (32 caracteres)
        mark("RenderCharLoop")
        emit(b"\x70\x00")                          # MOVEQ #0, D0
        emit(b"\x10\x18")                          # MOVE.B (A0)+, D0
        emit(b"\xE7\x40")                          # LSL.W #3, D0 (char_idx * 8 bytes)
        emit(b"\x47\xF2\x00\x00")                  # LEA 0(A2, D0.W), A3
        emit(b"\x74\x07")                          # MOVEQ #7, D2 (8 filas por glifo)
        mark("RenderRowLoop")
        emit(b"\x10\x1B")                          # MOVE.B (A3)+, D0
        emit(b"\x76\x07")                          # MOVEQ #7, D3 (8 píxeles por fila)
        mark("RenderBitLoop")
        emit(b"\xE9\x8C")                          # LSL.L #4, D4
        emit(b"\xD0\x00")                          # ADD.B D0, D0 (MSB -> Carry)
        emit(b"\x55\xC5")                          # SCS D5 ($FF si bit=1, $00 si bit=0)
        emit(b"\x02\x05\x00\x0F")                  # ANDI.B #$0F, D5 ($0F blanco o $00 negro)
        emit(b"\x88\x05")                          # OR.B D5, D4
        emit_pc_rel16(0x51CB, "RenderBitLoop")     # DBF D3, RenderBitLoop
        emit(b"\x22\xC4")                          # MOVE.L D4, (A1)+
        emit_pc_rel16(0x51CA, "RenderRowLoop")     # DBF D2, RenderRowLoop
        emit_pc_rel16(0x51C9, "RenderCharLoop")    # DBF D1, RenderCharLoop
        emit_w(0x4E75)                             # RTS

        # Subrutina CaptureCurrentFrameBackup:
        # Calcula TileBase y Row22Target en $000C0000 y guarda los 16 bytes del mapa de paletas
        # en $0000E000 + los 2048 bytes de tiles originales de las filas 22 y 23 en $0000E010.
        mark("CaptureCurrentFrameBackup")
        emit_pc_rel16(0x41FA, "SavedMenuColor")
        emit(b"\x30\xB9\x00\x0C\x00\x2A")
        emit(b"\x12\x39\x00\x0C\x00\x03")          # MOVE.B ($000C0003).L, D1
        emit(b"\x02\x41\x00\x03")                  # ANDI.W #3, D1
        emit(b"\x0C\x41\x00\x03")                  # CMPI.W #3, D1
        emit_pc_rel16(0x6700, "Pal4Mode")          # BEQ.W Pal4Mode
        emit(b"\x0C\x41\x00\x01")                  # CMPI.W #1, D1
        emit_pc_rel16(0x6700, "Pal2Mode")          # BEQ.W Pal2Mode
        emit(b"\x22\x3C\x00\x0C\x00\x2C")          # MOVE.L #$000C002C, D1 (1 pal)
        emit_pc_rel16(0x6000, "GotTileBase")       # BRA.W GotTileBase

        mark("Pal2Mode")
        emit(b"\x22\x3C\x00\x0C\x01\x0C")          # MOVE.L #$000C010C, D1 (2 pal: 12 + 64 + 192 = $10C)
        emit_pc_rel16(0x6000, "GotTileBase")       # BRA.W GotTileBase

        mark("Pal4Mode")
        emit(b"\x22\x3C\x00\x0C\x01\x4C")          # MOVE.L #$000C014C, D1 (4 pal: 12 + 128 + 192 = $14C)

        mark("GotTileBase")
        emit_pc_rel16(0x41FA, "TileBaseAddr")      # LEA TileBaseAddr(PC), A0
        emit(b"\x20\x81")                          # MOVE.L D1, (A0)

        # Guardar los 16 bytes de selección de paleta de las filas 22 y 23 (en TileBase - 16)
        emit(b"\x20\x41")                          # MOVEA.L D1, A0
        emit(b"\x41\xE8\xFF\xF0")                  # LEA -16(A0), A0
        emit(b"\x22\x7C\x00\x00\xE0\x00")          # MOVEA.L #$0000E000, A1 (BackupPalMap en PRG-RAM)
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+

        # Calcular Row22Target = TileBase + $5800 (704 * 32)
        emit(b"\x06\x81\x00\x00\x58\x00")          # ADDI.L #$5800, D1
        emit_pc_rel16(0x41FA, "Row22Target")       # LEA Row22Target(PC), A0
        emit(b"\x20\x81")                          # MOVE.L D1, (A0)

        # Guardar los 2048 bytes de tiles originales de las filas 22 y 23 en $0000E010
        emit(b"\x20\x41")                          # MOVEA.L D1, A0
        emit(b"\x22\x7C\x00\x00\xE0\x10")          # MOVEA.L #$0000E010, A1 (BackupBuffer en PRG-RAM)
        emit(b"\x30\x3C\x01\xFF")                  # MOVE.W #511, D0
        mark("BackupLoop")
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit_pc_rel16(0x51C8, "BackupLoop")        # DBF D0, BackupLoop
        emit_w(0x4E75)                             # RTS

        # Subrutina RestoreCurrentFrameBackup:
        # Restaura los 16 bytes del mapa de paletas y los 2048 bytes de las filas 22 y 23.
        mark("RestoreCurrentFrameBackup")
        emit_pc_rel16(0x41FA, "SavedMenuColor")
        emit(b"\x33\xD0\x00\x0C\x00\x2A")
        emit(b"\x10\x39\x00\x0C\x00\x03")          # MOVE.B ($000C0003).L, D0
        emit(b"\x02\x40\x00\x03")                  # ANDI.W #3, D0
        emit_pc_rel16(0x6700, "SkipPalMapRestore") # BEQ.W SkipPalMapRestore
        emit(b"\x20\x7C\x00\x00\xE0\x00")          # MOVEA.L #$0000E000, A0 (BackupPalMap)
        emit_pc_rel16(0x227A, "TileBaseAddr")      # MOVEA.L TileBaseAddr(PC), A1
        emit(b"\x43\xE9\xFF\xF0")                  # LEA -16(A1), A1
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        mark("SkipPalMapRestore")
        emit(b"\x20\x7C\x00\x00\xE0\x10")          # MOVEA.L #$0000E010, A0 (BackupBuffer)
        emit_pc_rel16(0x227A, "Row22Target")       # MOVEA.L Row22Target(PC), A1
        emit(b"\x30\x3C\x01\xFF")                  # MOVE.W #511, D0
        mark("RestoreLoop")
        emit(b"\x22\xD8")                          # MOVE.L (A0)+, (A1)+
        emit_pc_rel16(0x51C8, "RestoreLoop")       # DBF D0, RestoreLoop
        emit_w(0x4E75)                             # RTS

        mark("OpenMenuMode")
        # Verificar que hay un fotograma 'SM' válido en $000C0000 antes de abrir el menú
        emit(b"\x0C\x79\x53\x4D\x00\x0C\x00\x00")  # CMPI.W #'SM', ($000C0000).L
        emit_pc_rel16(0x6600, "DoFilmTask")  # seguir decodificando hasta SM válido
        emit_pc_rel16(0x41FA, "MenuPending")
        emit_w(0x4250)
        # Pausar antes de esperar la liberación: mantener START no desincroniza.
        emit(b"\x13\xFC\x00\xFF\x00\xFF\x00\x11")

        # 1) Esperar a que se suelte START
        mark("WaitStartRel1")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x08\x00\x00\x0F")                  # BTST #15, D0
        emit_pc_rel16(0x6600, "WaitStartRel1")     # BNE.W WaitStartRel1

        # 2) Pausar audio en el chip Ricoh RF5C164
        emit(b"\x20\x7C\x00\xFF\x00\x00")          # MOVEA.L #$00FF0000, A0
        emit(b"\x11\x7C\x00\xFF\x00\x11")          # MOVE.B #$FF, $11(A0)

        # 3) Hacer copia de seguridad de las filas 22 y 23 del fotograma actual
        emit_pc_rel16(0x4EBA, "CaptureCurrentFrameBackup")

        # 4) Opción seleccionada por defecto = 2 (PLAY)
        emit_pc_rel16(0x41FA, "SelectedOption")    # LEA SelectedOption(PC), A0
        emit(b"\x30\x3C\x00\x02")                  # MOVE.W #2, (A0)

        mark("RedrawMenu")
        emit(b"\x33\xFC\x0E\xEE\x00\x0C\x00\x2A")
        # Si el fotograma tiene tabla de selección de paleta, asignar Paleta 0 a las filas 22 y 23
        emit(b"\x10\x39\x00\x0C\x00\x03")          # MOVE.B ($000C0003).L, D0
        emit(b"\x02\x40\x00\x03")                  # ANDI.W #3, D0
        emit_pc_rel16(0x6700, "SkipPalMapZero")    # BEQ.W SkipPalMapZero
        emit_pc_rel16(0x207A, "TileBaseAddr")      # MOVEA.L TileBaseAddr(PC), A0
        emit(b"\x41\xE8\xFF\xF0")                  # LEA -16(A0), A0
        emit(b"\x42\x98")                          # CLR.L (A0)+
        emit(b"\x42\x98")                          # CLR.L (A0)+
        emit(b"\x42\x98")                          # CLR.L (A0)+
        emit(b"\x42\x98")                          # CLR.L (A0)+
        mark("SkipPalMapZero")

        # Renderizar los 32 tiles de la fila 22 correspondientes a SelectedOption
        emit_pc_rel16(0x41FA, "SelectedOption")    # LEA SelectedOption(PC), A0
        emit(b"\x30\x10")                          # MOVE.W (A0), D0
        emit(b"\xEB\x40")                          # LSL.W #5, D0 (SelectedOption * 32)
        emit_pc_rel16(0x41FA, "MenuRow22Table")    # LEA MenuRow22Table(PC), A0
        emit(b"\xD0\xC0")                          # ADDA.W D0, A0
        emit_pc_rel16(0x227A, "Row22Target")       # MOVEA.L Row22Target(PC), A1
        emit_pc_rel16(0x4EBA, "Render32CharsToA1") # JSR Render32CharsToA1(PC)

        # Actualizar contador dinámico de capítulo actual (CAP XX/YY) en MenuRow23Data+5 y +6
        emit_pc_rel16(0x41FA, "CurOffset")         # LEA CurOffset(PC), A0
        emit(b"\x30\x10")                          # MOVE.W (A0), D0
        emit(b"\xE4\x48")                          # LSR.W #2, D0 (índice 0-based)
        emit(b"\xE5\x48")  # índice *4 para ChapterNumbers
        emit_pc_rel16(0x45FA, "ChapterNumbers")
        emit(b"\x30\x32\x00\x00")
        emit(b"\x48\xC0")                          # EXT.L D0
        emit(b"\x80\xFC\x00\x0A")                  # DIVU #10, D0 (D0.W=decenas, D0 high=unidades)
        emit_pc_rel16(0x41FA, "MenuRow23Data")     # LEA MenuRow23Data(PC), A0
        emit(b"\x11\x40\x00\x05")                  # MOVE.B D0, 5(A0) (decenas 0..9)
        emit(b"\x48\x40")                          # SWAP D0
        emit(b"\x11\x40\x00\x06")                  # MOVE.B D0, 6(A0) (unidades 0..9)

        # Renderizar los 32 tiles de la fila 23 (CAP XX/YY + controles; A1 ya apunta a la fila 23)
        emit_pc_rel16(0x303A, "ReadErrorPending")
        emit_pc_rel16(0x6700, "RenderChapterRow")
        emit_pc_rel16(0x41FA, "MenuErrorRow23Data")
        mark("RenderChapterRow")
        emit_pc_rel16(0x4EBA, "Render32CharsToA1") # JSR Render32CharsToA1(PC)

        # Mostrar en pantalla manteniendo el banco 1M en el Sub-CPU
        emit_pc_rel16(0x4EBA, "DisplaySubWordRAM")

        mark("MenuInputLoop")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)

        # START (Bit 15) -> Cerrar menú y continuar reproducción
        emit(b"\x08\x00\x00\x0F")                  # BTST #15, D0
        emit_pc_rel16(0x6600, "CloseMenuAndResume")

        # Botón B (Bit 12) -> Cerrar menú y continuar reproducción
        emit(b"\x08\x00\x00\x0C")                  # BTST #12, D0
        emit_pc_rel16(0x6600, "CloseMenuAndResume")

        # Cruceta IZQUIERDA (Bit 10)
        emit(b"\x08\x00\x00\x0A")                  # BTST #10, D0
        emit_pc_rel16(0x6600, "MenuDpadLeft")

        # Cruceta DERECHA (Bit 11)
        emit(b"\x08\x00\x00\x0B")                  # BTST #11, D0
        emit_pc_rel16(0x6600, "MenuDpadRight")

        # Botón A (Bit 14) o Botón C (Bit 13) -> Ejecutar opción seleccionada
        emit(b"\x08\x00\x00\x0E")                  # BTST #14, D0
        emit_pc_rel16(0x6600, "MenuExecuteAction")
        emit(b"\x08\x00\x00\x0D")                  # BTST #13, D0
        emit_pc_rel16(0x6600, "MenuExecuteAction")

        emit(b"\x30\x3C\x04\x00")                  # MOVE.W #1024, D0
        mark("InputDelayLoop")
        emit_w(0x4E71)                             # NOP
        emit_pc_rel16(0x51C8, "InputDelayLoop")    # DBF D0, InputDelayLoop
        emit_pc_rel16(0x6000, "MenuInputLoop")     # BRA.W MenuInputLoop

        mark("MenuDpadLeft")
        mark("WaitLeftUp")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x08\x00\x00\x0A")                  # BTST #10, D0
        emit_pc_rel16(0x6600, "WaitLeftUp")        # BNE.W WaitLeftUp
        emit_pc_rel16(0x41FA, "SelectedOption")    # LEA SelectedOption(PC), A0
        emit(b"\x30\x10")                          # MOVE.W (A0), D0
        emit(b"\x53\x40")                          # SUBQ.W #1, D0
        emit_pc_rel16(0x6C00, "SaveOptLeft")       # BGE.W SaveOptLeft
        emit(b"\x70\x06")                          # MOVEQ #6, D0
        mark("SaveOptLeft")
        emit(b"\x30\x80")                          # MOVE.W D0, (A0)
        emit_pc_rel16(0x6000, "RedrawMenu")        # BRA.W RedrawMenu

        mark("MenuDpadRight")
        mark("WaitRightUp")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x08\x00\x00\x0B")                  # BTST #11, D0
        emit_pc_rel16(0x6600, "WaitRightUp")       # BNE.W WaitRightUp
        emit_pc_rel16(0x41FA, "SelectedOption")    # LEA SelectedOption(PC), A0
        emit(b"\x30\x10")                          # MOVE.W (A0), D0
        emit(b"\x52\x40")                          # ADDQ.W #1, D0
        emit(b"\x0C\x40\x00\x06")                  # CMPI.W #6, D0
        emit_pc_rel16(0x6F00, "SaveOptRight")      # BLE.W SaveOptRight
        emit(b"\x70\x00")                          # MOVEQ #0, D0
        mark("SaveOptRight")
        emit(b"\x30\x80")                          # MOVE.W D0, (A0)
        emit_pc_rel16(0x6000, "RedrawMenu")        # BRA.W RedrawMenu

        mark("MenuExecuteAction")
        mark("WaitActionBtnUp")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x02\x40\x60\x00")                  # ANDI.W #$6000, D0 (A o C)
        emit_pc_rel16(0x6600, "WaitActionBtnUp")   # BNE.W WaitActionBtnUp
        emit_pc_rel16(0x41FA, "SelectedOption")    # LEA SelectedOption(PC), A0
        emit(b"\x30\x10")                          # MOVE.W (A0), D0
        emit(b"\x0C\x40\x00\x00")                  # CMPI.W #0, D0 (ANT)
        emit_pc_rel16(0x6700, "ActionPrevTrack")
        emit(b"\x0C\x40\x00\x01")                  # CMPI.W #1, D0 (REW)
        emit_pc_rel16(0x6700, "ActionRewind")
        emit(b"\x0C\x40\x00\x02")                  # CMPI.W #2, D0 (PLAY)
        emit_pc_rel16(0x6700, "CloseMenuAndResume")
        emit(b"\x0C\x40\x00\x03")                  # CMPI.W #3, D0 (PAUSA)
        emit_pc_rel16(0x6700, "MenuInputLoop")
        emit(b"\x0C\x40\x00\x04")                  # CMPI.W #4, D0 (FWD)
        emit_pc_rel16(0x6700, "ActionFastForward")
        emit(b"\x0C\x40\x00\x05")                  # CMPI.W #5, D0 (SIG)
        emit_pc_rel16(0x6700, "ActionNextTrack")
        emit(b"\x0C\x40\x00\x06")                  # CMPI.W #6, D0 (STOP)
        emit_pc_rel16(0x6700, "ActionStop")
        emit_pc_rel16(0x6000, "MenuInputLoop")     # BRA.W MenuInputLoop

        mark("CloseMenuAndResume")
        emit_pc_rel16(0x303A, "ReadErrorPending")
        emit_pc_rel16(0x6700, "ResumeHealthyVideo")
        emit_pc_rel16(0x6100, "ResetReadRecovery")
        emit_pc_rel16(0x6000, "RetryCurrentVideo")
        mark("ResumeHealthyVideo")
        mark("WaitCloseRelease")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x02\x40\xF0\xF0")                  # ANDI.W #$F0F0, D0
        emit_pc_rel16(0x6600, "WaitCloseRelease")

        # Restaurar los 16 bytes del mapa de paletas y los 2048 bytes de las filas 22 y 23
        emit_pc_rel16(0x4EBA, "RestoreCurrentFrameBackup")

        # Transferir el fotograma 100% limpio de nuevo al VDP
        emit_pc_rel16(0x4EBA, "DisplaySubWordRAM")

        # Reactivar el Canal 1 del chip Ricoh RF5C164 para reanudar el audio y el reloj AudioTime
        emit(b"\x20\x7C\x00\xFF\x00\x00")          # MOVEA.L #$00FF0000, A0
        emit(b"\x11\x7C\x00\xFE\x00\x11")          # MOVE.B #$FE, $11(A0)

        emit_pc_rel16(0x41FA, "MenuPending")
        emit_w(0x4250)
        emit_pc_rel16(0x6000, "TaskLoop")

        mark("ActionNextTrack")
        emit_pc_rel16(0x6100, "ResetReadRecovery")
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
        emit_w(0x3011)
        emit_pc_rel16(0x45FA, "NextOffsets")
        emit(b"\x32\xB2\x00\x00")
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        mark("ActionPrevTrack")
        emit_pc_rel16(0x6100, "ResetReadRecovery")
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
        emit_w(0x3011)
        emit_pc_rel16(0x45FA, "PrevOffsets")
        emit(b"\x32\xB2\x00\x00")
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        mark("ActionRewind")
        emit_pc_rel16(0x6100, "ResetReadRecovery")
        emit_pc_rel16(0x43FA, "CurOffset")
        emit_w(0x3011)
        emit_pc_rel16(0x45FA, "StartOffsets")
        emit(b"\x32\xB2\x00\x00")
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        mark("ActionFastForward")
        emit_pc_rel16(0x303A, "ReadErrorPending")
        emit_pc_rel16(0x6700, "FastForwardHealthyVideo")
        emit_pc_rel16(0x6100, "ResetReadRecovery")
        emit_pc_rel16(0x6000, "RetryCurrentVideo")
        mark("FastForwardHealthyVideo")
        # 1) Restaurar primero los tiles originales de las filas 22 y 23 para que Method 0
        #    no arrastre el gráfico del menú durante el avance rápido
        emit_pc_rel16(0x4EBA, "RestoreCurrentFrameBackup")

        # 2) Acelerar temporalmente el reloj del chip RF5C164 a 4x (FDH = $10) con volumen 0
        #    y Canal 1 activo ($FE) para que FilmTask avance 45 entradas (~3s) en tiempo real
        emit(b"\x20\x7C\x00\xFF\x00\x00")          # MOVEA.L #$00FF0000, A0
        emit(b"\x11\x7C\x00\xC0\x00\x0F")          # MOVE.B #$C0, $0F(A0) (Canal 1)
        emit(b"\x42\x28\x00\x01")                  # CLR.B $01(A0)        (Volumen = 0 silencioso)
        emit(b"\x11\x7C\x00\x10\x00\x07")          # MOVE.B #$10, $07(A0) (FDH = $10 -> velocidad 4x)
        emit(b"\x11\x7C\x00\xFE\x00\x11")          # MOVE.B #$FE, $11(A0) (Activar Canal 1)

        emit_pc_rel16(0x6100, "ReadAudioTime")
        emit_w(0x2A00)
        emit_w(0x0685)
        emit_l(round(6*BASE_RATE*PCM_RATE_HW/PCM_RATE_SUBPLAY))
        emit(b"\x3C\x3C\x7F\xFF")
        mark("FFLoop")
        emit(b"\x3F\x3C\x00\x01")                  # MOVE.W #1, -(A7) -> FilmTask
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit(b"\x4A\x80")                          # TST.L D0
        emit(b"\x0C\x80\xFF\xFF\xFF\xFE")  # -2: error de lectura
        emit_pc_rel16(0x6700, "RecoverReadError")
        emit(b"\x4A\x80")
        emit_pc_rel16(0x6D00, "FFReachedEnd")      # BLT.W FFReachedEnd
        emit_pc_rel16(0x6100, "ReadAudioTime")
        emit_w(0xB085)
        emit_pc_rel16(0x6C00, "FFFinished")
        emit_pc_rel16(0x51CE, "FFLoop")
        mark("FFFinished")

        # 3) Volver a pausar el chip RF5C164 y restaurar su frecuencia ($04) y volumen al 100% ($FF)
        emit(b"\x20\x7C\x00\xFF\x00\x00")          # MOVEA.L #$00FF0000, A0
        emit(b"\x11\x7C\x00\xFF\x00\x11")          # MOVE.B #$FF, $11(A0) (Pausar Canal 1)
        emit(b"\x11\x7C\x00\xC0\x00\x0F")          # MOVE.B #$C0, $0F(A0)
        emit(b"\x11\x7C\x00\x04\x00\x07")          # MOVE.B #$04, $07(A0) (FDH = $04 -> 16.276 Hz)
        emit(b"\x11\x7C\x00\xFF\x00\x01")          # MOVE.B #$FF, $01(A0) (Volumen 100%)

        # 4) Capturar el nuevo fotograma alcanzado en BackupBuffer y redibujar el menú encima
        emit_pc_rel16(0x4EBA, "CaptureCurrentFrameBackup")
        emit_pc_rel16(0x6000, "RedrawMenu")

        mark("ReadAudioTime")
        emit_w(0x2F3C)
        emit_l(BASE_RATE)
        emit_pc_rel16(0x487A, "Cinepak", 0x70)
        emit_pc_rel16(0x4EBA, "Cinepak", 0x1684)
        emit_w(0x508F)
        emit_w(0x4E75)

        mark("FFReachedEnd")
        emit(b"\x20\x7C\x00\xFF\x00\x00")          # MOVEA.L #$00FF0000, A0
        emit(b"\x11\x7C\x00\xFF\x00\x11")          # MOVE.B #$FF, $11(A0)
        emit(b"\x11\x7C\x00\xC0\x00\x0F")          # MOVE.B #$C0, $0F(A0)
        emit(b"\x11\x7C\x00\x04\x00\x07")          # MOVE.B #$04, $07(A0)
        emit(b"\x11\x7C\x00\xFF\x00\x01")          # MOVE.B #$FF, $01(A0) (Volumen 100%)
        emit_pc_rel16(0x6000, "ActionNextTrack")

        mark("ActionStop")
        emit_pc_rel16(0x6100, "ResetReadRecovery")
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
        emit_w(0x4251)                             # CLR.W (A1)
        mark("StopIdleLoop")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x02\x40\xE0\x00")                  # ANDI.W #$E000, D0 (START, A o C)
        emit_pc_rel16(0x6700, "StopIdleLoop")
        mark("WaitStopRelease")
        emit_pc_rel16(0x6100, "ReadPad")  # BSR.W ReadPad -> D0 = estado del mando (ambos bytes plegados)
        emit(b"\x02\x40\xF0\xF0")                  # ANDI.W #$F0F0, D0
        emit_pc_rel16(0x6600, "WaitStopRelease")
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        # Variables y tablas compactas del Menú Multimedia (fg=15 blanco brillante, bg=0 negro)
        mark("SelectedOption")
        emit_w(2)
        mark("TileBaseAddr")
        emit_l(0)
        mark("Row22Target")
        emit_l(0)

        row22_variants = [
            "[|<] <<   >   ||   >>   >|   ST ",
            " |< [<<]  >   ||   >>   >|   ST ",
            " |<  <<  [>]  ||   >>   >|   ST ",
            " |<  <<   >  [||]  >>   >|   ST ",
            " |<  <<   >   ||  [>>]  >|   ST ",
            " |<  <<   >   ||   >>  [>|]  ST ",
            " |<  <<   >   ||   >>   >|  [ST]",
        ]
        tot_chaps = max(chapter_ids)
        row23_str = f" CAP 01/{tot_chaps:02d}  <>:MOVER START:SALIR"
        error_row = " ERROR CD - START:REINTENTAR"

        # Fijar '0'..'9' en los índices 0..9 para actualización directa de CAP XX/YY en ensamblador
        charset_chars = list("0123456789")
        for s in row22_variants + [row23_str, error_row]:
            for ch in s[:32].ljust(32):
                if ch not in charset_chars:
                    charset_chars.append(ch)

        mark("MenuTileCharset")
        for ch in charset_chars:
            g_idx = max(0, min(95, ord(ch) - 32))
            emit(MAINFONT_BIN[g_idx * 8 : (g_idx + 1) * 8])

        mark("MenuRow22Table")
        for v in row22_variants:
            v32 = v[:32].ljust(32)
            emit(bytes(charset_chars.index(ch) for ch in v32))

        mark("MenuRow23Data")
        r32 = row23_str[:32].ljust(32)
        emit(bytes(charset_chars.index(ch) for ch in r32))
        mark("MenuErrorRow23Data")
        emit(bytes(charset_chars.index(ch) for ch in error_row[:32].ljust(32)))
        if len(code) % 2 == 1:
            emit(b"\x00")

    mark("SavedMenuColor")
    emit_w(0)
    mark("OpenFailures")
    emit_w(0)
    mark("ReadFailures")
    emit_w(0)
    mark("ReadErrorPending")
    emit_w(0)
    mark("MenuPending")
    emit_w(0)
    mark("LastStart")
    emit_w(0)
    for table_name in ("ChapterNumbers", "NextOffsets", "PrevOffsets", "StartOffsets"):
        mark(table_name)
        for i, chap in enumerate(chapter_ids):
            if table_name == "ChapterNumbers":
                val = chap
            elif table_name == "NextOffsets":
                val = next((j * 4 for j in range(i + 1, len(chapter_ids)) if chapter_ids[j] != chap), len(chapter_ids) * 4)
            elif table_name == "PrevOffsets":
                val = next((j * 4 for j, c in enumerate(chapter_ids) if c == max(1, chap - 1)), 0)
            else:
                val = chapter_ids.index(chap)*4
            emit_w(val)
            emit_w(0)
    mark("CurOffset")
    emit_w(0)
    mark("LoopFlag")
    emit_w(1 if loop_flag else 0)
    mark("SectorList")
    for s in sector_list:
        emit_l(s)
    emit_l(0)

    mark("Cinepak")
    emit(subplay_bytes)

    for pos, target, pc_base, offset_extra in fixups_16:
        disp = (labels[target] + offset_extra) - pc_base
        assert -32768 <= disp <= 32767, f"Displacement out of range for {target}: {disp}"
        struct.pack_into(">h", code, pos, disp)

    struct.pack_into(">I", code, pos_total_len, len(code))
    if debug_symbols is not None:
        debug_symbols.update(labels)
    return bytes(code)


def make_dir_record(sector, size, name_bytes, is_dir=False, date_bytes=b"\x7e\x0b\x03\x10\x00\x00\x00"):
    """Crea un registro de directorio ISO 9660 / Joliet."""
    name_len = len(name_bytes)
    pad_len = 1 if (name_len % 2 == 0) else 0
    rec_len = 33 + name_len + pad_len
    flags = 0x02 if is_dir else 0x00
    rec = bytearray(rec_len)
    rec[0] = rec_len
    rec[1] = 0
    struct.pack_into("<I", rec, 2, sector)
    struct.pack_into(">I", rec, 6, sector)
    struct.pack_into("<I", rec, 10, size)
    struct.pack_into(">I", rec, 14, size)
    rec[18:25] = date_bytes
    rec[25] = flags
    rec[26] = 0
    rec[27] = 0
    struct.pack_into("<H", rec, 28, 1)
    struct.pack_into(">H", rec, 30, 1)
    rec[32] = name_len
    rec[33 : 33 + name_len] = name_bytes
    return bytes(rec)


def build_megacd_disc(
    cin_paths, out_dir, region="E", disc_title="VIDEO FMV MEGA CD",
    loop_playback=True, allow_skip=True, enable_osd=True, chapter_ids=None
):
    """
    Construye la carpeta final con:
      - VIDEO_MEGACD_<REGION>.ISO (Imagen completa Mode 1 / 2048 con Boot Sector de la región elegida)
      - VIDEO_MEGACD_<REGION>.CUE (Hoja CUE para grabación directa o emulación)
      - Archivos_CD/ (Todos los archivos contenidos dentro del CD: VIDEO.CIN / VID001.CIN..., IP.BIN, SP.BIN, etc.)
      - INSTRUCCIONES_DE_USO_Y_GRABACION.txt
    """
    os.makedirs(out_dir, exist_ok=True)
    files_dir = os.path.join(out_dir, "Archivos_CD")
    os.makedirs(files_dir, exist_ok=True)

    region = region.upper()
    if region == "U":
        head_template = bytearray(HEAD_US)
        ip_offset_in_sec0 = 0x200 + 1412  # 0x784
        reg_name = "USA_NTSC"
    elif region == "J":
        head_template = bytearray(HEAD_JP)
        ip_offset_in_sec0 = 0x200 + 1412  # scdmake rellena con pad a 1412 (0x784) en las 3 regiones
        reg_name = "JAP_NTSC"
    else:
        region = "E"
        head_template = bytearray(HEAD_EU)
        ip_offset_in_sec0 = 0x784
        reg_name = "PAL_EUR"

    # Personalizar título del disco en Sector 0 (0x120..0x180)
    clean_title = re.sub(r"[^A-Za-z0-9 _\-]", "", disc_title.upper())[:48].ljust(48)
    title_bytes = clean_title.encode("ascii")
    head_template[0x120:0x150] = title_bytes
    head_template[0x150:0x180] = title_bytes

    # Calcular sectores de inicio para cada archivo .CIN a partir del Sector 25 (0x19)
    cin_names = ["VIDEO.CIN" if len(cin_paths) == 1 else f"VID{i+1:03d}.CIN" for i in range(len(cin_paths))]
    root_sectors = root_directory_sectors(cin_names + ["ABS.TXT", "BIB.TXT", "CPY.TXT", "IP.BIN", "MAINPLAY.BIN", "SP.BIN", "SUBPLAY.BIN"])
    if root_sectors > 1:
        head_template.extend(bytes((root_sectors-1)*4096))
    current_sector = 23 + root_sectors*2
    cin_sectors = []
    file_entries = []

    for idx, cpath in enumerate(cin_paths):
        cname = "VIDEO.CIN" if len(cin_paths) == 1 else f"VID{idx + 1:03d}.CIN"
        csize = os.path.getsize(cpath)
        n_sec = max(1, (csize + 2047) // 2048)
        cin_sectors.append(current_sector)
        file_entries.append((cname, cpath, current_sector, csize))
        # Copiar a Archivos_CD
        dst_cin = os.path.join(files_dir, cname)
        if os.path.abspath(cpath) != os.path.abspath(dst_cin):
            shutil.copy2(cpath, dst_cin)
        current_sector += n_sec

    # Preparar IP.BIN, MAINPLAY.BIN, SP.BIN, SUBPLAY.BIN con parches de sincronización y audio 6.0
    ip_bin = bytearray(IP_BIN)
    mainplay_bin = bytearray(MAINPLAY_BIN)
    subplay_bin = bytearray(SUBPLAY_BIN)

    patch_cinepak_players(ip_bin, mainplay_bin, subplay_bin)
    controller_patch = patch_main_controller(ip_bin, mainplay_bin)
    import json
    with open(os.path.join(out_dir, "Parche_mando_68000.json"), "w", encoding="utf-8") as patch_file:
        json.dump(controller_patch, patch_file, ensure_ascii=False, indent=2)

    # En MAINPLAY.BIN (offset 0x1344) y en IP.BIN (offset 0x30 + 0x1344 = 0x1374),
    # reemplazar BRA.S MAINCODEEnd (60 08) por NOP (4E 71) para que MAINCODE permanezca
    # esperando el siguiente comando $03 de FilmOpen al cambiar de vídeo/capítulo o hacer bucle.
    assert mainplay_bin[0x1344:0x1346] == b"\x60\x08"
    mainplay_bin[0x1344:0x1346] = b"\x4E\x71"
    assert ip_bin[0x1374:0x1376] == b"\x60\x08"
    ip_bin[0x1374:0x1376] = b"\x4E\x71"
    sp_bin = build_multi_sp_bin(
        bytes(subplay_bin), cin_sectors,
        loop_flag=loop_playback, allow_skip=allow_skip, enable_osd=enable_osd, chapter_ids=chapter_ids
    )

    # Inyectar IP.BIN y SP.BIN en los Sectores 0..15 (System Area)
    head_template[ip_offset_in_sec0 : ip_offset_in_sec0 + len(ip_bin)] = ip_bin
    sp_padded_size = max(0x2000, ((len(sp_bin) + 2047) // 2048) * 2048)
    assert 0x2000 + sp_padded_size <= 0x8000, f"SP.BIN excede los primeros 16 sectores ({len(sp_bin)} bytes)"
    head_template[0x2000 : 0x8000] = b"\x00" * (0x8000 - 0x2000)
    head_template[0x2000 : 0x2000 + len(sp_bin)] = sp_bin
    # Actualizar SP Load Size en cabecera SEGADISCSYSTEM (offsets 0x2C y 0x44, igual que scdmake)
    struct.pack_into(">I", head_template, 0x2C, sp_padded_size)
    struct.pack_into(">I", head_template, 0x44, sp_padded_size)

    # Archivos complementarios estándar Mega CD
    abs_txt = f"ABS.TXT;{clean_title.strip()} - CINEPAK FOR SEGA FMV DISC ({reg_name}).\r\n".encode("ascii")
    bib_txt = b"BIB.TXT;SEGA MEGA CD CINEPAK VER 1.2 HARDWARE PLAYER.\r\n"
    cpy_txt = b"CPY.TXT;(C) SEGA MEGA CD FMV STUDIO.\r\n"

    aux_files = [
        ("ABS.TXT", abs_txt),
        ("BIB.TXT", bib_txt),
        ("CPY.TXT", cpy_txt),
        ("IP.BIN", bytes(ip_bin)),
        ("MAINPLAY.BIN", bytes(mainplay_bin)),
        ("SP.BIN", bytes(sp_bin)),
        ("SUBPLAY.BIN", bytes(subplay_bin)),
    ]

    for fname, fbytes in aux_files:
        fpath = os.path.join(files_dir, fname)
        with open(fpath, "wb") as f:
            f.write(fbytes)
        fsize = len(fbytes)
        n_sec = max(1, (fsize + 2047) // 2048)
        file_entries.append((fname, fpath, current_sector, fsize))
        current_sector += n_sec

    pad_sectors = 150
    total_sectors = max(300, current_sector + pad_sectors)
    actual_pad_sectors = total_sectors - current_sector

    # Construir Sector 23 (Directorio ISO 9660) y Sector 24 (Directorio Joliet)
    sec23 = bytearray(2048*root_sectors)
    sec24 = bytearray(2048*root_sectors)

    pos23 = 0
    for dot_b in (b"\x00", b"\x01"):
        r = make_dir_record(23, 2048*root_sectors, dot_b, is_dir=True)
        sec23[pos23 : pos23 + len(r)] = r
        pos23 += len(r)

    pos24 = 0
    for dot_b in (b"\x00", b"\x01"):
        r = make_dir_record(23+root_sectors, 2048*root_sectors, dot_b, is_dir=True)
        sec24[pos24 : pos24 + len(r)] = r
        pos24 += len(r)

    for fname, _, fsec, fsize in sorted(file_entries, key=lambda x: x[0]):
        iso_name = (fname + ";1").encode("ascii")
        r23 = make_dir_record(fsec, fsize, iso_name, is_dir=False)
        joliet_name = fname.encode("utf-16-be")
        r24 = make_dir_record(fsec, fsize, joliet_name, is_dir=False)
        if pos23 % 2048 + len(r23) > 2048:
            pos23 = (pos23+2047)//2048*2048
        if pos24 % 2048 + len(r24) > 2048:
            pos24 = (pos24+2047)//2048*2048
        if pos23 + len(r23) <= len(sec23) and pos24 + len(r24) <= len(sec24):
            sec23[pos23 : pos23 + len(r23)] = r23
            pos23 += len(r23)
            sec24[pos24 : pos24 + len(r24)] = r24
            pos24 += len(r24)

    head_template[23*2048:(23+root_sectors)*2048] = sec23
    head_template[(23+root_sectors)*2048:(23+root_sectors*2)*2048] = sec24
    for descriptor, extent in ((16,23),(17,23+root_sectors)):
        base = descriptor*2048
        struct.pack_into("<I",head_template,base+158,extent)
        struct.pack_into(">I",head_template,base+162,extent)
        struct.pack_into("<I",head_template,base+166,root_sectors*2048)
        struct.pack_into(">I",head_template,base+170,root_sectors*2048)
        for field, endian in ((140,"<"),(144,"<"),(148,">"),(152,">")):
            table = struct.unpack_from(endian+"I",head_template,base+field)[0]
            if table:
                struct.pack_into(endian+"I",head_template,table*2048+2,extent)

    for s in (16, 17):
        base = s * 2048
        struct.pack_into("<I", head_template, base + 80, total_sectors)
        struct.pack_into(">I", head_template, base + 84, total_sectors)

    pvd_base = 16 * 2048
    head_template[pvd_base + 702 : pvd_base + 739] = b"CPY.TXT;1".ljust(37, b" ")
    head_template[pvd_base + 739 : pvd_base + 776] = b"ABS.TXT;1".ljust(37, b" ")
    head_template[pvd_base + 776 : pvd_base + 813] = b"BIB.TXT;1".ljust(37, b" ")

    iso_filename = f"VIDEO_MEGACD_{reg_name}.ISO"
    cue_filename = f"VIDEO_MEGACD_{reg_name}.CUE"
    iso_path = os.path.join(out_dir, iso_filename)
    cue_path = os.path.join(out_dir, cue_filename)

    # Escribir ISO por bloques para soportar imágenes grandes de hasta 700 MB sin usar RAM excesiva
    with open(iso_path, "wb") as out_f:
        out_f.write(head_template)
        for fname, fpath, fsec, fsize in file_entries:
            with open(fpath, "rb") as in_f:
                while True:
                    buf = in_f.read(1024 * 1024)
                    if not buf:
                        break
                    out_f.write(buf)
            rem = fsize % 2048
            if rem != 0:
                out_f.write(b"\x00" * (2048 - rem))
        out_f.write(b"\x00" * (actual_pad_sectors * 2048))

    validate_iso(iso_path, cin_paths)
    if os.path.getsize(iso_path) > CD_CAPACITY_MB*1024*1024:
        raise RuntimeError("La imagen supera la capacidad configurada de 700 MiB; reduce la duración o calidad.")

    with open(cue_path, "w", encoding="ascii") as f:
        f.write(f'FILE "{iso_filename}" BINARY\n')
        f.write("  TRACK 01 MODE1/2048\n")
        f.write("    INDEX 01 00:00:00\n")

    readme_path = os.path.join(out_dir, "INSTRUCCIONES_DE_USO_Y_GRABACION.txt")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(
            "===============================================================================\n"
            f"  DISCO DE VÍDEO FMV PREPARADO PARA SEGA MEGA CD ({reg_name}) - STUDIO 7.0\n"
            "===============================================================================\n\n"
            "1. ARCHIVOS GENERADOS EN ESTA CARPETA\n"
            "-------------------------------------\n"
            f"  * {cue_filename}\n"
            "      Hoja de grabación CUE. Selecciona este archivo en tu grabador de CDs\n"
            "      (o pulsa '💿 GRABAR CD MEGA CD' directamente en el programa con ImgBurn\n"
            "      integrado) o ábrelo en el emulador Kega Fusion.\n\n"
            f"  * {iso_filename}\n"
            f"      Imagen completa del disco ({total_sectors} sectores = {total_sectors * 2048:,} bytes).\n"
            "      Contiene los Sectores de Arranque 0..15 con el código de seguridad\n"
            f"      oficial para la región '{region}', el motor reproductor Cinepak Ver 1.2\n"
            f"      (IP.BIN + SP.BIN) y {len(cin_paths)} bloque(s)/capítulo(s) de vídeo (.CIN).\n\n"
            "  * Archivos_CD\\\n"
            "      Carpeta con todos los archivos individuales que residen dentro del\n"
            "      sistema de archivos ISO 9660 del CD:\n"
            "        - VIDEO.CIN / VID001.CIN... : Vídeos en formato Cinepak for Sega (FILM/SM)\n"
            "          con audio PCM sign-magnitude masterizado en 16 bits sin ruido de fondo\n"
            "          y chip Ricoh RF5C164 al 100% de ganancia hardware ($FF).\n"
            "        - IP.BIN / MAINPLAY.BIN     : Reproductor de vídeo para CPU 68000 principal.\n"
            "        - SP.BIN / SUBPLAY.BIN      : Decodificador Cinepak y audio para Sub-CPU.\n"
            "        - ABS.TXT, BIB.TXT, CPY.TXT : Metadatos estándar de disco Sega Mega CD.\n\n"
            "2. CONTROLES Y MENÚ MULTIMEDIA EN PANTALLA (BOTÓN START)\n"
            "--------------------------------------------------------\n"
            "  * Al encender la Mega CD con el disco dentro, pulsa START en la pantalla\n"
            "    de la BIOS para iniciar la reproducción.\n"
            f"  * Menú Multimedia al pulsar START: {'ACTIVADO' if enable_osd else 'DESACTIVADO'}\n"
            "      - Pulsa START durante el vídeo para abrir el menú multimedia en la\n"
            "        parte inferior con contador de capítulo en vivo (CAP XX/YY):\n"
            "        [|<] Ant, [<<] Rew, [>] Play, [||] Pausa, [>>] Fwd, [>|] Sig, [ST] Stop.\n"
            "      - Usa Izquierda/Derecha en la cruceta para elegir opción y el botón A o C para confirmar.\n"
            "      - Pulsa START de nuevo (o elige [>] Play) para ocultar el menú por completo\n"
            "        y continuar viendo el vídeo limpio a pantalla completa.\n"
            f"  * Repetición automática en bucle:  {'ACTIVADA (al terminar el último vídeo vuelve a empezar)' if loop_playback else 'DESACTIVADA'}\n\n"
            "3. GRABACIÓN DIRECTA EN UN CD VIRGEN PARA UNA MEGA CD ORIGINAL\n"
            "--------------------------------------------------------------\n"
            "  * Puedes grabar el disco directamente desde el botón '💿 GRABAR CD MEGA CD'\n"
            "    del programa (incluye ImgBurn portable integrado y autoconfigurado en modo\n"
            "    DAO para Mega CD sin necesidad de instalar nada).\n"
            "===============================================================================\n"
        )

    return iso_path, cue_path, readme_path


def detect_cd_burners():
    """
    Detecta las unidades grabadoras de CD/DVD conectadas al equipo mediante WMI (Win32_CDROMDrive,
    priorizando grabadoras ASUS externas/internas como en Grabar_MegaCD_ImgBurn.bat) e IMAPI2.
    Devuelve una lista de diccionarios: [{'id': unique_id, 'drive': 'F:', 'name': 'F: (ASUS SDRW-08D2S-U)'}]
    """
    ps_script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        "$wmi = Get-CimInstance Win32_CDROMDrive; "
        "if ($null -ne $wmi) { "
        "  foreach ($d in $wmi) { "
        "    $drv = ($d.Drive).Trim(); "
        "    $nm = ($d.Name).Trim(); "
        "    if (-not $nm) { $nm = ($d.Caption).Trim() }; "
        "    Write-Output ('WMI|' + $drv + '|' + $nm) "
        "  } "
        "} else { "
        "  $dm = New-Object -ComObject IMAPI2.MsftDiscMaster2; "
        "  if ($null -ne $dm) { "
        "    foreach ($id in $dm) { "
        "      $rec = New-Object -ComObject IMAPI2.MsftDiscRecorder2; "
        "      $rec.InitializeDiscRecorder($id); "
        "      $drv = ($rec.VolumePathNames -join ','); "
        "      $ven = ($rec.VendorId).Trim(); "
        "      $prd = ($rec.ProductId).Trim(); "
        "      Write-Output ($id + '|' + $drv + '|' + $ven + ' ' + $prd) "
        "    } "
        "  } "
        "}"
    )
    burners = []
    seen_drives = set()
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW
        )
        for line in res.stdout.splitlines():
            line = line.strip()
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) == 3:
                    uid, drv, model = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    drv_clean = drv.rstrip("\\")
                    if drv_clean and drv_clean.upper() in seen_drives:
                        continue
                    if drv_clean:
                        seen_drives.add(drv_clean.upper())
                    label = f"{drv_clean} ({model})" if drv_clean else model
                    is_asus = "ASUS" in model.upper()
                    burners.append({"id": uid, "drive": drv_clean or "F:", "name": label, "is_asus": is_asus})
    except Exception:
        pass
    # Priorizar grabadora ASUS si está conectada
    burners.sort(key=lambda b: (0 if b.get("is_asus") else 1, b.get("drive", "")))
    return burners


def burn_iso_to_cd(iso_path, cue_path=None, recorder_id=None, drive_letter=None, speed_x=4, status_cb=None):
    """
    Graba la imagen .CUE/.ISO completa de Mega CD (incluyendo los Sectores 0..15 de arranque y seguridad
    SEGA Mega CD, IP.BIN, SP.BIN, MAINPLAY.BIN, SUBPLAY.BIN y los vídeos .CIN) directamente en un
    CD-R virgen utilizando el ImgBurn portable integrado en el ejecutable (con la configuración
    exacta de Grabar_MegaCD_ImgBurn.bat en modo DAO), o IMAPI2 nativo como respaldo.
    """
    if not os.path.isfile(iso_path):
        raise RuntimeError(f"No se encontró el archivo ISO para grabar:\n{iso_path}")

    # Determinar el archivo de origen (.CUE preferido para ImgBurn en Mega CD, o .ISO)
    if not cue_path or not os.path.isfile(cue_path):
        candidate_cue = os.path.splitext(iso_path)[0] + ".CUE"
        if os.path.isfile(candidate_cue):
            cue_path = candidate_cue
    src_image = cue_path if (cue_path and os.path.isfile(cue_path)) else iso_path

    # Determinar letra de unidad destino (ej: "F:")
    target_drive = (drive_letter or "").strip().rstrip("\\")
    if not target_drive or target_drive == "AUTO":
        detected = detect_cd_burners()
        if detected and detected[0].get("drive"):
            target_drive = detected[0]["drive"]
        else:
            target_drive = "F:"
    if len(target_drive) == 1 and target_drive.isalpha():
        target_drive = target_drive.upper() + ":"

    imgburn_exe = find_imgburn()
    if imgburn_exe and os.path.isfile(imgburn_exe):
        if status_cb:
            status_cb(
                f"Iniciando ImgBurn integrado -> Unidad {target_drive} a {speed_x}x "
                f"({os.path.basename(src_image)})..."
            )

        descriptor,ini_path=tempfile.mkstemp(prefix="ImgBurn_Astra3_",suffix=".ini")
        os.close(descriptor)
        log_path=os.path.join(os.path.dirname(os.path.abspath(iso_path)),
                              "Registro_grabacion_"+time.strftime("%Y%m%d_%H%M%S")+".log")
        try:
            with open(ini_path, "w", encoding="ascii") as f:
                f.write("[Settings]\r\n")
                f.write("WRITE_WriteType=0\r\n")     # DAO/SAO óptimo para SEGA Mega CD
                f.write("WRITE_Verify=1\r\n")
                f.write("WRITE_TestMode=0\r\n")
                f.write("WRITE_DeleteImage=0\r\n")
                f.write("WRITE_EjectTray=1\r\n")
                f.write("GENERAL_CloseProgramAfterWrite=1\r\n")
        except Exception:
            pass

        cmd = [
            imgburn_exe,
            "/MODE", "WRITE",
            "/SRC", src_image,
            "/DEST", target_drive,
            "/SPEED", f"{int(speed_x)}x",
            "/VERIFY", "YES",
            "/TESTMODE", "NO",
            "/EJECT", "YES",
            "/START",
            "/WAITFORMEDIA",
            "/CLOSESUCCESS",
            "/PORTABLE",
            "/NOSAVESETTINGS",
            "/SETTINGS", ini_path,
            "/LOG", log_path,
        ]
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL)
        try:
            if os.path.isfile(ini_path):
                os.remove(ini_path)
        except Exception:
            pass

        if res.returncode != 0:
            raise RuntimeError(
                f"ImgBurn devolvió el código de estado {res.returncode}.\n\n"
                "Verifica que la grabadora esté conectada con un disco CD-R virgen insertado."
            )
        confirm_imgburn_verification(log_path)
        if status_cb:status_cb("CD grabado y verificado contra la ISO. Registro: "+log_path)
        return True

    # Respaldo con IMAPI2 nativo de Windows si por algún motivo no estuviera ImgBurn
    if status_cb:
        status_cb("Inicializando grabadora de CD y verificando disco CD-R virgen...")

    sectors_per_sec = max(1, int(speed_x)) * 75 if speed_x and int(speed_x) > 0 else 300
    rec_filter = recorder_id if recorder_id else ""

    ps_burn = f"""
$ErrorActionPreference = 'Stop'
$isoPath = '{iso_path.replace("'", "''")}'
$targetId = '{rec_filter.replace("'", "''")}'
$reqSectorsPerSec = {sectors_per_sec}

$dm = New-Object -ComObject IMAPI2.MsftDiscMaster2
if ($dm.Count -eq 0) {{
    throw 'NO_BURNER: No se detectó ninguna unidad grabadora de CD/DVD conectada al ordenador.'
}}

$chosenId = $null
if ($targetId -ne '' -and $targetId -ne 'AUTO' -and $targetId -ne 'WMI') {{
    foreach ($id in $dm) {{
        if ($id -eq $targetId) {{ $chosenId = $id; break }}
    }}
}}
if ($null -eq $chosenId) {{
    $chosenId = $dm.Item(0)
}}

$recorder = New-Object -ComObject IMAPI2.MsftDiscRecorder2
$recorder.InitializeDiscRecorder($chosenId)

$dataWriter = New-Object -ComObject IMAPI2.MsftDiscFormat2Data
if (-not $dataWriter.IsRecorderSupported($recorder)) {{
    throw 'UNSUPPORTED_RECORDER: La unidad seleccionada no soporta grabación de discos.'
}}
$dataWriter.Recorder = $recorder
$dataWriter.ClientName = 'MegaCDVideoStudio7'

if (-not $dataWriter.IsCurrentMediaSupported($recorder)) {{
    throw 'NO_MEDIA: No hay un disco CD-R virgen insertado en la grabadora o el disco no es válido.'
}}
if (-not $dataWriter.MediaPhysicallyBlank) {{
    throw 'NOT_BLANK: El CD insertado no está vacío. Por favor, inserta un CD-R virgen nuevo.'
}}

$dataWriter.ForceMediaToBeClosed = $true
try {{
    $dataWriter.SetWriteSpeed($reqSectorsPerSec, $false)
}} catch {{
}}

$stream = New-Object -ComObject ADODB.Stream
$stream.Type = 1
$stream.Open()
$stream.LoadFromFile($isoPath)

$dataWriter.Write($stream)
$stream.Close()

Write-Output 'BURN_OK'
"""
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_burn],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        creationflags=CREATE_NO_WINDOW
    )
    if res.returncode != 0 or "BURN_OK" not in res.stdout:
        err_txt = (res.stderr or res.stdout or "Error desconocido al grabar el CD.").strip()
        if "NO_BURNER:" in err_txt:
            raise RuntimeError(
                "No se detectó ninguna unidad grabadora de CD/DVD conectada al ordenador.\n\n"
                "Conecta tu grabadora de CD (USB o interna), pulsa '↻ Detectar' y vuelve a pulsar '💿 Grabar CD'."
            )
        if "NO_MEDIA:" in err_txt:
            raise RuntimeError(
                "No hay ningún CD-R virgen dentro de la grabadora.\n\n"
                "Inserta un disco CD-R virgen (no CD-RW, ya que el láser original de Mega CD requiere CD-R) y vuelve a intentarlo."
            )
        if "NOT_BLANK:" in err_txt:
            raise RuntimeError(
                "El disco insertado en la grabadora no está vacío.\n\n"
                "Por favor, inserta un disco CD-R virgen nuevo."
            )
        raise RuntimeError(f"No se pudo completar la grabación del CD:\n{err_txt}")
    if status_cb:status_cb("CD escrito con IMAPI2; comparando el disco con la ISO...")
    verify_iso_against_device(iso_path,target_drive)
    return True


class MegaCDVideoStudioApp(StudioLayout, CavcStudio):
    """Flujos de conversión, emulación y grabación del estudio."""

    def __init__(self, root):
        self.root = root
        self.root.title("Mega CD Estudio 14.1")
        self.root.configure(bg=BG)
        self._init_window_geometry()

        self.videos = []  # Lista de dicts: {path, name, duration, width, height, segments}
        self.is_converting = False
        self._conversion_running=False
        self._conversion_cancel_event=threading.Event()
        self._closing=False
        self.root.protocol("WM_DELETE_WINDOW",self._close_window)
        self.last_iso_path = None
        self.last_cue_path = None
        self.burners_list = []

        self._setup_styles()
        self._build_ui()
        self._check_existing_disc()
        self.refresh_burners()

    def _get_selected_fps(self):
        val = self.fps_var.get()
        m = re.match(r"^(\d+)", val)
        return int(m.group(1)) if m else 15

    def _get_selected_region(self):
        return self.region_var.get()[0].upper()

    def _get_selected_palettes(self):
        m = re.match(r"^(\d+)", self.pal_var.get())
        return int(m.group(1)) if m else 4

    def _get_selected_aspect(self):
        v = self.aspect_var.get().lower()
        if "estirar" in v:
            return "stretch"
        if "recortar" in v:
            return "crop"
        return "keep"

    def _get_selected_audio_mode(self):
        v = self.audio_mode_var.get().lower()
        if "+12" in v or "máximo" in v or "maximo" in v:
            return "max"
        if "+4" in v or "estándar" in v or "estandar" in v:
            return "standard"
        if "0 db" in v or "original" in v:
            return "original"
        return "cinema"

    def _get_selected_video_eq(self):
        v = self.video_eq_var.get().lower()
        if "s-video" in v or "hdmi" in v:
            return "svideo_hdmi"
        if "crt" in v or "alto contraste" in v:
            return "crt"
        return "standard"

    def _get_selected_chapter_sec(self):
        m = re.match(r"^(\d+)", self.chapter_dur_var.get())
        mins = int(m.group(1)) if m else 3
        return effective_chapter_seconds(float(mins * 60), self._get_selected_fps())

    def _get_requested_chapter_sec(self):
        m = re.match(r"^(\d+)", self.chapter_dur_var.get())
        return float((int(m.group(1)) if m else 3) * 60)

    def _get_selected_tint_fix(self):
        v = self.tint_var.get().lower()
        if "fuerte" in v:
            return "strong"
        if "desactiv" in v:
            return "off"
        return "auto"

    def _conversion_settings(self):
        workers=self.workers_var.get() if hasattr(self,"workers_var") else "Automático"
        quality=self.quality_mode_var.get()
        mode="sharp24" if quality.startswith("24 FPS") else ("sharp15" if quality.startswith("15 FPS") else ("fixed" if quality.startswith("FPS fijos") else "sharp"))
        return {"fps":24 if mode=="sharp24" else (15 if mode=="sharp15" else self._get_selected_fps()),"region":self._get_selected_region(),
                "num_palettes":self._get_selected_palettes(),"aspect_mode":self._get_selected_aspect(),
                "audio_mode":self._get_selected_audio_mode(),"vocal_clarity":bool(self.vocal_clarity_var.get()),
                "video_eq_mode":self._get_selected_video_eq(),"tint_fix":self._get_selected_tint_fix(),
                "chapter_seconds":self._get_requested_chapter_sec(),"fit_cd":bool(self.fit_cd_var.get()),
                "loop_playback":bool(self.loop_var.get()),"allow_skip":bool(self.skip_var.get()),
                "enable_osd":bool(self.osd_menu_var.get()),"disc_title":self.title_var.get().strip() or "VIDEO FMV MEGA CD PAL",
                "quality_mode":mode,
                "auto_burn":bool(self.auto_burn_var.get()),"workers":int(workers) if workers.isdigit() else 0,
                "acceleration":"cpu" if hasattr(self,"acceleration_var") and self.acceleration_var.get().startswith("CPU") else "auto"}

    def _quality_mode_selected(self):
        if self.quality_mode_var.get().startswith("24 FPS"):
            self.fps_var.set("24 FPS (Cine 24p - Con sincronía A/V activa)")
            self.fit_cd_var.set(False)
        elif self.quality_mode_var.get().startswith("15 FPS"):
            self.fps_var.set("15 FPS (Alta fluidez - Nitidez True-RGB sin artefactos)")
            self.fit_cd_var.set(True)
        elif self.quality_mode_var.get().startswith("Nitidez"):
            self.fit_cd_var.set(True)
        self._refresh_tree()

    def choose_generated_disc(self):
        image=filedialog.askopenfilename(title="Elegir CD para probar o grabar",initialdir=self.out_dir_var.get(),
            filetypes=[("Disco Mega CD","*.cue *.CUE *.iso *.ISO")])
        if not image:return
        cue=Path(image)
        if cue.suffix.lower()==".cue":
            iso=cue.with_suffix(".ISO")
            if not iso.is_file():iso=cue.with_suffix(".iso")
        else:
            iso=cue;candidate=cue.with_suffix(".CUE")
            if not candidate.is_file():candidate=cue.with_suffix(".cue")
            if candidate.is_file():cue=candidate
        if not iso.is_file():
            messagebox.showerror("Disco incompleto","No se encuentra el ISO correspondiente.");return
        self.last_iso_path=str(iso);self.last_cue_path=str(cue)
        self.fusion_btn.configure(state="normal")
        self.status_var.set(f"Disco elegido: {cue}")

    def _compute_max_frame_budget(self,fps):
        if not self.videos or not self.fit_cd_var.get():return None
        plan=make_capacity_plan(self.videos,self._conversion_settings(),sys.modules[__name__])
        available=sum(j["max_cin_bytes"]-j["audio_reserved"]-j["header_reserved"] for j in plan["jobs"])
        return min(default_frame_budget(fps),max(540,available//plan["frames"]))

    def _close_window(self):
        if self._conversion_running:
            self._closing=True
            self.cancel_conversion()
            self.root.after(150,self._wait_close)
        else:self.root.destroy()

    def _wait_close(self):
        if self._conversion_running:self.root.after(150,self._wait_close)
        else:self.root.destroy()

    def cancel_conversion(self):
        self._conversion_cancel_event.set()
        self.status_var.set("Cancelando. Las partes verificadas se conservan para reanudar.")

    def _refresh_tree(self):
        if self._uses_cavc():
            self._refresh_cavc_tree()
            return
        if hasattr(self,"quality_mode_var") and self.quality_mode_var.get().startswith("15 FPS"):
            if not self.fit_cd_var.get():
                self.fit_cd_var.set(True)
            if self._get_selected_fps() != 15:
                self.fps_var.set("15 FPS (Alta fluidez - Nitidez True-RGB sin artefactos)")
        if hasattr(self,"quality_mode_var") and self.quality_mode_var.get().startswith("24 FPS"):
            if self.fit_cd_var.get():
                self.fit_cd_var.set(False)
            if self._get_selected_fps()!=24:self.fps_var.set("24 FPS (Cine 24p - Con sincronía A/V activa)")
        for item in self.tree.get_children():
            self.tree.delete(item)

        fps = self._get_selected_fps()
        chap_sec = self._get_selected_chapter_sec() if hasattr(self, "chapter_dur_var") else MAX_SEGMENT_SEC
        req_sec = self._get_requested_chapter_sec() if hasattr(self, "chapter_dur_var") else chap_sec
        if hasattr(self, "chap_hint_var"):
            if chap_sec < req_sec - 1:
                self.chap_hint_var.set(
                    f"Capítulos de {format_mmss(req_sec)}: división interna en CIN de hasta {format_mmss(chap_sec)}. "
                    "Puede haber una pausa entre CIN."
                )
            else:
                self.chap_hint_var.set("")
        _plan=None
        _plan_error=""
        try:
            if self.videos:_plan=make_capacity_plan(self.videos,self._conversion_settings(),sys.modules[__name__])
        except (ValueError,OSError) as error:_plan_error=str(error)
        base_budget = default_frame_budget(fps)
        max_fb = None
        if max_fb is not None:
            eff_frame_bytes = max_fb * 0.96
        else:
            eff_frame_bytes = base_budget * 0.82
        kbps_est = (fps * eff_frame_bytes) / 1024.0 + 16.3
        total_mb = 0.0

        for idx, v in enumerate(self.videos):
            dur = v["duration"]
            n_seg = max(1, int(math.ceil(dur / req_sec)))
            v["segments"] = n_seg
            if _plan:
                _bytes=sum(j["estimated_bytes"] for j in _plan["jobs"] if j["source_index"]==idx)
                est_mb=_bytes/1048576
            else:est_mb=(dur*kbps_est)/1024.0
            total_mb += est_mb
            mins = int(dur // 60)
            secs = dur % 60
            dur_str = f"{mins:02d}:{secs:04.1f}"
            seg_str = f"{n_seg} cap." if n_seg > 1 else "1 (.CIN)"
            self.tree.insert(
                "", "end", iid=str(idx),
                values=(idx + 1, v["name"], f"{v['width']}x{v['height']}", dur_str, seg_str, f"{est_mb:.1f} MB", v["path"])
            )

        if _plan:total_mb=_plan["estimated_iso_bytes"]/1048576
        pct=total_mb/CD_CAPACITY_MB*100
        tag=" [Plan de CD calculado]" if _plan and _plan["fit_cd"] else ""
        _fps_notice=f" | {_plan['requested_fps']} → {_plan['fps']} FPS" if _plan and _plan['quality_mode']=='sharp' else ""
        self.cap_label.configure(text=f"CD: hasta {total_mb:.1f} / 700 MiB ({pct:.1f}%){tag}{_fps_notice}")
        if _plan and _plan['quality_mode']=='sharp' and hasattr(self,"chap_hint_var"):
            self.chap_hint_var.set(f"Nitidez: {_plan['fps']} FPS efectivos (máximo solicitado: {_plan['requested_fps']}). No se copian bloques cambiados para ahorrar espacio.")
        if _plan and _plan['quality_mode']=='sharp24':
            count=_plan['disc_count'];boxes=_plan['active_boxes']
            area=f"{boxes[0]['active_box'][0]}×{boxes[0]['active_box'][1]}" if len(boxes)==1 else "por vídeo"
            self.cap_label.configure(text=f"24 FPS nítidos | {count} CD | Área {area} | Máx. 700 MiB por CD")
            self.chap_hint_var.set(f"24 FPS fijos. Área activa {area} para respetar el lector 1×. Se prepararán {count} CD; el cambio de disco es manual.")
        if _plan_error:
            self.cap_label.configure(text="No cabe con estos ajustes; comprobar antes de convertir")
            if hasattr(self,"chap_hint_var"):self.chap_hint_var.set(_plan_error)

        self._update_library_summary(pct)

    def _check_existing_disc(self):
        if self.last_iso_path and os.path.isfile(self.last_iso_path):return
        out_dir = self.out_dir_var.get().strip()
        for cue in sorted(Path(out_dir).glob("CD_*/*.CUE")):
            iso=cue.with_suffix(".ISO")
            if iso.is_file():
                self.last_cue_path=str(cue);self.last_iso_path=str(iso)
                self.fusion_btn.configure(state="normal");self._update_selected_disc();return
        if os.path.isdir(out_dir):
            for fn in os.listdir(out_dir):
                if fn.upper().endswith(".CUE"):
                    cue_p = os.path.join(out_dir, fn)
                    iso_p = os.path.splitext(cue_p)[0] + ".ISO"
                    if os.path.isfile(iso_p):
                        self.last_cue_path = cue_p
                        self.last_iso_path = iso_p
                        self.fusion_btn.configure(state="normal")
                        self._update_selected_disc()
                        break

    def refresh_burners(self):
        self.burners_list = detect_cd_burners()
        if self.burners_list:
            vals = [b["name"] for b in self.burners_list]
            self.burner_cb.configure(values=vals)
            self.burner_var.set(vals[0])
        else:
            vals = ["F: (Detección automática ASUS / Grabadora CD-ROM)"]
            self.burner_cb.configure(values=vals)
            self.burner_var.set(vals[0])

    def _get_selected_burner_info(self):
        sel = self.burner_var.get()
        for b in self.burners_list:
            if b["name"] == sel:
                return b.get("id", "AUTO"), b.get("drive", "F:")
        m = re.match(r"^([A-Za-z]:)", sel.strip())
        drv = m.group(1).upper() if m else "F:"
        return "AUTO", drv

    def _get_selected_burn_speed(self):
        m = re.match(r"^(\d+)", self.burn_speed_var.get())
        return int(m.group(1)) if m else 4

    def add_videos(self):
        if self.is_converting:
            return
        paths = filedialog.askopenfilenames(
            title="Seleccionar vídeo(s) para convertir a Mega CD",
            filetypes=[
                ("Archivos de Vídeo", "*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv *.mpg *.mpeg *.m4v *.ts"),
                ("Todos los archivos", "*.*")
            ]
        )
        if not paths:
            return
        self._probe_and_add_async(paths)

    def add_folder(self):
        if self.is_converting:
            return
        folder = filedialog.askdirectory(title="Seleccionar carpeta con vídeos")
        if not folder:
            return
        exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".m4v", ".ts"}
        paths = [
            os.path.join(folder, fn)
            for fn in sorted(os.listdir(folder))
            if os.path.splitext(fn)[1].lower() in exts
        ]
        if not paths:
            messagebox.showinfo("Sin vídeos", "No se encontraron archivos de vídeo en la carpeta seleccionada.")
            return
        self._probe_and_add_async(paths)

    def _build_video_entry(self, p):
        dur, w, h = probe_video_info(p)
        if dur <= 0:
            dur = 10.0
        return {
            "path": p,
            "name": os.path.basename(p),
            "duration": dur,
            "width": w,
            "height": h,
            "segments": 1
        }

    def _add_video_paths(self, paths):
        self.status_var.set("Analizando vídeo(s) seleccionado(s)...")
        self.root.update_idletasks()
        for p in paths:
            self.videos.append(self._build_video_entry(p))
        self._refresh_tree()
        self.status_var.set(f"{len(self.videos)} vídeo(s) en la lista listos para convertir.")

    def _probe_and_add_async(self, paths):
        pending = list(paths)
        if not pending:
            return
        self._probing_count = getattr(self, "_probing_count", 0) + 1
        generation = getattr(self, "_probe_generation", 0)
        self._set_probe_actions("disabled")
        self.status_var.set(f"Analizando {len(pending)} vídeo(s)...")
        self.root.update_idletasks()

        def worker():
            entries = []
            try:
                for idx, p in enumerate(pending, 1):
                    if getattr(self, "_closing", False) or generation != getattr(self, "_probe_generation", 0):
                        return
                    entries.append(self._build_video_entry(p))
                    if len(pending) > 1:
                        def _prog(i=idx, total=len(pending)):
                            if not self._closing and generation == getattr(self, "_probe_generation", 0):
                                self.status_var.set(f"Analizando vídeos ({i}/{total})...")
                        self.root.after(0, _prog)
            finally:
                def _finish():
                    if self._closing or generation != getattr(self, "_probe_generation", 0):
                        return
                    self._probing_count = max(0, getattr(self, "_probing_count", 1) - 1)
                    if getattr(self, "_closing", False):
                        return
                    if entries:
                        self.videos.extend(entries)
                        self._refresh_tree()
                    if self._probing_count == 0:
                        self._set_probe_actions("normal")
                        self.status_var.set(
                            f"{len(self.videos)} vídeo(s) en la lista listos para convertir."
                        )
                self.root.after(0, _finish)

        threading.Thread(target=worker, daemon=True).start()

    def _set_probe_actions(self, state):
        for button in (self.convert_btn, self.preview_btn, self.burn_btn):
            button.configure(state=state)

    def move_up(self):
        if self.is_converting:
            return
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx > 0:
            self.videos[idx - 1], self.videos[idx] = self.videos[idx], self.videos[idx - 1]
            self._refresh_tree()
            self.tree.selection_set(str(idx - 1))

    def move_down(self):
        if self.is_converting:
            return
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < len(self.videos) - 1:
            self.videos[idx + 1], self.videos[idx] = self.videos[idx], self.videos[idx + 1]
            self._refresh_tree()
            self.tree.selection_set(str(idx + 1))

    def remove_selected(self):
        if self.is_converting:
            return
        sel = self.tree.selection()
        if not sel:
            return
        indices = []
        for item in sel:
            try:
                indices.append(int(item))
            except ValueError:
                pass
        for idx in sorted(set(indices), reverse=True):
            if 0 <= idx < len(self.videos):
                del self.videos[idx]
        self._refresh_tree()

    def clear_videos(self):
        if self.is_converting:
            return
        self._probe_generation = getattr(self, "_probe_generation", 0) + 1
        self._probing_count = 0
        self._set_probe_actions("normal")
        self.videos.clear()
        self._refresh_tree()
        self.status_var.set("Lista vacía.")

    def browse_out_dir(self):
        if self.is_converting:
            return
        d = filedialog.askdirectory(title="Seleccionar carpeta de salida para el CD de Mega CD")
        if d:
            self.out_dir_var.set(d)

    def open_out_dir(self):
        out_dir = self.out_dir_var.get().strip()
        if os.path.isdir(out_dir):
            os.startfile(out_dir)
        else:
            messagebox.showinfo("Carpeta no creada aún", f"La carpeta aún no existe:\n{out_dir}\n\nSe creará automáticamente al convertir.")

    def _launch_cue_in_fusion(self, cue_file):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        fusion_exe = os.path.join(desktop, "Fusion.exe")
        if not os.path.isfile(fusion_exe):
            fusion_exe = filedialog.askopenfilename(
                title="Localizar Fusion.exe (Kega Fusion)",
                filetypes=[("Ejecutable Kega Fusion", "Fusion.exe"), ("Ejecutables", "*.exe")]
            )
            if not fusion_exe:
                return
        reg = {"eu": "E", "us": "U", "jp": "J"}.get(
            read_cavc_info(cue_file).get("region"), self._get_selected_region())
        reg_flag = "-eur" if reg == "E" else ("-usa" if reg == "U" else "-jap")
        subprocess.Popen(
            [fusion_exe, cue_file, "-mcd", reg_flag],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(fusion_exe)
        )

    def launch_in_fusion(self):
        if not self.last_cue_path or not os.path.isfile(self.last_cue_path):
            messagebox.showwarning("Sin imagen de disco", "Primero genera un disco para poder abrirlo en Kega Fusion.")
            return
        self._launch_cue_in_fusion(self.last_cue_path)

    def start_quick_preview(self):
        """
        Codifica una muestra rápida de 15 segundos del vídeo seleccionado (o del primero de la lista)
        con la configuración actual de volumen, ecualizador vocal, brillo S-Video/HDMI y menú START,
        y la abre directamente en Kega Fusion para comprobar imagen y volumen en segundos.
        """
        if self.is_converting:
            return
        if self._uses_cavc():
            self._start_cavc(preview=True)
            return
        if not self.videos:
            messagebox.showwarning("Añade algún vídeo", "Añade primero un vídeo a la lista para hacer la prueba rápida de 15 segundos.")
            return

        sel = self.tree.selection()
        v_idx = int(sel[0]) if sel else 0
        if v_idx < 0 or v_idx >= len(self.videos):
            v_idx = 0
        v_info = self.videos[v_idx]

        self.is_converting = True
        self.convert_btn.configure(state="disabled")
        self.burn_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self.progress_var.set(0.0)

        settings=self._conversion_settings()
        try:
            plan=make_capacity_plan(self.videos,settings,sys.modules[__name__])
        except ValueError as error:
            self.is_converting=False
            self.convert_btn.configure(state="normal");self.burn_btn.configure(state="normal");self.preview_btn.configure(state="normal")
            messagebox.showerror("No cabe con estos ajustes",str(error));return
        settings["fps"]=plan["fps"]
        jobs=[j for j in plan["jobs"] if j["source_index"]==v_idx]
        if plan["fit_cd"]:
            settings["preview_frame_budget"]=min((j["max_cin_bytes"]-j["audio_reserved"]-j["header_reserved"]-8000*(min(2,j["frames"])+j["keyframes"]))//max(1,j["frames"]-2-j["keyframes"]) for j in jobs)
        settings["frame_active_box"]=jobs[0].get("active_box")
        self._conversion_cancel_event.clear();self._conversion_running=True
        self.cancel_btn.configure(state="normal")
        t = threading.Thread(target=self._quick_preview_worker, args=(dict(v_info),settings), daemon=True)
        t.start()

    def _quick_preview_worker(self,v_info,settings):
        try:
            fps=settings["fps"];region=settings["region"];num_palettes=settings["num_palettes"]
            aspect_mode=settings["aspect_mode"];audio_mode=settings["audio_mode"]
            vocal_clarity=settings["vocal_clarity"];video_eq_mode=settings["video_eq_mode"]
            tint_fix=settings["tint_fix"];enable_osd=settings["enable_osd"]

            dur = v_info["duration"]
            # Si el vídeo dura más de 45s, tomamos el fragmento a partir del segundo 25 (o 15% del vídeo)
            # para evitar intros en negro y escuchar diálogos/escenas reales inmediatamente.
            start_sec = min(25.0, dur * 0.15) if dur > 45.0 else 0.0
            sample_len = min(15.0, max(2.0, dur - start_sec))

            preview_dir = os.path.join(tempfile.gettempdir(), "MegaCD_Preview_7")
            os.makedirs(preview_dir, exist_ok=True)
            work_dir = tempfile.mkdtemp(prefix="megacd_prev_")
            out_cin = os.path.join(work_dir, "VIDEO.CIN")

            def on_prog(f_cur, f_tot, last_bytes):
                pct = min(92.0, (f_cur / max(1, f_tot)) * 92.0)
                self.root.after(0, lambda: (
                    self.progress_var.set(pct),
                    self.status_var.set(
                        f"Generando Prueba Rápida 15s ({v_info['name']}): fotograma {f_cur}/{f_tot} "
                        f"[Audio: {audio_mode.upper()}, EQ: {video_eq_mode}]..."
                    )
                ))

            encode_segment_to_cin(
                v_info["path"], start_sec, sample_len, fps, aspect_mode, num_palettes,
                out_cin, work_dir, progress_cb=on_prog, max_frame_budget=settings.get("preview_frame_budget"),
                audio_mode=audio_mode, vocal_clarity=vocal_clarity, video_eq_mode=video_eq_mode,
                tint_fix=tint_fix,acceleration=settings["acceleration"],
                frame_active_box=settings.get("frame_active_box"),
                allow_lossy_reuse=settings["quality_mode"] not in ("sharp","sharp24"),cancel_cb=self._conversion_cancel_event.is_set
            )

            iso_path, cue_path, _ = build_megacd_disc(
                [out_cin], preview_dir, region=region, disc_title="PRUEBA 15S MEGA CD",
                loop_playback=True, allow_skip=True, enable_osd=enable_osd
            )
            shutil.rmtree(work_dir, ignore_errors=True)

            def on_done():
                self.is_converting=False;self._conversion_running=False
                self.cancel_btn.configure(state="disabled")
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.progress_var.set(100.0)
                self.status_var.set("¡Prueba rápida de 15s lista! Abriendo en Kega Fusion...")
                if not self._closing:self._launch_cue_in_fusion(cue_path)

            self.root.after(0, on_done)
        except Exception as e:
            err_msg = str(e)
            def on_err():
                self.is_converting=False;self._conversion_running=False
                self.cancel_btn.configure(state="disabled")
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.status_var.set(f"Error en prueba rápida: {err_msg}")
                if not self._closing:messagebox.showerror("Prueba rápida detenida",err_msg)
            self.root.after(0, on_err)

    def start_burn_cd(self):
        """
        Graba directamente el CD en formato autoarrancable de Mega CD (con Sector 0..15 de seguridad,
        IP.BIN, SP.BIN, MAINPLAY.BIN, SUBPLAY.BIN y vídeos .CIN) usando el ImgBurn portable integrado.
        Si hay vídeos en la lista que aún no se han convertido, ofrece convertirlos y grabarlos seguidamente.
        """
        if self.is_converting:
            return
        self._check_existing_disc()

        if self.videos and (not self.last_iso_path or not os.path.isfile(self.last_iso_path)):
            ans = messagebox.askyesno(
                "Convertir y Grabar CD Mega CD",
                "Se van a convertir primero los vídeos de la lista al formato Mega CD (con sus archivos de arranque IP.BIN / SP.BIN "
                "y audio amplificado sin ruido) y a continuación se grabará el CD automáticamente con el ImgBurn integrado.\n\n"
                "¿Deseas comenzar?"
            )
            if ans:
                self.auto_burn_var.set(True)
                self.start_conversion()
            return

        iso_to_burn = self.last_iso_path
        if not iso_to_burn or not os.path.isfile(iso_to_burn):
            iso_to_burn = filedialog.askopenfilename(
                title="Seleccionar imagen CUE o ISO de Mega CD para grabar en el CD",
                filetypes=[("Imagen de Disco Mega CD (.CUE / .ISO)", "*.cue *.CUE *.iso *.ISO"), ("Todos los archivos", "*.*")]
            )
            if not iso_to_burn:
                return
            if iso_to_burn.upper().endswith(".CUE"):
                self.last_cue_path = iso_to_burn
                cand_iso = os.path.splitext(iso_to_burn)[0] + ".ISO"
                if os.path.isfile(cand_iso):
                    iso_to_burn = cand_iso
            self.last_iso_path = iso_to_burn

        iso_mb = os.path.getsize(iso_to_burn) / (1024.0 * 1024.0)
        if os.path.getsize(iso_to_burn) > 360_000 * 2048:
            messagebox.showwarning(
                "Tamaño superior a 700 MB",
                f"La imagen ISO seleccionada ocupa {iso_mb:.1f} MB y supera los 700 MB de un CD-R estándar.\n"
                "Activa la casilla 'Ajustar como máximo a la capacidad de 1 CD (700 MB)' y vuelve a convertir."
            )
            return

        speed_x = self._get_selected_burn_speed()
        rec_id, drive_letter = self._get_selected_burner_info()
        ans = messagebox.askyesno(
            "Grabar CD Autoarrancable de SEGA Mega CD (ImgBurn Integrado)",
            f"¿Deseas grabar ahora el disco en formato SEGA Mega CD?\n\n"
            f"• Imagen: {os.path.basename(iso_to_burn)} ({iso_mb:.2f} MB)\n"
            f"• Unidad destino: {drive_letter}\n"
            f"• Velocidad: {speed_x}x (Modo DAO Mega CD)\n"
            f"• Motor: ImgBurn Portable Integrado\n\n"
            f"• Verificación contra ISO: activada.\n"
            f"Si la bandeja se abre antes de verificar, ciérrala con el mismo CD dentro.\n\n"
            f"Asegúrate de haber insertado un disco CD-R virgen en tu grabadora."
        )
        if not ans:
            return

        self.is_converting = True
        self.convert_btn.configure(state="disabled")
        self.burn_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self.progress_var.set(25.0)
        self.status_var.set(f"Grabando CD de Mega CD ({os.path.basename(iso_to_burn)}) en {drive_letter} a {speed_x}x con ImgBurn integrado...")

        t = threading.Thread(target=self._burn_worker, args=(iso_to_burn, rec_id, drive_letter, speed_x), daemon=True)
        t.start()

    def _burn_worker(self, iso_path, rec_id, drive_letter, speed_x):
        try:
            burn_iso_to_cd(
                iso_path, cue_path=self.last_cue_path, recorder_id=rec_id,
                drive_letter=drive_letter, speed_x=speed_x,
                status_cb=lambda msg: self.root.after(0, lambda: self.status_var.set(msg))
            )
            def on_burn_ok():
                self.is_converting = False
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.progress_var.set(100.0)
                self.status_var.set("¡CD de Mega CD grabado y finalizado con éxito! Ya puedes insertarlo en tu consola.")
                messagebox.showinfo(
                    "¡CD de Mega CD Grabado con Éxito!",
                    "El disco CD-R se ha grabado y cerrado correctamente con el sistema de arranque SEGA Mega CD "
                    "(Sectores de seguridad, IP.BIN, SP.BIN, MAINPLAY.BIN, SUBPLAY.BIN y pistas de vídeo .CIN).\n\n"
                    "Ya puedes insertarlo directamente en tu consola SEGA Mega CD y pulsar START."
                )
            self.root.after(0, on_burn_ok)
        except Exception as e:
            err_msg = str(e)
            def on_burn_err():
                self.is_converting = False
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.progress_var.set(0.0)
                self.status_var.set(f"Aviso de grabación: {err_msg.splitlines()[0]}")
                messagebox.showwarning("Grabación de CD Mega CD", err_msg)
            self.root.after(0, on_burn_err)

    def start_conversion(self):
        if getattr(self, "_probing_count", 0):
            return
        if self._uses_cavc():
            self._start_cavc()
            return
        if self.is_converting:
            return
        if not self.videos:
            messagebox.showwarning("Añade algún vídeo", "Por favor, añade al menos un archivo de vídeo a la lista antes de continuar.")
            return

        out_dir = self.out_dir_var.get().strip()
        if not out_dir:
            messagebox.showwarning("Carpeta de salida", "Selecciona una carpeta de destino válida.")
            return

        self.is_converting = True
        self.convert_btn.configure(state="disabled")
        self.burn_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self.progress_var.set(0.0)

        settings=self._conversion_settings()
        sources=[dict(v) for v in self.videos]
        self._conversion_cancel_event.clear()
        self._conversion_running=True
        self.cancel_btn.configure(state="normal")
        t = threading.Thread(target=self._conversion_worker, args=(out_dir,settings,sources), daemon=True)
        t.start()

    def _conversion_worker(self,out_dir,settings=None,sources=None):
        settings=settings or self._conversion_settings()
        sources=sources or [dict(v) for v in self.videos]
        started=time.perf_counter()
        def note(message):self.root.after(0,lambda m=message:self.status_var.set(m))
        def progress(done,total,index,cur,part_total,last):
            elapsed=max(.001,time.perf_counter()-started)
            speed=done/elapsed
            eta=(total-done)/max(.1,speed)
            message=f"Convirtiendo: {done:,}/{total:,} fotogramas | {speed:.1f} fot/s | Restan aprox. {format_mmss(eta)} | CIN {index+1}"
            self.root.after(0,lambda d=done,t=total,m=message:(self.progress_var.set(min(94,94*d/max(1,t))),self.status_var.set(m)))
        try:
            result=convert_playlist(sources,out_dir,settings,progress_cb=progress,note_cb=note,cancel_event=self._conversion_cancel_event)
            def finish():
                self.is_converting=False;self._conversion_running=False
                self.cancel_btn.configure(state="disabled")
                self.convert_btn.configure(state="normal");self.burn_btn.configure(state="normal");self.preview_btn.configure(state="normal")
                self.last_iso_path=result["iso_path"];self.last_cue_path=result["cue_path"]
                self.fusion_btn.configure(state="normal");self.progress_var.set(100)
                mb=result["iso_bytes"]/1048576;disc_count=result.get("disc_count",1)
                self.status_var.set(f"{disc_count} CD verificado(s) | {result['fps']} FPS | Tiempo: {format_mmss(result['elapsed_seconds'])} | GPU: {result['gpu_encode_jobs']} CIN.")
                if self._closing:return
                if settings.get("auto_burn"):
                    if disc_count>1:messagebox.showinfo("Varios CD preparados","Se iniciará la grabación del primer CD. Para los demás, cambie el CD virgen y use Elegir disco.")
                    self.start_burn_cd()
                else:messagebox.showinfo("Disco creado y verificado — Mega CD Estudio 14.1",
                    f"Carpeta: {out_dir}\n\nDiscos: {disc_count} | Total: {mb:.2f} MiB | Máx. 700 MiB por CD\nCapítulos lógicos: {result['plan']['logical_chapters']}\n"
                    f"Archivos CIN: {len(result['jobs'])}\nFPS solicitados: {result['requested_fps']} | efectivos: {result['fps']}\nGPU en codificación Cinepak: {result['gpu_encode_jobs']} CIN\n"
                    f"Audio: {settings['audio_mode']}\nMenú START: {'Activado' if settings['enable_osd'] else 'Desactivado'}\n"
                    "\nInformes: Informe_de_seguridad.txt e Informe_conversion_Astra_2.json.\nLas transiciones entre CIN conservan el comportamiento de Astra.")
            self.root.after(0,finish)
        except Exception as error:
            text=str(error)
            cancelled=isinstance(error,ConversionCancelled)
            def failed():
                self.is_converting=False;self._conversion_running=False
                self.cancel_btn.configure(state="disabled")
                self.convert_btn.configure(state="normal");self.burn_btn.configure(state="normal");self.preview_btn.configure(state="normal")
                self.status_var.set(text)
                if self._closing:return
                if cancelled:messagebox.showinfo("Conversión cancelada",text)
                else:messagebox.showerror("Conversión interrumpida — trabajo conservado",text+"\n\nLos capítulos completos quedan guardados para reanudar con los mismos ajustes.")
            self.root.after(0,failed)


def run_cli_test(args):
    """Modo de prueba por línea de comandos: megacd_studio.py --cli-test <video> <out_dir> [fps] [aspect] [pals]"""
    vpath = args[0]
    out_dir = args[1]
    fps = int(args[2]) if len(args) > 2 else 15
    aspect = args[3] if len(args) > 3 else "stretch"
    pals = int(args[4]) if len(args) > 4 else 4
    print(f"[CLI] Convirtiendo {vpath} -> {out_dir} ({fps} FPS, aspect={aspect}, pals={pals})")
    work_dir = tempfile.mkdtemp(prefix="megacd_cli_")
    cin_path = os.path.join(work_dir, "VIDEO.CIN")
    sz, n_f = encode_segment_to_cin(vpath, 0.0, None, fps, aspect, pals, cin_path, work_dir)
    iso_path, cue_path, readme_path = build_megacd_disc([cin_path], out_dir, region="E")
    shutil.rmtree(work_dir, ignore_errors=True)
    print(f"[CLI] OK -> {iso_path} ({os.path.getsize(iso_path):,} bytes, CIN={sz:,} bytes, {n_f} frames)")


def run_cli_conversion(config_path):
    import json
    with open(config_path,"r",encoding="utf-8-sig") as f:config=json.load(f)
    def cli_note(message):
        # Un EXE sin consola puede heredar un descriptor inválido al lanzarlo
        # desde PowerShell. La salida informativa no invalida un disco terminado.
        try:print(message,flush=True)
        except (OSError,ValueError):pass
    result=convert_playlist(config["sources"],config["output"],config["settings"],note_cb=cli_note)
    cli_note(json.dumps(result,ensure_ascii=False))


def run_self_test(report_path):
    import json
    fast.initialize()
    gpu.configure("auto")
    _gpu_probe=gpu.nearest(np.zeros((1,12),np.float32),np.zeros((1,12),np.float32))
    ip, main, sub = map(bytearray, (IP_BIN, MAINPLAY_BIN, SUBPLAY_BIN))
    patch_cinepak_players(ip, main, sub)
    patch = patch_main_controller(ip, main)
    sp = build_multi_sp_bin(bytes(sub), [25])
    ff = subprocess.run([find_ffmpeg(), "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=CREATE_NO_WINDOW, timeout=30)
    result = {"estado": "OK" if ff.returncode == 0 else "ERROR", "sp_bytes": len(sp),
              "main_extra_bytes": patch["extra_bytes"], "ffmpeg": ff.stdout.decode("utf-8", "replace").splitlines()[0],
              "imgburn_disponible": bool(find_imgburn()),"cpu_jit":fast.AVAILABLE,"gpu_encode":gpu.stats(), "hardware": "No verificado en consola real"}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if ff.returncode:
        raise SystemExit(1)


from megacd_validation_ui import install_validation_ui
install_validation_ui(MegaCDVideoStudioApp)

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if len(sys.argv)==3 and sys.argv[1]=="--verify-worker":
        from megacd_integrity import worker_main
        worker_main(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        run_self_test(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=="--convert-json":
        run_cli_conversion(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "--cli-test":
        run_cli_test(sys.argv[2:])
    else:
        if sys.platform == "win32":
            try:
                import ctypes
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        root = tk.Tk()
        app = MegaCDVideoStudioApp(root)
        root.mainloop()
