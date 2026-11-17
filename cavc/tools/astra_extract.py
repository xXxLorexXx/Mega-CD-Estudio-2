"""Lossless input preparation shared by the single and multipart encoders."""
from fractions import Fraction
import fcntl
import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess
import wave

from source_range import sample_range, video_filter
from video_geometry import endpoint_snap_filter, parse_ratio, probe_source, raw_filter, source_filter


class SharedInputSpaceError(OSError):
    pass


def run_ffmpeg(args, disk_guard=None):
    if disk_guard is None:
        subprocess.run(args, check=True, stdin=subprocess.DEVNULL)
        return
    with subprocess.Popen(args, stdin=subprocess.DEVNULL) as process:
        try:
            while True:
                disk_guard()
                try:
                    status = process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    continue
                disk_guard()
                if status:
                    raise subprocess.CalledProcessError(status, args)
                return
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise


def filters(env):
    source = env["CBRSIM_SRC"]
    video_map = env.get("CBRSIM_VIDEO_MAP", "0:V:0")
    selector = video_map.split(":", 1)[1]
    if env.get("CBRSIM_MASTER_VF") and env.get("CBRSIM_RAW_VF"):
        sw, sh, sn, sd = 0, 0, 1, 1
    else:
        sw, sh, sn, sd = probe_source(source, selector)
    if env.get("CBRSIM_SOURCE_SAR"):
        sn, sd = parse_ratio(env["CBRSIM_SOURCE_SAR"])
    geometry = dict(src_sar_num=sn, src_sar_den=sd,
                    fit=env.get("CBRSIM_GEOMETRY_FIT", "pad"),
                    resize_filter=env.get("CBRSIM_RESIZE_FILTER", "lanczos"))
    width, height = int(env["CBRSIM_W"]), int(env["CBRSIM_H"])
    master = env.get("CBRSIM_MASTER_VF") or source_filter(
        width, height, sw, sh, denoise=env.get("CBRSIM_MASTER_DENOISE", "1") != "0", **geometry)
    raw = env.get("CBRSIM_RAW_VF") or raw_filter(width, height, sw, sh, **geometry)
    if env.get("CBRSIM_SELECTIVE_DENOISE") == "1":
        master += ",hqdn3d=0.7:1.2:1.4:2.2"
    prefix = endpoint_snap_filter(
        int(env.get("CBRSIM_PREPROCESS_ENDPOINT_SNAP_BLACK_MAX", -1)),
        int(env.get("CBRSIM_PREPROCESS_ENDPOINT_SNAP_WHITE_MIN", 256)))
    return (f"{prefix},{master}", f"{prefix},{raw}") if prefix else (master, raw)


def video(command, source, mapping, source_filter, destination, *, hw, frames=0, disk_guard=None):
    def run(input_options):
        args = [*command, *input_options, "-i", source, "-map", mapping,
                "-vf", source_filter, "-an", "-pix_fmt", "rgb24",
                "-compression_level", "1"]
        if frames:
            args.extend(("-frames:v", str(frames)))
        run_ffmpeg([*args, str(destination / "%05d.png")], disk_guard)

    if hw:
        try:
            run(["-hwaccel", "cuda"])
            return
        except subprocess.CalledProcessError:
            print("Decodificación CUDA no disponible para esta entrada. Continuando por CPU.", flush=True)
            for path in destination.glob("*.png"):
                path.unlink()
    run([])


def video_dual(command, source, mapping, master_filter, master_dest, raw_filter_chain, raw_dest, *, hw, frames=0, disk_guard=None):
    frame_opts = ("-frames:v", str(frames)) if frames else ()

    def run(input_options):
        args = [
            *command, *input_options, "-i", source,
            "-map", mapping, "-vf", master_filter, "-an", "-pix_fmt", "rgb24",
            "-compression_level", "1", *frame_opts, str(master_dest / "%05d.png"),
            "-map", mapping, "-vf", raw_filter_chain, "-an", "-pix_fmt", "rgb24",
            "-compression_level", "1", *frame_opts, str(raw_dest / "%05d.png"),
        ]
        run_ffmpeg(args, disk_guard)

    def clean_both():
        for dest in (master_dest, raw_dest):
            for path in dest.glob("*.png"):
                path.unlink()

    if hw:
        try:
            run(["-hwaccel", "cuda"])
            return True
        except subprocess.CalledProcessError:
            print("Decodificación CUDA no disponible para esta entrada. Continuando por CPU.", flush=True)
            clean_both()
    try:
        run([])
        return True
    except subprocess.CalledProcessError:
        clean_both()
        return False


# Frames decoded before a part's first frame when the extractor seeks into the
# prepared source and the chain has a temporal filter (hqdn3d keeps a running
# state). 40 s of history reproduced the decode-from-zero pixels bit-exactly in
# tests; spatial-only chains need no margin at all.
SEEK_PREROLL_FRAMES = 1000
TEMPORAL_FILTERS = ("hqdn3d", "atadenoise", "tmix", "deflicker", "fspp", "owdenoise")


def seek_window(bounds, fps, preroll=SEEK_PREROLL_FRAMES):
    """Input seek (s), read length (s) and the part's absolute output ticks.

    The decoder starts `preroll` output frames early (plus one second for
    sources whose frame rate differs from the output), timestamps stay on the
    source timeline (-copyts -start_at_zero), so the fps filter picks the same
    source frame for every output tick as a decode from zero. The part is then
    cut by absolute tick (trim start_pts/end_pts in the fps filter's 1/fps
    time base), never by a count relative to the seek.
    """
    clock = Fraction(str(fps))
    first = max(0, bounds[0] - preroll)
    seek = max(Fraction(0), Fraction(first) / clock - 1)
    length = Fraction(bounds[1]) / clock - seek + 2
    return float(seek), float(length), bounds


def first_tick(command, source, mapping, fps):
    """Absolute output tick of the first frame a decode from zero produces.

    The fps filter anchors its grid on the first video timestamp, which is not
    zero when audio starts earlier (common in MKV/MP4). Part frame indices in a
    decode from zero count from that tick; the seek path must add it.
    """
    output = subprocess.run(
        [*command[:command.index("-loglevel")], "-loglevel", "info",
         *command[command.index("-loglevel") + 2:], "-i", source, "-map", mapping,
         "-vf", f"fps={fps},showinfo", "-frames:v", "1", "-an", "-f", "null", "-"],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, errors="replace").stderr
    for line in output.splitlines():
        if "Parsed_showinfo" in line and " pts:" in line:
            return int(line.split(" pts:", 1)[1].split()[0])
    raise RuntimeError("cannot locate the first output frame of the source")


def extract(folder, env, master, raw, *, workers, bounds=None, audio_name, shared=False, disk_guard=None):
    folder = Path(folder)
    source, fps = env["CBRSIM_SRC"], env["CBRSIM_FPS"]
    audio_rate = int(env["CBRSIM_AUDIO_RATE"])
    base = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
            "-threads", str(workers), "-filter_threads", str(workers),
            "-ss", "0", "-t", env["CBRSIM_DURATION"]]
    mapping = env.get("CBRSIM_VIDEO_MAP", "0:V:0")
    frames = bounds[1] - bounds[0] if bounds else 0
    # Audio keeps the whole-source decode below (loudness filters must see the
    # same input for every part); only the video seeks.
    audio_base = base
    chain_for = lambda vf: video_filter(vf, fps, bounds)
    if (bounds and not shared and bounds[0] > 0
            and os.environ.get("ASTRA_SEEK_EXTRACT") == "1"):
        temporal = any(name in chain for chain in (master, raw) for name in TEMPORAL_FILTERS)
        seek, length, _ = seek_window(bounds, fps, SEEK_PREROLL_FRAMES if temporal else 0)
        if seek > 0:
            tick = first_tick(base, source, mapping, fps)
            base = [*base[:-4], "-ss", f"{seek:.6f}", "-t", f"{length:.6f}",
                    "-copyts", "-start_at_zero"]
            chain_for = lambda vf: (f"{vf},fps={fps},trim=start_pts={bounds[0] + tick}:"
                                    f"end_pts={bounds[1] + tick},setpts=PTS-STARTPTS")
            print(f"Extracción por tramo: salto a {seek:.2f} s "
                  f"({'con' if temporal else 'sin'} margen para filtros temporales).", flush=True)
    hw_enabled = os.environ.get("ASTRA_INPUT_CUDA") == "1"
    master_dest = folder / "master"
    raw_dest = folder / "raw"
    master_dest.mkdir(exist_ok=True)
    raw_dest.mkdir(exist_ok=True)
    if master != raw:
        print("Extrayendo imagen master y raw en una pasada…", flush=True)
        master_chain = chain_for(master)
        raw_chain = chain_for(raw)
        if shared:
            master_chain += ",setpts=PTS-STARTPTS"
            raw_chain += ",setpts=PTS-STARTPTS"
        if not video_dual(base, source, mapping, master_chain, master_dest,
                          raw_chain, raw_dest, hw=hw_enabled, frames=frames, disk_guard=disk_guard):
            video(base, source, mapping, master_chain, master_dest, hw=hw_enabled,
                  frames=frames, disk_guard=disk_guard)
            video(base, source, mapping, raw_chain, raw_dest, hw=hw_enabled,
                  frames=frames, disk_guard=disk_guard)
    else:
        for label, vf in (("master", master), ("raw", raw)):
            destination = folder / label
            if label == "raw":
                for path in master_dest.glob("*.png"):
                    os.link(path, destination / path.name)
                continue
            print(f"Extrayendo imagen {label}…", flush=True)
            chain = chain_for(vf)
            if shared:
                # Match exact frame-range extraction. A PNG output with nonzero
                # starting PTS would otherwise duplicate the first source frame.
                chain += ",setpts=PTS-STARTPTS"
            video(base, source, mapping, chain, destination,
                  hw=hw_enabled, frames=frames, disk_guard=disk_guard)
    audio_filter = env.get("CBRSIM_AUDIO_AF", "")
    audio_map = env.get("CBRSIM_AUDIO_MAP")
    if bounds:
        start, end = sample_range(bounds, fps, audio_rate)
    command = [*audio_base, "-i", source, "-vn"]
    if audio_map:
        command.extend(("-map", audio_map))
    if audio_filter:
        command.extend(("-af", audio_filter))
    tail = ["-ac", "1", "-ar", str(audio_rate), "-c:a", "pcm_s16le"]
    cached = None
    if bounds and not shared and os.environ.get("ASTRA_SEEK_EXTRACT") == "1":
        cached = shared_audio(command + tail, source)
    if cached is not None:
        # Every part slices the same whole-source PCM it would have decoded.
        copy_audio(cached, folder / audio_name, start, end)
        return
    run_ffmpeg([*command, *tail, str(folder / audio_name)], disk_guard)
    if bounds:
        temporary = folder / "audio-range.wav"
        copy_audio(folder / audio_name, temporary, start, end)
        temporary.replace(folder / audio_name)


def shared_audio(command, source):
    """Decode and filter the whole-source audio once per job workspace.

    Parts of one disc run as separate processes; the first one to arrive writes
    the PCM under a lock, the others reuse it. Returns None when no job
    workspace is available, so the caller decodes as before.
    """
    root = os.environ.get("CAVC_WORKSPACE_ROOT")
    if not root:
        return None
    try:
        stat = Path(source).stat()
    except OSError:
        return None
    key = hashlib.sha256(json.dumps(
        [command, str(Path(source).resolve()), stat.st_size, stat.st_mtime_ns],
        ensure_ascii=True).encode()).hexdigest()[:24]
    folder = Path(root) / "audio-cache"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}.wav"
    with (folder / f"{key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not target.is_file():
            pending = folder / f"{key}.{os.getpid()}.wav"
            run_ffmpeg([*command, str(pending)])
            os.replace(pending, target)
    return target


def copy_audio(source, destination, start, end):
    with wave.open(str(source), "rb") as original:
        original.setpos(min(start, original.getnframes()))
        stride = original.getnchannels() * original.getsampwidth()
        with wave.open(str(destination), "wb") as output:
            output.setparams(original.getparams())
            remaining = end - start
            while remaining:
                count = min(remaining, 65536)
                output.writeframesraw(original.readframes(count).ljust(count * stride, b"\0"))
                remaining -= count


def materialize(prepared, folder, bounds, fps, rate, audio_name):
    start, end = bounds
    for label in ("master", "raw"):
        destination = folder / label
        destination.mkdir(exist_ok=True)
        for index in range(start, end):
            source = prepared / label / f"{index + 1:05d}.png"
            target = destination / f"{index - start + 1:05d}.png"
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
    copy_audio(prepared / audio_name, folder / audio_name, *sample_range(bounds, fps, rate))
