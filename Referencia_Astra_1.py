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
from tkinter import ttk, filedialog, messagebox
import numpy as np
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


def probe_video_info(video_path):
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
    return int(min(13500, max(1600, VIDEO_BPS_BUDGET // max(1, int(fps)))))


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


def probe_color_matrix(video_path, width, height):
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
        scale = f"scale=256:192:force_original_aspect_ratio=increase:flags=lanczos:{cm},format=rgb24,crop=256:192"
    elif aspect_mode == "stretch":
        scale = f"scale=256:192:flags=lanczos:{cm},format=rgb24"
    else:
        scale = f"scale={act_w}:{act_h}:flags=lanczos:{cm},format=rgb24,pad=256:192:{pad_x}:{pad_y}:black"
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
    audio_mode="cinema_boost", vocal_clarity=True
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
    subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW
    )

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
        else:
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
            d_rgb = (
                (b_rgb * b_rgb).sum(axis=1, keepdims=True)
                + (cb_rgb * cb_rgb).sum(axis=1, keepdims=True).T
                - 2.0 * (b_rgb @ cb_rgb.T)
            )
            m2_codes[p_tiles] = np.argmin(d_rgb, axis=1).astype(np.uint8).reshape(len(p_tiles), 8)

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
                d16_rgb = (
                    (b16_rgb * b16_rgb).sum(axis=1, keepdims=True)
                    + (cb16_rgb * cb16_rgb).sum(axis=1, keepdims=True).T
                    - 2.0 * (b16_rgb @ cb16_rgb.T)
                )
                m3_codes[p_tiles] = np.argmin(d16_rgb, axis=1).astype(np.uint8).reshape(len(p_tiles), 16)

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
    tint_fix="auto", budget_scale=1.0
):
    """Una pasada de codificación de un segmento a .CIN. Devuelve (bytes, fotogramas, media B/s, pico B/s)."""
    ffmpeg_bin = find_ffmpeg()
    total_dur, src_w, src_h = probe_video_info(video_path)
    act_box = compute_active_box(src_w, src_h, aspect_mode)
    act_w, act_h, pad_x, pad_y = act_box

    pcm_bytes = extract_segment_pcm(
        video_path, start_sec, dur_sec, work_dir,
        audio_mode=audio_mode, vocal_clarity=vocal_clarity
    )

    color_matrix = probe_color_matrix(video_path, src_w, src_h)
    vf_full = build_video_filter(fps, video_eq_mode, aspect_mode, act_box, color_matrix, tint_fix)

    cmd = [ffmpeg_bin, "-y"]
    if start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    if dur_sec is not None:
        cmd += ["-t", f"{dur_sec:.3f}"]
    cmd += [
        "-i", video_path,
        "-vf", vf_full,
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ]

    # Leer fotogramas por tubería en streaming para no consumir mucha RAM en vídeos largos
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW
    )

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
    base_budget = int(max(1200, base_budget * budget_scale))
    if max_frame_budget is not None and max_frame_budget > 0:
        base_budget = int(min(base_budget, max(1600, max_frame_budget)))
    max_peak_budget = int(min(14500, max(2000, base_budget * 2.2)))
    byte_reservoir = min(4000, base_budget // 2)
    frame_bytes_log = []

    while True:
        f_raw = proc.stdout.read(frame_byte_size)
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

        # Refrescar bandas negras en 2 fotogramas consecutivos (ambos bancos Word RAM) cada 48 fotogramas
        border_refresh = (f_idx % 48) in (0, 1)
        pal_count_out, pal_bytes, pal_map_bytes, encoded_tiles_payload, cur_tinds = quantize_and_encode_frame(
            rgb, act_box, num_palettes=num_palettes, prev2_tinds=prev2_tinds, target_frame_bytes=t_budget,
            force_border_refresh=border_refresh
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
        byte_reservoir = int(np.clip(byte_reservoir + (base_budget - length), -4000, 24000))
        sample_data.extend(sm_frame)
        stab_entries.append((offset, length, img_time_int, img_dur_int))
        frame_bytes_log.append(len(sample_data) - bytes_before)

        img_time_current += frame_duration
        f_idx += 1

        if progress_cb and (f_idx % 5 == 0 or f_idx == expected_frames):
            progress_cb(f_idx, max(f_idx, expected_frames), length)

    proc.stdout.close()
    return_code = proc.wait()
    if return_code:
        raise RuntimeError(f"ffmpeg terminó con error {return_code}; se descarta el capítulo incompleto.")

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

    with open(out_cin_path, "wb") as f:
        f.write(film_header)
        f.write(sample_data)

    return len(film_header) + len(sample_data), f_idx, avg_bps, peak_bps


def encode_segment_to_cin(
    video_path, start_sec, dur_sec, fps, aspect_mode, num_palettes,
    out_cin_path, work_dir, progress_cb=None, max_frame_budget=None,
    audio_mode="cinema_boost", vocal_clarity=True, video_eq_mode="svideo_hdmi",
    tint_fix="auto", note_cb=None
):
    """
    Codifica un vídeo (o segmento) a .CIN (Cinepak for Sega FILM/SM) comprobando que el caudal medio y
    el pico no superan lo que puede leer un Mega CD (1x). Si una pasada se pasa de caudal, se repite con
    un presupuesto menor (hasta 4 pasadas) o cancela la conversión si no cumple los límites medidos.
    """
    scale = 1.0
    size = n_frames = 0
    for attempt in range(4):
        size, n_frames, avg_bps, peak_bps = _encode_segment_pass(
            video_path, start_sec, dur_sec, fps, aspect_mode, num_palettes,
            out_cin_path, work_dir, progress_cb=progress_cb, max_frame_budget=max_frame_budget,
            audio_mode=audio_mode, vocal_clarity=vocal_clarity, video_eq_mode=video_eq_mode,
            tint_fix=tint_fix, budget_scale=scale
        )
        measured = inspect_cin(out_cin_path, fps)
        avg_bps, peak_bps = measured["media_B_s"], measured["pico_6s_B_s"]
        if measured["seguro"]:
            break
        worst = max(avg_bps / SAFE_TOTAL_BPS, peak_bps / SAFE_WINDOW_BPS)
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
    emit_pc_rel16(0x6000, "FinishCurrentVideo")  # EOF no es un fallo de FilmOpen

    mark("OpenFailed")
    # FilmOpen ya contiene cuatro intentos. No llamar a FilmTask ni enviar
    # FilmClose: un Open fallido todavía no envió el comando 3 al Main.
    emit_pc_rel16(0x41FA, "OpenFailures")
    emit_w(0x5250)
    emit_w(0x3010)
    emit(b"\x0C\x40")
    emit_w(len(sector_list))
    emit_pc_rel16(0x6C00, "SubCPULoop")
    emit_pc_rel16(0x43FA, "CurOffset")
    emit_w(0x5851)
    emit_pc_rel16(0x6000, "PlayCurrentVideo")

    mark("FinishLogicalChapter")
    emit_pc_rel16(0x43FA, "CurOffset")
    emit_w(0x3011)
    emit_pc_rel16(0x45FA, "NextOffsets")
    emit(b"\x32\xB2\x00\x00")  # MOVE.W (A2,D0.W),(A1)
    emit_w(0x5951)  # compensar el incremento común de cuatro
    mark("FinishCurrentVideo")
    emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
    emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
    emit_w(0x548F)                             # ADDQ.L #2, A7
    emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
    emit(b"\x58\x51")                          # ADDQ.W #4, (A1)
    emit_pc_rel16(0x6000, "PlayCurrentVideo")  # BRA.W PlayCurrentVideo

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
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
        emit_w(0x3011)
        emit_pc_rel16(0x45FA, "NextOffsets")
        emit(b"\x32\xB2\x00\x00")
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        mark("ActionPrevTrack")
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x43FA, "CurOffset")         # LEA CurOffset(PC), A1
        emit_w(0x3011)
        emit_pc_rel16(0x45FA, "PrevOffsets")
        emit(b"\x32\xB2\x00\x00")
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        mark("ActionRewind")
        emit_pc_rel16(0x43FA, "CurOffset")
        emit_w(0x3011)
        emit_pc_rel16(0x45FA, "StartOffsets")
        emit(b"\x32\xB2\x00\x00")
        emit(b"\x3F\x3C\x00\x02")                  # MOVE.W #2, -(A7) -> FilmClose
        emit_pc_rel16(0x4EBA, "Cinepak")           # JSR Cinepak(PC)
        emit_w(0x548F)                             # ADDQ.L #2, A7
        emit_pc_rel16(0x6000, "PlayCurrentVideo")

        mark("ActionFastForward")
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

        # Fijar '0'..'9' en los índices 0..9 para actualización directa de CAP XX/YY en ensamblador
        charset_chars = list("0123456789")
        for s in row22_variants + [row23_str]:
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
        if len(code) % 2 == 1:
            emit(b"\x00")

    mark("SavedMenuColor")
    emit_w(0)
    mark("OpenFailures")
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

        ini_path = os.path.join(tempfile.gettempdir(), "ImgBurn_MegaCD_7.ini")
        try:
            with open(ini_path, "w", encoding="ascii") as f:
                f.write("[Settings]\r\n")
                f.write("WRITE_WriteType=0\r\n")     # DAO/SAO óptimo para SEGA Mega CD
                f.write("WRITE_Verify=0\r\n")
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
            "/VERIFY", "NO",
            "/TESTMODE", "NO",
            "/EJECT", "YES",
            "/START",
            "/WAITFORMEDIA",
            "/CLOSESUCCESS",
            "/PORTABLE",
            "/NOSAVESETTINGS",
            "/SETTINGS", ini_path,
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

try {{
    $recorder.EjectMedia()
}} catch {{}}
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
    return True


UI_BG = "#14161f"


class ScrollFrame(ttk.Frame):
    """Contenedor con desplazamiento vertical que ocupa siempre todo el ancho de la ventana.
    Si hay espacio de sobra, el contenido se estira (los paneles con expand=True crecen);
    si la ventana es pequeña, aparece la barra de desplazamiento."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=UI_BG)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self._relayout())
        self.canvas.bind("<Configure>", lambda e: self._relayout())

    def _relayout(self):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        req_h = self.inner.winfo_reqheight()
        h = max(req_h, ch)
        self.canvas.itemconfigure(self._win, width=cw, height=h)
        self.canvas.configure(scrollregion=(0, 0, cw, h))


class FlowFrame(ttk.Frame):
    """Contenedor que coloca sus hijos de izquierda a derecha y salta de línea cuando no caben
    (usa place con altura calculada, así cada fila es independiente y nada se ensancha de más)."""

    def __init__(self, parent, hgap=8, vgap=4, style="Panel.TFrame"):
        super().__init__(parent, style=style)
        self.hgap, self.vgap = hgap, vgap
        self.items = []
        self._key = None
        self.bind("<Configure>", lambda e: self._reflow())

    def add(self, widget):
        self.items.append(widget)
        self._reflow()
        return widget

    def _reflow(self):
        width = self.winfo_width()
        sizes = [(w.winfo_reqwidth(), w.winfo_reqheight()) for w in self.items]
        key = (width, tuple(sizes))
        if key == self._key:
            return
        self._key = key
        avail = width if width > 1 else 10 ** 6
        rows, cur, x = [], [], 0
        for w, (sw, sh) in zip(self.items, sizes):
            if cur and x + sw > avail:
                rows.append(cur)
                cur, x = [], 0
            cur.append((w, x, sw, sh))
            x += sw + self.hgap
        if cur:
            rows.append(cur)
        y = 0
        for row in rows:
            row_h = max(sh for (_w, _x, _sw, sh) in row)
            for w, px, _sw, sh in row:
                w.place(x=px, y=y + (row_h - sh) // 2)
            y += row_h + self.vgap
        self.configure(height=max(1, y))


class FormGrid(ttk.Frame):
    """Formulario de campos (etiqueta + control) que se reparte en 1, 2 o 3 columnas según el ancho."""

    def __init__(self, parent, min_col_px=330, max_cols=3):
        super().__init__(parent, style="Panel.TFrame")
        self.min_col_px, self.max_cols = min_col_px, max_cols
        self.cells = []
        self._state = None
        self.bind("<Configure>", lambda e: self._reflow())

    def add(self, label, factory):
        cell = ttk.Frame(self, style="Panel.TFrame")
        ttk.Label(cell, text=label).pack(anchor="w")
        widget = factory(cell)
        widget.pack(fill="x", pady=(2, 0))
        self.cells.append(cell)
        self._reflow()
        return cell, widget

    def _reflow(self):
        width = self.winfo_width()
        n = max(1, min(self.max_cols, width // self.min_col_px)) if width > 1 else 1
        state = (n, len(self.cells))
        if state == self._state:
            return
        self._state = state
        for c in self.cells:
            c.grid_forget()
        for i in range(self.max_cols):
            self.columnconfigure(i, weight=0, uniform="")
        for i in range(n):
            self.columnconfigure(i, weight=1, uniform="form")
        for i, cell in enumerate(self.cells):
            last = (i % n) == n - 1
            cell.grid(row=i // n, column=i % n, sticky="new", padx=(0, 0 if last else 14), pady=4)


class MegaCDVideoStudioApp:
    """Interfaz gráfica principal de SEGA Mega CD Video Studio 7.0."""

    def __init__(self, root):
        self.root = root
        self.root.title("Mega CD Grabador de Vídeo Beta")
        self.root.configure(bg="#14161f")
        self._init_window_geometry()

        self.videos = []  # Lista de dicts: {path, name, duration, width, height, segments}
        self.is_converting = False
        self.last_iso_path = None
        self.last_cue_path = None
        self.burners_list = []

        self._setup_styles()
        self._build_ui()
        self._check_existing_disc()
        self.refresh_burners()

    # ------------------------------------------------------------------
    # Ventana y estilos adaptables (v7.1: responsive a cualquier resolución / DPI)
    # ------------------------------------------------------------------
    def _init_window_geometry(self):
        r = self.root
        r.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w = int(min(1200, sw * 0.92))
        h = int(min(960, sh * 0.90))
        w = max(w, min(520, sw))
        h = max(h, min(420, sh))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 16)
        r.geometry(f"{w}x{h}+{x}+{y}")
        r.minsize(min(520, sw), min(420, sh))
        # Escala tipográfica según la altura útil de la pantalla (el DPI lo gestiona Tk)
        self.ui_font_scale = 1.0 if sh >= 1000 else (0.93 if sh >= 820 else 0.86)
        self.font_family = "Segoe UI"

    def _fs(self, size):
        return max(7, int(round(size * self.ui_font_scale)))

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        bg_dark = "#14161f"
        bg_panel = "#1e2230"
        bg_field = "#10131c"
        fg_text = "#e8ecf5"
        accent = "#2b6cb0"
        accent_active = "#3182ce"
        ff = self.font_family

        style.configure(".", background=bg_dark, foreground=fg_text, font=(ff, self._fs(10)))
        style.configure("TFrame", background=bg_dark)
        style.configure("Panel.TFrame", background=bg_panel, relief="flat")
        style.configure("TLabelframe", background=bg_panel, foreground="#7fb3ff", font=(ff, self._fs(10), "bold"))
        style.configure("TLabelframe.Label", background=bg_panel, foreground="#7fb3ff", font=(ff, self._fs(10), "bold"))
        style.configure("TLabel", background=bg_panel, foreground=fg_text)
        style.configure("Header.TLabel", background=bg_dark, foreground="#ffffff", font=(ff, self._fs(16), "bold"))
        style.configure("SubHeader.TLabel", background=bg_dark, foreground="#9aa5be", font=(ff, self._fs(10)))
        style.configure("Status.TLabel", background=bg_dark, foreground=fg_text, font=(ff, self._fs(9)))
        style.configure("Hint.TLabel", background=bg_panel, foreground="#e0b34d", font=(ff, self._fs(9)))
        style.configure("TCheckbutton", background=bg_panel, foreground=fg_text)
        style.map("TCheckbutton", background=[("active", bg_panel)], foreground=[("active", "#ffffff")])
        style.configure("TButton", background="#2d3448", foreground="#ffffff", padding=(10, 5), font=(ff, self._fs(9), "bold"))
        style.map("TButton", background=[("active", "#3e4863"), ("disabled", "#1b1e29")], foreground=[("disabled", "#6c7693")])
        style.configure("Accent.TButton", background=accent, foreground="#ffffff", padding=(14, 8), font=(ff, self._fs(11), "bold"))
        style.map("Accent.TButton", background=[("active", accent_active), ("disabled", "#253042")])
        style.configure("Treeview", background="#10121a", fieldbackground="#10121a", foreground="#e8ecf5", rowheight=int(24 * self.ui_font_scale) + 2)
        style.configure("Treeview.Heading", background="#262c3f", foreground="#ffffff", font=(ff, self._fs(9), "bold"))
        style.map("Treeview", background=[("selected", "#2b6cb0")], foreground=[("selected", "#ffffff")])

        style.configure(
            "TEntry", fieldbackground=bg_field, background=bg_field, foreground="#ffffff", insertcolor="#ffffff",
            bordercolor="#3b4664", lightcolor="#3b4664", darkcolor="#3b4664",
            selectbackground=accent, selectforeground="#ffffff", padding=4
        )
        style.map(
            "TEntry",
            fieldbackground=[("readonly", bg_field), ("disabled", "#181b26"), ("focus", "#151a28"), ("!disabled", bg_field)],
            foreground=[("readonly", "#ffffff"), ("disabled", "#7a849e"), ("focus", "#ffffff"), ("!disabled", "#ffffff")]
        )
        style.configure(
            "TCombobox", fieldbackground=bg_field, background="#2d3448", foreground="#ffffff", arrowcolor="#7fb3ff",
            bordercolor="#3b4664", lightcolor="#3b4664", darkcolor="#3b4664",
            selectbackground=bg_field, selectforeground="#ffffff", padding=4
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", bg_field), ("disabled", "#181b26"), ("focus", bg_field), ("active", bg_field), ("!disabled", bg_field)],
            background=[("readonly", "#2d3448"), ("active", "#3e4863"), ("!disabled", "#2d3448")],
            foreground=[("readonly", "#ffffff"), ("disabled", "#7a849e"), ("focus", "#ffffff"), ("active", "#ffffff"), ("!disabled", "#ffffff")],
            selectbackground=[("readonly", bg_field), ("focus", bg_field), ("!disabled", bg_field)],
            selectforeground=[("readonly", "#ffffff"), ("focus", "#ffffff"), ("!disabled", "#ffffff")],
            arrowcolor=[("readonly", "#7fb3ff"), ("active", "#ffffff"), ("!disabled", "#7fb3ff")]
        )
        style.configure("Vertical.TScrollbar", background="#2d3448", troughcolor=bg_dark, bordercolor=bg_dark, arrowcolor="#7fb3ff")

        self.root.option_add("*TCombobox*Listbox*Background", bg_field)
        self.root.option_add("*TCombobox*Listbox*Foreground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox*selectBackground", accent)
        self.root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox*Font", (ff, self._fs(10)))

    # ------------------------------------------------------------------
    # Rueda del ratón: desplaza la ventana sin cambiar valores de los desplegables
    # ------------------------------------------------------------------
    def _on_mousewheel(self, event):
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        if w is not None and w.winfo_class() in ("Treeview", "Listbox"):
            return None
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -3 if event.delta > 0 else 3
        self.scroll.canvas.yview_scroll(step, "units")
        return "break"

    def _combo(self, parent, var, values, on_select=None, width=10):
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", values=values, width=width)

        def _sel(e):
            e.widget.selection_clear()
            if on_select:
                on_select()
        cb.bind("<<ComboboxSelected>>", _sel)
        cb.bind("<MouseWheel>", self._on_mousewheel)
        cb.bind("<Button-4>", self._on_mousewheel)
        cb.bind("<Button-5>", self._on_mousewheel)
        return cb

    def _on_tree_configure(self, event):
        w = event.width
        allc = ("idx", "name", "res", "dur", "chapters", "est_mb", "path")
        if w >= 940:
            cols = allc
        elif w >= 720:
            cols = ("idx", "name", "res", "dur", "chapters", "est_mb")
        elif w >= 540:
            cols = ("idx", "name", "dur", "chapters", "est_mb")
        else:
            cols = ("idx", "name", "dur", "est_mb")
        if cols != self._tree_cols_state:
            self._tree_cols_state = cols
            self.tree.configure(displaycolumns=cols)

    def _build_ui(self):
        self._tree_cols_state = None
        self.scroll = ScrollFrame(self.root)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.inner
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel)
        self.root.bind_all("<Button-5>", self._on_mousewheel)
        pad = 14

        # Cabecera
        hdr_frame = ttk.Frame(body)
        hdr_frame.pack(fill="x", padx=pad, pady=(10, 4))
        ttk.Label(hdr_frame, text="MEGA CD GRABADOR DE VÍDEO (BETA)", style="Header.TLabel").pack(anchor="w")
        sub_lbl = ttk.Label(
            hdr_frame,
            text="Cinepak for Sega (.CIN) para Mega CD PAL: audio RF5C164 al 100% sin soplido, menú START (CAP XX/YY), "
                 "perfiles de imagen S-Video/HDMI con corrección de tinte y grabación con ImgBurn integrado.",
            style="SubHeader.TLabel", justify="left"
        )
        sub_lbl.pack(anchor="w", fill="x", pady=(2, 0))
        hdr_frame.bind("<Configure>", lambda e: sub_lbl.configure(wraplength=max(200, e.width - 8)))

        # Panel 1: Lista de vídeos
        list_lf = ttk.LabelFrame(body, text=" 1. Vídeos a incluir en el CD (uno o varios) ")
        list_lf.pack(fill="both", expand=True, padx=pad, pady=4)

        tree_frame = ttk.Frame(list_lf, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        cols = ("idx", "name", "res", "dur", "chapters", "est_mb", "path")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=4)
        heads = {"idx": "#", "name": "Archivo de vídeo", "res": "Resolución", "dur": "Duración",
                 "chapters": "Capítulos", "est_mb": "Tamaño est.", "path": "Ruta completa"}
        for c, t in heads.items():
            self.tree.heading(c, text=t)
        self.tree.column("idx", width=34, minwidth=28, anchor="center", stretch=False)
        self.tree.column("name", width=200, minwidth=90, anchor="w")
        self.tree.column("res", width=90, minwidth=60, anchor="center", stretch=False)
        self.tree.column("dur", width=80, minwidth=60, anchor="center", stretch=False)
        self.tree.column("chapters", width=90, minwidth=60, anchor="center", stretch=False)
        self.tree.column("est_mb", width=90, minwidth=60, anchor="center", stretch=False)
        self.tree.column("path", width=260, minwidth=80, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Configure>", self._on_tree_configure)

        btn_bar = FlowFrame(list_lf)
        btn_bar.pack(fill="x", padx=10, pady=(4, 6))
        for txt, cmd in (
            ("+ Añadir vídeo(s)...", self.add_videos), ("Añadir carpeta...", self.add_folder),
            ("↑ Subir", self.move_up), ("↓ Bajar", self.move_down),
            ("Quitar seleccionado", self.remove_selected), ("Limpiar lista", self.clear_videos),
        ):
            btn_bar.add(ttk.Button(btn_bar, text=txt, command=cmd))
        self.cap_label = ttk.Label(btn_bar, text="Capacidad CD (700 MB): 0.0 MB (0%)", font=(self.font_family, self._fs(9), "bold"))
        btn_bar.add(self.cap_label)

        # Panel 2: Parámetros
        opts_lf = ttk.LabelFrame(body, text=" 2. Configuración técnica, audio e imagen ")
        opts_lf.pack(fill="x", padx=pad, pady=4)

        form = FormGrid(opts_lf, min_col_px=330, max_cols=3)
        form.pack(fill="x", padx=10, pady=(6, 2))

        self.region_var = tk.StringVar(value="E - Europa / España (Mega CD PAL 50Hz)")
        form.add("Región de la consola:", lambda p: self._combo(p, self.region_var, [
            "E - Europa / España (Mega CD PAL 50Hz)",
            "U - Estados Unidos (Sega CD NTSC 60Hz)",
            "J - Japón (Mega CD NTSC-J 60Hz)",
        ]))

        self.pal_var = tk.StringVar(value="4 Paletas VDP por fotograma (Hasta 60 colores - Alta Calidad)")
        form.add("Calidad de color VDP:", lambda p: self._combo(p, self.pal_var, [
            "4 Paletas VDP por fotograma (Hasta 60 colores - Alta Calidad)",
            "2 Paletas VDP por fotograma (Hasta 30 colores - Equilibrado)",
            "1 Paleta VDP por fotograma (15 colores - Más rápido)",
        ], on_select=self._refresh_tree))

        self.fps_var = tk.StringVar(value="15 FPS (Alta fluidez - Nitidez True-RGB sin artefactos)")
        form.add("Fotogramas / seg (FPS):", lambda p: self._combo(p, self.fps_var, [
            "8 FPS (Máxima nitidez lossless ~2.5 horas en CD)",
            "10 FPS (Estándar clásico Tomcat Alley - Alta nitidez)",
            "12 FPS (Fluido estándar - Alta nitidez)",
            "15 FPS (Alta fluidez - Nitidez True-RGB sin artefactos)",
            "20 FPS (Ultra fluido - Con sincronía A/V activa)",
            "24 FPS (Cine 24p - Con sincronía A/V activa)",
        ], on_select=self._refresh_tree))

        self.aspect_var = tk.StringVar(value="Automático (Mantener proporción original con bandas tile-skip)")
        form.add("Ajuste de pantalla:", lambda p: self._combo(p, self.aspect_var, [
            "Automático (Mantener proporción original con bandas tile-skip)",
            "Pantalla Completa 256x192 (Estirar a 4:3)",
            "Recortar al Centro 256x192 (Zoom / Rellenar 4:3)",
        ]))

        self.audio_mode_var = tk.StringVar(value="Alto Cine (+8 dB + Normalizador de Diálogos - Recomendado)")
        form.add("Potencia de audio PCM:", lambda p: self._combo(p, self.audio_mode_var, [
            "Alto Cine (+8 dB + Normalizador de Diálogos - Recomendado)",
            "Máximo (+12 dB Boost Extremo para películas muy bajas)",
            "Estándar (+4 dB Equilibrado sin ruido)",
            "Original (0 dB Directo con volumen chip al 100%)",
        ]))

        self.video_eq_var = tk.StringVar(value="S-Video / Conversor HDMI (Realce de escenas oscuras - Recomendado)")
        form.add("Perfil de brillo TV / cable:", lambda p: self._combo(p, self.video_eq_var, [
            "S-Video / Conversor HDMI (Realce de escenas oscuras - Recomendado)",
            "Estándar Mega CD (Gamma 1.12 equilibrado)",
            "Alto Contraste / TV CRT (Colores intensos)",
        ]))

        self.tint_var = tk.StringVar(value="Anti-rosa: Suave (recomendado)")
        form.add("Corrección de tinte (rosa/magenta):", lambda p: self._combo(p, self.tint_var, [
            "Anti-rosa: Suave (recomendado)",
            "Anti-rosa: Fuerte (si aún se ve rosado)",
            "Anti-rosa: Desactivado",
        ]))

        self.chapter_dur_var = tk.StringVar(value="3 min por capítulo (Estándar - Saltos precisos en menú START)")
        cell, _cb = form.add("Duración por capítulo:", lambda p: self._combo(p, self.chapter_dur_var, [
            "1 min por capítulo (Saltos rápidos estilo DVD)",
            "2 min por capítulo (Saltos ágiles)",
            "3 min por capítulo (Estándar - Saltos precisos en menú START)",
            "5 min por capítulo (Capítulos largos)",
            "10 min por capítulo (Capítulos largos)",
        ], on_select=self._refresh_tree))
        self.chap_hint_var = tk.StringVar(value="")
        chap_hint = ttk.Label(cell, textvariable=self.chap_hint_var, style="Hint.TLabel", justify="left")
        chap_hint.pack(anchor="w", fill="x")
        cell.bind("<Configure>", lambda e: chap_hint.configure(wraplength=max(120, e.width - 8)))

        self.title_var = tk.StringVar(value="VIDEO FMV MEGA CD PAL")
        form.add("Título interno del CD:", lambda p: ttk.Entry(p, textvariable=self.title_var, width=10))

        chk = FlowFrame(opts_lf, hgap=18)
        chk.pack(fill="x", padx=10, pady=(2, 8))
        self.vocal_clarity_var = tk.BooleanVar(value=True)
        self.loop_var = tk.BooleanVar(value=True)
        self.skip_var = tk.BooleanVar(value=True)
        self.osd_menu_var = tk.BooleanVar(value=True)
        self.fit_cd_var = tk.BooleanVar(value=True)
        chk.add(ttk.Checkbutton(chk, text="🔊 Claridad vocal Mega CD (2.6 kHz + puerta de silencio)", variable=self.vocal_clarity_var))
        chk.add(ttk.Checkbutton(chk, text="Repetir en bucle al terminar", variable=self.loop_var))
        chk.add(ttk.Checkbutton(chk, text="Saltar capítulo con botón (A/B/C)", variable=self.skip_var))
        chk.add(ttk.Checkbutton(chk, text="★ Menú multimedia con START (Play, Pausa, CAP, Rew, Fwd, Stop)", variable=self.osd_menu_var))
        chk.add(ttk.Checkbutton(chk, text="💿 Ajustar como máximo a 1 CD (700 MB)", variable=self.fit_cd_var, command=self._refresh_tree))

        # Panel 3: Salida y grabadora
        out_lf = ttk.LabelFrame(body, text=" 3. Carpeta de salida y grabación directa en CD (ImgBurn integrado) ")
        out_lf.pack(fill="x", padx=pad, pady=4)

        out_row = ttk.Frame(out_lf, style="Panel.TFrame")
        out_row.pack(fill="x", padx=10, pady=(6, 4))
        out_row.columnconfigure(1, weight=1)
        default_out = os.path.join(os.path.expanduser("~"), "Desktop", "Disco_MegaCD_PAL_Preparado")
        self.out_dir_var = tk.StringVar(value=default_out)
        ttk.Label(out_row, text="Carpeta de destino:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(out_row, textvariable=self.out_dir_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(out_row, text="Examinar...", command=self.browse_out_dir).grid(row=0, column=2)

        burn_row = FlowFrame(out_lf, hgap=10)
        burn_row.pack(fill="x", padx=10, pady=(4, 8))
        burn_row.add(ttk.Label(burn_row, text="Grabadora de CD:"))
        self.burner_var = tk.StringVar(value="Detección automática (Grabadora ASUS / CD-ROM)")
        self.burner_cb = self._combo(burn_row, self.burner_var, [], width=34)
        burn_row.add(self.burner_cb)
        burn_row.add(ttk.Button(burn_row, text="↻ Detectar", command=self.refresh_burners))
        burn_row.add(ttk.Label(burn_row, text="Velocidad:"))
        self.burn_speed_var = tk.StringVar(value="4x (600 KB/s - Recomendado Mega CD)")
        burn_row.add(self._combo(burn_row, self.burn_speed_var, [
            "4x (600 KB/s - Recomendado Mega CD)",
            "8x (1200 KB/s - Seguro)",
            "16x (2400 KB/s - Rápido)",
        ], width=28))
        self.auto_burn_var = tk.BooleanVar(value=False)
        burn_row.add(ttk.Checkbutton(burn_row, text="Grabar CD automáticamente al terminar", variable=self.auto_burn_var))

        # Panel 4: Progreso y botones
        act_lf = ttk.Frame(body)
        act_lf.pack(fill="x", padx=pad, pady=(4, 10))

        self.progress_var = tk.DoubleVar(value=0.0)
        self.pbar = ttk.Progressbar(act_lf, variable=self.progress_var, maximum=100.0)
        self.pbar.pack(fill="x", expand=True, pady=(2, 5))

        self.status_var = tk.StringVar(
            value="Listo. Añade un vídeo y pulsa Convertir, Prueba Rápida 15s o Grabar CD."
        )
        status_lbl = ttk.Label(act_lf, textvariable=self.status_var, style="Status.TLabel", justify="left")
        status_lbl.pack(anchor="w", fill="x", pady=(0, 6))
        act_lf.bind("<Configure>", lambda e: status_lbl.configure(wraplength=max(200, e.width - 8)))

        btn_row = FlowFrame(act_lf, hgap=8, vgap=6, style="TFrame")
        btn_row.pack(fill="x")
        self.convert_btn = ttk.Button(btn_row, text="▶  CONVERTIR Y CREAR DISCO MEGA CD (.ISO / .CUE)", style="Accent.TButton", command=self.start_conversion)
        btn_row.add(self.convert_btn)
        self.burn_btn = ttk.Button(btn_row, text="💿  GRABAR CD MEGA CD (ImgBurn)", style="Accent.TButton", command=self.start_burn_cd)
        btn_row.add(self.burn_btn)
        self.preview_btn = ttk.Button(btn_row, text="⚡ Prueba rápida (15s en Fusion)", command=self.start_quick_preview)
        btn_row.add(self.preview_btn)
        self.fusion_btn = ttk.Button(btn_row, text="Probar disco en Fusion", command=self.launch_in_fusion, state="disabled")
        btn_row.add(self.fusion_btn)
        btn_row.add(ttk.Button(btn_row, text="Abrir carpeta de salida", command=self.open_out_dir))

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

    def _compute_max_frame_budget(self, fps):
        """
        Si la opción 'Ajustar como máximo a la capacidad de 1 CD (700 MB)' está activa,
        calcula el presupuesto máximo de bytes por fotograma para que la suma de todos los
        vídeos (.CIN + cabeceras + sistema Mega CD) nunca supere 685 MB (~97.8% de un CD de 700 MB).
        """
        if not hasattr(self, "fit_cd_var") or not self.fit_cd_var.get():
            return None
        total_dur = sum(max(0.1, v["duration"]) for v in self.videos)
        if total_dur <= 0:
            return None
        max_cin_bytes = 685.0 * 1024.0 * 1024.0
        total_bps_avail = max_cin_bytes / total_dur
        video_bps_avail = max(24000.0, total_bps_avail - 17000.0)
        max_fb = int(video_bps_avail / max(1, fps))
        default_fb = default_frame_budget(fps)
        return max_fb if max_fb < default_fb else None

    def _refresh_tree(self):
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
        base_budget = default_frame_budget(fps)
        max_fb = self._compute_max_frame_budget(fps)
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
            est_mb = (dur * kbps_est) / 1024.0
            total_mb += est_mb
            mins = int(dur // 60)
            secs = dur % 60
            dur_str = f"{mins:02d}:{secs:04.1f}"
            seg_str = f"{n_seg} cap." if n_seg > 1 else "1 (.CIN)"
            self.tree.insert(
                "", "end", iid=str(idx),
                values=(idx + 1, v["name"], f"{v['width']}x{v['height']}", dur_str, seg_str, f"{est_mb:.1f} MB", v["path"])
            )

        if hasattr(self, "fit_cd_var") and self.fit_cd_var.get() and total_mb > 685.0:
            total_mb = 685.0
        pct = (total_mb / CD_CAPACITY_MB) * 100.0
        fit_tag = " [Ajustado a 1 CD]" if max_fb is not None else ""
        self.cap_label.configure(text=f"Capacidad CD (700 MB): {total_mb:.1f} MB ({pct:.1f}%){fit_tag}")

    def _check_existing_disc(self):
        out_dir = self.out_dir_var.get().strip()
        if os.path.isdir(out_dir):
            for fn in os.listdir(out_dir):
                if fn.upper().endswith(".CUE"):
                    cue_p = os.path.join(out_dir, fn)
                    iso_p = os.path.splitext(cue_p)[0] + ".ISO"
                    if os.path.isfile(iso_p):
                        self.last_cue_path = cue_p
                        self.last_iso_path = iso_p
                        self.fusion_btn.configure(state="normal")
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
        self._add_video_paths(paths)

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
        self._add_video_paths(paths)

    def _add_video_paths(self, paths):
        self.status_var.set("Analizando vídeo(s) seleccionado(s)...")
        self.root.update_idletasks()
        for p in paths:
            dur, w, h = probe_video_info(p)
            if dur <= 0:
                dur = 10.0
            self.videos.append({
                "path": p,
                "name": os.path.basename(p),
                "duration": dur,
                "width": w,
                "height": h,
                "segments": 1
            })
        self._refresh_tree()
        self.status_var.set(f"{len(self.videos)} vídeo(s) en la lista listos para convertir.")

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
        idx = int(sel[0])
        if 0 <= idx < len(self.videos):
            del self.videos[idx]
            self._refresh_tree()

    def clear_videos(self):
        if self.is_converting:
            return
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
        reg = self._get_selected_region()
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

        t = threading.Thread(target=self._quick_preview_worker, args=(v_info,), daemon=True)
        t.start()

    def _quick_preview_worker(self, v_info):
        try:
            fps = self._get_selected_fps()
            region = self._get_selected_region()
            num_palettes = self._get_selected_palettes()
            aspect_mode = self._get_selected_aspect()
            audio_mode = self._get_selected_audio_mode()
            vocal_clarity = bool(self.vocal_clarity_var.get())
            video_eq_mode = self._get_selected_video_eq()
            tint_fix = self._get_selected_tint_fix()
            enable_osd = bool(self.osd_menu_var.get())

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
                out_cin, work_dir, progress_cb=on_prog, max_frame_budget=None,
                audio_mode=audio_mode, vocal_clarity=vocal_clarity, video_eq_mode=video_eq_mode,
                tint_fix=tint_fix
            )

            iso_path, cue_path, _ = build_megacd_disc(
                [out_cin], preview_dir, region=region, disc_title="PRUEBA 15S MEGA CD",
                loop_playback=True, allow_skip=True, enable_osd=enable_osd
            )
            shutil.rmtree(work_dir, ignore_errors=True)

            def on_done():
                self.is_converting = False
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.progress_var.set(100.0)
                self.status_var.set("¡Prueba rápida de 15s lista! Abriendo en Kega Fusion...")
                self._launch_cue_in_fusion(cue_path)

            self.root.after(0, on_done)
        except Exception as e:
            err_msg = str(e)
            def on_err():
                self.is_converting = False
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.status_var.set(f"Error en prueba rápida: {err_msg}")
                messagebox.showerror("Error en Prueba Rápida", err_msg)
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
        if iso_mb > 703.0:
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

        t = threading.Thread(target=self._conversion_worker, args=(out_dir,), daemon=True)
        t.start()

    def _conversion_worker(self, out_dir):
        t0 = time.time()
        try:
            fps = self._get_selected_fps()
            region = self._get_selected_region()
            num_palettes = self._get_selected_palettes()
            aspect_mode = self._get_selected_aspect()
            audio_mode = self._get_selected_audio_mode()
            vocal_clarity = bool(self.vocal_clarity_var.get())
            video_eq_mode = self._get_selected_video_eq()
            tint_fix = self._get_selected_tint_fix()
            chap_sec = self._get_selected_chapter_sec()
            req_chap_sec = self._get_requested_chapter_sec()
            def note(msg):
                self.root.after(0, lambda m=msg: self.status_var.set(m))
            disc_title = self.title_var.get().strip() or "VIDEO FMV MEGA CD"
            loop_playback = bool(self.loop_var.get())
            allow_skip = bool(self.skip_var.get())
            enable_osd = bool(self.osd_menu_var.get())
            max_frame_budget = self._compute_max_frame_budget(fps)
            auto_burn = bool(self.auto_burn_var.get())

            # Conservar la duración elegida y dividir solamente los CIN técnicos.
            segment_tasks = []
            chapter_ids = []
            chapter_base = 0
            for v in self.videos:
                plan = chapter_plan(v["duration"], req_chap_sec, fps)
                for part in plan:
                    segment_tasks.append((v["path"], v["name"], part["start"], part["duration"], part["part"], part["parts"]))
                    chapter_ids.append(chapter_base + part["logical"])
                chapter_base += max((p["logical"] for p in plan), default=0)
            if chapter_base > 99:
                raise RuntimeError("Este disco supera 99 capítulos lógicos; elige capítulos más largos.")

            total_segments = len(segment_tasks)
            work_dir = tempfile.mkdtemp(prefix="megacd_studio_")
            cin_paths = []

            for seg_idx, (vpath, vname, st_sec, dl_sec, part_num, part_total) in enumerate(segment_tasks):
                cin_name = "VIDEO.CIN" if total_segments == 1 else f"VID{seg_idx + 1:03d}.CIN"
                out_cin = os.path.join(work_dir, cin_name)

                part_label = f" (Cap. {part_num}/{part_total})" if part_total > 1 else ""

                def on_prog(f_cur, f_tot, last_bytes, s_idx=seg_idx, nm=vname, pl=part_label):
                    seg_frac = min(1.0, f_cur / max(1, f_tot))
                    global_pct = ((s_idx + seg_frac) / total_segments) * 92.0
                    self.root.after(0, lambda: (
                        self.progress_var.set(global_pct),
                        self.status_var.set(
                            f"Codificando [{s_idx + 1}/{total_segments}] {nm}{pl}: "
                            f"fotograma {f_cur}/{f_tot} ({last_bytes:,} bytes, {num_palettes} paletas VDP, Audio: {audio_mode.upper()})..."
                        )
                    ))

                encode_segment_to_cin(
                    vpath, st_sec, dl_sec, fps, aspect_mode, num_palettes,
                    out_cin, work_dir, progress_cb=on_prog, max_frame_budget=max_frame_budget,
                    audio_mode=audio_mode, vocal_clarity=vocal_clarity, video_eq_mode=video_eq_mode,
                    tint_fix=tint_fix, note_cb=note
                )
                cin_paths.append(out_cin)

            chap_note = ""
            if chap_sec < req_chap_sec - 1:
                chap_note = (f"• Capítulos lógicos: {format_mmss(req_chap_sec)}; CIN técnicos de hasta {format_mmss(chap_sec)}.\n"
                             "• Las transiciones entre CIN pueden tener una pausa.\n")
            self.root.after(0, lambda: (
                self.progress_var.set(95.0),
                self.status_var.set("Ensamblando imagen de CD autoarrancable (.ISO + .CUE + Sectores de licencia Mega CD + Audio RF5C164 100%)...")
            ))

            iso_path, cue_path, readme_path = build_megacd_disc(
                cin_paths, out_dir, region=region, disc_title=disc_title,
                loop_playback=loop_playback, allow_skip=allow_skip, enable_osd=enable_osd, chapter_ids=chapter_ids
            )
            write_safety_report(out_dir, cin_paths, fps, chapter_ids)

            shutil.rmtree(work_dir, ignore_errors=True)
            elapsed = time.time() - t0
            iso_mb = os.path.getsize(iso_path) / (1024.0 * 1024.0)

            def on_done():
                self.is_converting = False
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.last_iso_path = iso_path
                self.last_cue_path = cue_path
                self.fusion_btn.configure(state="normal")
                self.progress_var.set(100.0)
                self.status_var.set(
                    f"¡Completado en {elapsed:.1f}s! Disco generado: {os.path.basename(iso_path)} ({iso_mb:.2f} MB)"
                )
                if auto_burn:
                    self.start_burn_cd()
                else:
                    messagebox.showinfo(
                        "Disco de Mega CD creado — Astra Beta",
                        f"Se ha generado el disco autoarrancable de Mega CD en:\n\n"
                        f"{out_dir}\n\n"
                        f"• Imagen ISO: {os.path.basename(iso_path)} ({iso_mb:.2f} MB)\n"
                        f"• Hoja CUE: {os.path.basename(cue_path)}\n"
                        f"• Capítulos lógicos: {chapter_base}; archivos CIN: {len(cin_paths)}\n"
                        f"• Informe: Informe_de_seguridad.txt\n"
                        f"• Audio PCM: Chip RF5C164 al 100% ($FF) + Masterizado 16 bits sin ruido ({audio_mode.upper()})\n"
                        f"• Menú Multimedia en START (con CAP XX/YY): {'ACTIVADO' if enable_osd else 'DESACTIVADO'}\n"
                        f"{chap_note}\n"
                        f"Puedes grabarlo directamente pulsando '💿 GRABAR CD MEGA CD (ImgBurn)' o probarlo en Kega Fusion."
                    )

            self.root.after(0, on_done)

        except Exception as e:
            err_msg = str(e)
            def on_err():
                self.is_converting = False
                self.convert_btn.configure(state="normal")
                self.burn_btn.configure(state="normal")
                self.preview_btn.configure(state="normal")
                self.status_var.set(f"Error: {err_msg}")
                messagebox.showerror("Error durante la conversión", f"Se produjo un error:\n{err_msg}")
            self.root.after(0, on_err)


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


def run_self_test(report_path):
    import json
    ip, main, sub = map(bytearray, (IP_BIN, MAINPLAY_BIN, SUBPLAY_BIN))
    patch_cinepak_players(ip, main, sub)
    patch = patch_main_controller(ip, main)
    sp = build_multi_sp_bin(bytes(sub), [25])
    ff = subprocess.run([find_ffmpeg(), "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=CREATE_NO_WINDOW, timeout=30)
    result = {"estado": "OK" if ff.returncode == 0 else "ERROR", "sp_bytes": len(sp),
              "main_extra_bytes": patch["extra_bytes"], "ffmpeg": ff.stdout.decode("utf-8", "replace").splitlines()[0],
              "imgburn_disponible": bool(find_imgburn()), "hardware": "No verificado en consola real"}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if ff.returncode:
        raise SystemExit(1)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        run_self_test(sys.argv[2])
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
