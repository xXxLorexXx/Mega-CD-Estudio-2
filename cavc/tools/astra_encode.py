"""Windows/WSL entry point for one CAVC output disc."""
from pathlib import Path
import atexit
import json
import hashlib
import ctypes
import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time

from runtime_paths import desktop_workspace


job = Path(sys.argv[1]).resolve()
settings = json.loads((job / "job.json").read_text(encoding="utf-8"))
region = settings["region"]
fps = settings["fps"]
width, height = settings["size"]
codec = settings.get("codec", "CAVC")
if codec not in ("CAVC", "LORE"):
    raise SystemExit(f"Códec desconocido: {codec}")
lore = codec == "LORE"
if lore and (region != "eu" or fps != 25):
    raise SystemExit("LORE requiere una consola PAL y un reloj de 25 FPS nominales.")
os.environ["ASTRA_CAVC_REGION"] = region
os.environ["SECURITY_REGION"] = region
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["M68K_PREFIX"] = "/usr/bin/m68k-linux-gnu-"
os.environ["PYTHON"] = sys.executable
os.environ["ASTRA_PROGRESS_FILE"] = str(job / "progress.jsonl")
# Keep temporary frames together so per-part materialization can use hardlinks.
default_workspace = Path.home() / ".local/share/mega-cd-estudio/cavc81-workspace"
override = os.environ.get("CAVC_WORKSPACE_ROOT")
if override:
    workspace_root = Path(override)
else:
    seconds = sum(float(item.get("length") or item.get("duration") or 0)
                  for item in settings["sources"])
    workspace_root = desktop_workspace(default_workspace, round(seconds * fps), width, height)
workspace_root.mkdir(parents=True, exist_ok=True)
workspace = tempfile.TemporaryDirectory(prefix=job.name + "-", dir=workspace_root)
atexit.register(workspace.cleanup)
os.environ["CAVC_WORKSPACE_ROOT"] = workspace.name
os.environ.setdefault("SEGACD_RESOURCE_ROOT", str(Path(override or default_workspace) / "resources"))
if workspace_root != default_workspace:
    print(f"Fotogramas temporales en {workspace_root}", flush=True)
# Accept jobs from the previous Windows launcher as well.
encoding_profile = settings.get("profile", "fast" if settings.get("fast", True) else "detailed")
if encoding_profile not in ("fast", "detailed", "sharp"):
    raise SystemExit("Perfil de codificación CAVC desconocido.")
if lore:
    encoding_profile = "sharp"
fast = encoding_profile == "fast"
detail_motion = settings.get("detail_motion", False)
os.environ["ASTRA_INPUT_CUDA"] = "0"
if settings.get("acceleration") != "cpu":
    try:
        ctypes.CDLL("libnvcuvid.so.1")
    except OSError:
        print("NVDEC no disponible en WSL. El vídeo de entrada se lee por CPU.", flush=True)
    else:
        os.environ["ASTRA_INPUT_CUDA"] = "1"
os.environ["ASTRA_DESKTOP_PARTS"] = "1"
os.environ["ASTRA_PART_WORKERS"] = str(settings.get("part_workers", 1))
# 14.1: each part seeks to its own range of the source (bit-identical frames)
# instead of one whole-film extraction that needs tens of GB of free space.
os.environ["ASTRA_SEEK_EXTRACT"] = "1"
os.environ["ASTRA_DISABLE_SHARED_INPUT"] = "1"
lore_audio = ('audio_rate = 18000\naudio_filter = "loudnorm=I=-19:TP=-2:LRA=11"'
              if lore else "")
lore_video = 'output_dither = "soft-bayer"' if lore else ""
lore_motion = ("prioritize_motion = true\nmotion_compensation = true\nadaptive_motion = true"
               if lore else ("prioritize_motion = true\nmotion_compensation = true" if detail_motion else ""))
os.environ.setdefault("NUMBA_CACHE_DIR", str(Path.home() / ".cache/mega-cd-estudio/numba"))
os.environ.setdefault("CBRSIM_LOOP_PROFILE", "0")

import av_config
import sim_artifact_cache
from astra_capacity import (CD_BYTES, MAX_PARTS, MAX_PART_FRAMES, MAX_TOTAL_PARTS, MEMORY_FIXED,
                            MEMORY_PER_FRAME, SCENE_SLACK, estimate, part_limit)
from encode_config import load_profile
from astra_progress import emit
from video_geometry import HAR_NUM, HAR_DEN, parse_ratio, raw_filter


def run(*args, allow_retry=False):
    with subprocess.Popen(args, start_new_session=True) as child:
        while child.poll() is None:
            if (job / "cancel").exists():
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                raise SystemExit(130)
            time.sleep(.2)
        if child.returncode and not (allow_retry and child.returncode == 1):
            raise SystemExit(child.returncode)
        return child.returncode == 0


def source_path(win):
    return subprocess.check_output(["wslpath", "-a", "-u", win], text=True).rstrip("\n")


def probe(path):
    data = subprocess.check_output(["ffprobe", "-v", "error", "-show_format",
                                    "-show_streams", "-of", "json", path])
    info = json.loads(data)
    video = next((stream for stream in info["streams"]
                  if stream["codec_type"] == "video"
                  and not stream.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise SystemExit(f"El archivo no contiene una pista de vídeo: {path}")
    audio = next((stream for stream in info["streams"] if stream["codec_type"] == "audio"), None)
    duration = float(info["format"].get("duration", "nan"))
    if not math.isfinite(duration) or duration <= 0:
        raise SystemExit(f"El archivo no tiene una duración válida: {path}")
    return video, audio, duration


def prepare_source():
    inputs = settings["sources"]
    emit("prepare", 0, len(inputs))
    if len(inputs) == 1 and not settings["preview"] and not inputs[0].get("start") and not inputs[0].get("length"):
        source = source_path(inputs[0]["path"])
        video, audio, duration = probe(source)
        if audio:
            sar_num, sar_den = parse_ratio(video.get("sample_aspect_ratio"))
            return str(source), duration, f"{sar_num}:{sar_den}", video["index"], audio["index"]

    print("Preparando la lista de vídeos…", flush=True)
    normalized = Path(workspace.name) / "input"
    normalized.mkdir()
    manifest = []
    for index, entry in enumerate(inputs):
        source = source_path(entry["path"])
        video, audio, total = probe(source)
        start = entry.get("start", 0)
        duration = min(total - start, entry.get("length") or total)
        if start < 0 or not math.isfinite(duration) or duration <= 0:
            raise SystemExit(f"El tramo solicitado queda fuera del vídeo: {source}")
        sar_num, sar_den = parse_ratio(video.get("sample_aspect_ratio"))
        geometry = raw_filter(width, height, video["width"], video["height"],
                              src_sar_num=sar_num, src_sar_den=sar_den, fit=settings["fit"])
        output = normalized / f"clip-{index:03}.mkv"
        command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", str(start), "-i", source]
        if not audio:
            command.extend(("-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono"))
        args = ("-map", f"0:{video['index']}",
            "-map", f"0:{audio['index']}" if audio else "1:a:0",
            "-t", str(duration), "-vf",
            f"fps={fps},format=yuv444p,{geometry},setsar={HAR_NUM}/{HAR_DEN}",
            "-af", "apad", "-c:v", "ffv1", "-pix_fmt", "yuv444p",
            "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le",
            "-fflags", "+bitexact", str(output))
        if os.environ["ASTRA_INPUT_CUDA"] == "1":
            input_index = command.index("-i")
            accelerated = [*command[:input_index], "-hwaccel", "cuda", *command[input_index:]]
            if not run(*accelerated, *args, allow_retry=True):
                run(*command, *args)
        else:
            run(*command, *args)
        manifest.append(output.name)
        emit("prepare", index + 1, len(inputs))

    source = normalized / "source.mkv"
    (normalized / "playlist.txt").write_text("".join(f"file {name}\n" for name in manifest), encoding="utf-8")
    run("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f",
        "concat", "-safe", "1", "-i", str(normalized / "playlist.txt"), "-c", "copy", "-fflags", "+bitexact", str(source))
    return str(source), probe(str(source))[2], f"{HAR_NUM}:{HAR_DEN}", 0, 1


source, duration, sar, video_track, audio_track = prepare_source()
print(f"Pistas seleccionadas: vídeo {video_track}, audio {audio_track}", flush=True)
if not math.isfinite(duration) or duration <= 0:
    raise SystemExit("El vídeo no tiene una duración válida.")
print(f"{codec}: {duration:.2f} s, {fps} FPS, {width}x{height}, perfil {encoding_profile}", flush=True)
profile = job / f"{job.name}.toml"
simulation = job / "sim"
profile.write_text(f"""schema_version = 5
[source]
path = {json.dumps(source, ensure_ascii=False)}
fps = "{fps}"
duration = "{duration:.6f}"
sar = "{sar}"
video_map = "0:{video_track}"
audio_map = "0:{audio_track}"
{lore_audio}
[video]
width = {width}
height = {height}
fit = "{settings['fit']}"
master_denoise = {'true' if encoding_profile == 'detailed' and not detail_motion else 'false'}
{lore_video}
selective_denoise = {"true" if detail_motion else "false"}
[output]
directory = {json.dumps(str(simulation), ensure_ascii=False)}
reuse = true
emit_decisions = true
[encoder]
gpu = {"false" if settings.get("acceleration") == "cpu" else "true"}
cold_cap = {200 if lore else settings['cold_cap']}
{lore_motion}
detail_motion = {"true" if detail_motion else "false"}
raw_prefetch = {'false' if fast or region == 'eu' else 'true'}
scroll = {'false' if fast else 'true'}
boot_prefetch = {'false' if fast else 'true'}
[palette]
algorithm = "{settings['palette']}"
""", encoding="utf-8")
frames = max(2, round(duration * fps))
parts, estimated_iso_bytes = estimate(duration, fps, settings["part_minutes"],
                                      settings.get("preview_mp4", False))
multi_disc = bool(settings.get("multi_disc", False)) and not settings["preview"]
limit = part_limit(fps, settings["part_minutes"], settings.get("preview_mp4", False), frames)
scene_split = bool(settings.get("scene_split")) and parts > 1
slack = SCENE_SLACK if scene_split else 1.0
if frames > MAX_PART_FRAMES and limit > MAX_PART_FRAMES:
    # Long chapters need memory in proportion to their length. Shorten them to
    # what this WSL session can hold instead of failing an hour later.
    available = next((int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("MemAvailable:")), 0)
    fits = int((available * 0.85 - MEMORY_FIXED) // MEMORY_PER_FRAME / slack)
    if available and fits < limit:
        limit = max(MAX_PART_FRAMES, fits)
        parts, estimated_iso_bytes = estimate(duration, fps, limit / (60 * fps),
                                              settings.get("preview_mp4", False))
        print(f"Memoria de WSL disponible: {available / 1024**3:.1f} GB. Los capítulos se acortan "
              f"a {limit // fps // 60}:{limit // fps % 60:02d} ({parts} capítulos) para que quepan.",
              flush=True)
# Hard ceiling per chapter: the nominal length plus the shot-change slack.
os.environ["ASTRA_PART_LIMIT_FRAMES"] = str(round(limit * slack))
if parts > (MAX_TOTAL_PARTS if multi_disc else MAX_PARTS):
    raise SystemExit(f"CAVC y LORE admiten hasta {MAX_PARTS} partes por CD. "
                     "Elige capítulos más largos.")
if not settings["preview"] and estimated_iso_bytes > CD_BYTES and not multi_disc:
    raise SystemExit(f"La estimación conservadora de {codec} supera la capacidad "
                     "de un CD de 80 minutos. Activa «Dividir en varios CD» o divide la película.")
if scene_split:
    os.environ["ASTRA_SCENE_SPLIT"] = "1"
    print("Los cortes entre capítulos se ajustarán a un cambio de escena cercano.", flush=True)
source_stamp = sim_artifact_cache.source_stamp(Path(source).resolve())
source_hash = sim_artifact_cache.source_identity(source)["sha256"]
os.environ["ASTRA_SOURCE_PROOF"] = json.dumps([source_stamp, source_hash])
os.environ["ASTRA_PRUNE_PART_PNGS"] = "1"
os.environ["ASTRA_PREVIEW_MP4"] = "1" if settings.get("preview_mp4") else "0"
# Paths passed to make must be relative to the checkout, including under DrvFS.
config = str(profile.relative_to(Path.cwd()))
if parts == 1:
    encode = (sys.executable, "-u", "tools/sim.py", config)
else:
    encode = (sys.executable, "-u", "tools/multipart.py", config,
              "--parts", str(parts), "--security-region", region,
              *(("--multi-disc",) if multi_disc else ()))
if not run(*encode, allow_retry=not fast and region != "eu"):
    print("Reintentando con prelectura de patrones desactivada…", flush=True)
    profile.write_text(profile.read_text(encoding="utf-8").replace(
        "raw_prefetch = true", "raw_prefetch = false"
    ), encoding="utf-8")
    run(*encode)
emit("encoded")
emit("build")
if parts == 1:
    run("make", "disc", f"CONFIG={config}", f"SECURITY_REGION={region}")

def disc_checks(iso):
    cue = iso.with_suffix(".cue")
    with iso.open("rb") as stream:
        header = stream.read(512)
    expected = {"jp": b"J", "us": b"U", "eu": b"E"}[region]
    if header[:14] != b"SEGADISCSYSTEM" or header[0x1F0:0x1F1] != expected:
        raise SystemExit(f"Cabecera o región incorrecta en la ISO: {iso.name}")
    if not cue.is_file() or iso.name not in cue.read_text(encoding="utf-8") or iso.stat().st_size > CD_BYTES:
        raise SystemExit(f"CUE incompleto o imagen mayor de 700 MiB: {iso.name}")
    return cue


disc_list = []
if parts == 1:
    images = list(Path("out").glob(f"{job.name}*_release.iso"))
    if len(images) != 1:
        raise SystemExit(f"No se identificó la ISO generada por {codec}.")
    iso = images[0]
    from multipart_build import verify_iso
    verify_iso(iso, job / "disc")
    from player_constants import parse_header_sector
    with (job / "disc" / "HEADER.DAT").open("rb") as stream:
        frames = parse_header_sector(stream.read(2048)).frames
    receipt = None
    disc_list.append({"iso": iso, "parts": 1, "frames": frames})
else:
    receipts = [json.loads(path.read_text(encoding="utf-8"))
                for path in Path("out").glob(f"{job.name}*/multipart*_release.json")]
    receipts = [item for item in receipts if (Path("out") / Path(item["iso"]).name).is_file()]
    split = sorted((item for item in receipts if item.get("disc")), key=lambda item: item["disc"][0])
    if split:
        count = split[0]["disc"][1]
        if [item["disc"] for item in split] != [[i, count] for i in range(1, count + 1)]:
            raise SystemExit(f"Faltan discos de {codec}: se esperaban {count}.")
        receipts = split
    else:
        receipts = [item for item in receipts if not item.get("disc")]
        if len(receipts) != 1:
            raise SystemExit(f"No se identificó la ISO generada por {codec}.")
    for item in receipts:
        # Receipts hold absolute WSL paths; Windows joins result paths to cavc/.
        disc_list.append({"iso": Path("out") / Path(item["iso"]).name, "parts": len(item["items"]),
                          "frames": sum(part["frames"] for part in item["items"])})
    receipt = {"items": [part for item in receipts for part in item["items"]]}
    frames = sum(disc["frames"] for disc in disc_list)
    iso = disc_list[0]["iso"]
for disc in disc_list:
    disc["cue"] = disc_checks(disc["iso"])
cue = disc_list[0]["cue"]
def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

emit("disc")
preview = None
if settings.get("preview_mp4"):
    emit("preview")
    print("Renderizando la vista previa del flujo codificado…", flush=True)
    preview = job / f"Vista_previa_{codec}.mp4"
    if parts == 1:
        simulation = load_profile(profile).output_dir
        run(sys.executable, "-u", "tools/astra_preview.py", str(simulation), str(fps), str(preview))
    else:
        part_previews = [
            Path.cwd() / load_profile(Path(item["profile"])).artifact_dir / "preview.mp4"
            for item in receipt["items"]
        ]
        if all(p.is_file() for p in part_previews):
            concat_list = job / "preview_concat.txt"
            concat_list.write_text(
                "".join(f"file '{p.resolve().as_posix()}'\n" for p in part_previews),
                encoding="utf-8",
            )
            run("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", "-movflags", "+faststart", str(preview))
        else:
            preview = None
sim_artifact_cache.source_identity(source)
emit("ready")
(job / "result.json").write_text(json.dumps({
    "iso": str(iso), "cue": str(cue), "parts": parts, "frames": frames,
    "discs": [{"iso": str(disc["iso"]), "cue": str(disc["cue"]), "parts": disc["parts"],
               "frames": disc["frames"], "iso_sha256": digest(disc["iso"]),
               "cue_sha256": digest(disc["cue"])} for disc in disc_list],
    "region": region, "codec": codec, "profile": encoding_profile, "preview": str(preview.relative_to(Path.cwd())) if preview else None,
    "iso_sha256": digest(iso), "cue_sha256": digest(cue),
    "checks": ["packed_stream", "disc_image"],
    "fps_effective": av_config.playback_fps_for_content(fps),
}), encoding="utf-8")

