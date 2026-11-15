"""Lossless input preparation shared by the single and multipart encoders."""
from pathlib import Path
import os
import shutil
import subprocess
import wave

from source_range import sample_range, video_filter
from video_geometry import endpoint_snap_filter, parse_ratio, probe_source, raw_filter, source_filter


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


def video(command, source, mapping, source_filter, destination, *, hw, frames=0):
    def run(input_options):
        args = [*command, *input_options, "-i", source, "-map", mapping,
                "-vf", source_filter, "-an", "-pix_fmt", "rgb24",
                "-compression_level", "1"]
        if frames:
            args.extend(("-frames:v", str(frames)))
        subprocess.run([*args, str(destination / "%05d.png")], check=True, stdin=subprocess.DEVNULL)

    if hw:
        try:
            run(["-hwaccel", "cuda"])
            return
        except subprocess.CalledProcessError:
            print("Decodificación CUDA no disponible para esta entrada. Continuando por CPU.", flush=True)
            for path in destination.glob("*.png"):
                path.unlink()
    run([])


def video_dual(command, source, mapping, master_filter, master_dest, raw_filter_chain, raw_dest, *, hw, frames=0):
    frame_opts = ("-frames:v", str(frames)) if frames else ()

    def run(input_options):
        args = [
            *command, *input_options, "-i", source,
            "-map", mapping, "-vf", master_filter, "-an", "-pix_fmt", "rgb24",
            "-compression_level", "1", *frame_opts, str(master_dest / "%05d.png"),
            "-map", mapping, "-vf", raw_filter_chain, "-an", "-pix_fmt", "rgb24",
            "-compression_level", "1", *frame_opts, str(raw_dest / "%05d.png"),
        ]
        subprocess.run(args, check=True, stdin=subprocess.DEVNULL)

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


def extract(folder, env, master, raw, *, workers, bounds=None, audio_name, shared=False):
    folder = Path(folder)
    source, fps = env["CBRSIM_SRC"], env["CBRSIM_FPS"]
    audio_rate = int(env["CBRSIM_AUDIO_RATE"])
    base = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
            "-threads", str(workers), "-filter_threads", str(workers),
            "-ss", "0", "-t", env["CBRSIM_DURATION"]]
    mapping = env.get("CBRSIM_VIDEO_MAP", "0:V:0")
    frames = bounds[1] - bounds[0] if bounds else 0
    hw_enabled = os.environ.get("ASTRA_INPUT_CUDA") == "1"
    master_dest = folder / "master"
    raw_dest = folder / "raw"
    master_dest.mkdir(exist_ok=True)
    raw_dest.mkdir(exist_ok=True)
    if master != raw:
        print("Extrayendo imagen master y raw en una pasada…", flush=True)
        master_chain = video_filter(master, fps, bounds)
        raw_chain = video_filter(raw, fps, bounds)
        if shared:
            master_chain += ",setpts=PTS-STARTPTS"
            raw_chain += ",setpts=PTS-STARTPTS"
        if not video_dual(base, source, mapping, master_chain, master_dest,
                          raw_chain, raw_dest, hw=hw_enabled, frames=frames):
            video(base, source, mapping, master_chain, master_dest, hw=hw_enabled, frames=frames)
            video(base, source, mapping, raw_chain, raw_dest, hw=hw_enabled, frames=frames)
    else:
        for label, vf in (("master", master), ("raw", raw)):
            destination = folder / label
            if label == "raw":
                for path in master_dest.glob("*.png"):
                    os.link(path, destination / path.name)
                continue
            print(f"Extrayendo imagen {label}…", flush=True)
            chain = video_filter(vf, fps, bounds)
            if shared:
                # Match exact frame-range extraction. A PNG output with nonzero
                # starting PTS would otherwise duplicate the first source frame.
                chain += ",setpts=PTS-STARTPTS"
            video(base, source, mapping, chain, destination,
                  hw=hw_enabled, frames=frames)
    audio_filter = env.get("CBRSIM_AUDIO_AF", "")
    audio_map = env.get("CBRSIM_AUDIO_MAP")
    if bounds:
        start, end = sample_range(bounds, fps, audio_rate)
    command = [*base, "-i", source, "-vn"]
    if audio_map:
        command.extend(("-map", audio_map))
    if audio_filter:
        command.extend(("-af", audio_filter))
    subprocess.run([*command, "-ac", "1", "-ar", str(audio_rate), "-c:a", "pcm_s16le",
                    str(folder / audio_name)], check=True)
    if bounds:
        temporary = folder / "audio-range.wav"
        copy_audio(folder / audio_name, temporary, start, end)
        temporary.replace(folder / audio_name)


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
