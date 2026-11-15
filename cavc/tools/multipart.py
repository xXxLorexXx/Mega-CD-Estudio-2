#!/usr/bin/env python3
"""Encode independent parts of one profile and build a menu-free looping disc."""
from __future__ import annotations

import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

import astra_extract
from astra_progress import emit
import analysis_logs
import cavc_routing
import encode_config
import resource_tokens
import sim_artifact_cache
import tmpfs_workspace
from source_range import frame_range

ROOT = Path(__file__).resolve().parents[1]
MAX_PARTS = encode_config.MAX_MULTIPART_PARTS
PACKED_FILES = ("HEADER.DAT", "BODY.DAT", "MOVIE.DAT", "paltab.bin", "palidx.bin")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def token(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def split_frames(frames, parts, offset=0):
    if type(parts) is not int or not 1 <= parts <= MAX_PARTS:
        raise ValueError(f"parts must be within 1..{MAX_PARTS}")
    if frames < parts * 2:
        raise ValueError("each part needs at least two output frames")
    count, extra = divmod(frames, parts)
    if count + bool(extra) > cavc_routing.MAX_FRAMES:
        raise ValueError(f"each part must fit {cavc_routing.MAX_FRAMES} frames; increase --parts")
    result = []
    start = offset
    for index in range(parts):
        end = start + count + (index < extra)
        result.append((start, end))
        start = end
    return result


def toml_text(data):
    lines = []
    def table(values, prefix=()):
        if prefix:
            lines.extend(("", "[" + ".".join(json.dumps(x) for x in prefix) + "]"))
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(json.dumps(key) + " = " + json.dumps(
                    value, ensure_ascii=False, allow_nan=False))
        for key, value in values.items():
            if isinstance(value, dict):
                table(value, (*prefix, key))
    table(data)
    return "\n".join(lines) + "\n"


@contextlib.contextmanager
def stem_lock(stem):
    with resource_tokens.acquire_stem(stem):
        key = resource_tokens.HELD_STEMS_ENV
        previous = os.environ.get(key)
        os.environ.update(resource_tokens.held_stem_environment(stem))
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def run_logged(command, name, *, env=None, handoff=None):
    """Retain commands, PID, log, exit status and optional SIM lease handoff."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = ROOT / "logs" / f"multipart-{name}-{run_id}.log"
    receipt = {"command": [str(x) for x in command], "log": str(log),
               "started_at": run_id, "pid": None, "exit_code": None}
    environment = dict(os.environ if env is None else env)
    lease = None
    if handoff is not None:
        handoff.mkdir(parents=True)
        environment["SEGACD_TMPFS_HANDOFF"] = str(handoff)
    started = time.perf_counter()
    try:
        with log.open("w", encoding="utf-8") as output:
            output.write("$ " + " ".join(receipt["command"]) + "\n")
            output.flush()
            process = subprocess.Popen(receipt["command"], cwd=ROOT, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT)
            receipt["pid"] = process.pid
            write_json(log.with_suffix(".json"), receipt)
            print(f"multipart: {name}: pid={process.pid} log={log}", flush=True)
            try:
                while process.poll() is None:
                    if handoff is not None and lease is None:
                        ready = handoff / "ready.json"
                        if ready.exists():
                            path = Path(json.loads(ready.read_text())["path"])
                            lease = tmpfs_workspace.lease_managed_path(path)
                            if lease is None:
                                raise RuntimeError(f"cannot pin completed SIM: {path}")
                            (handoff / "ack").write_text("pinned\n")
                    time.sleep(0.1)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            finally:
                receipt.update(exit_code=process.poll(), seconds=time.perf_counter()-started)
                write_json(log.with_suffix(".json"), receipt)
        if process.returncode:
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 8192))
                tail = "\n".join(handle.read().decode("utf-8", "replace").splitlines()[-6:])
            raise RuntimeError(f"{name} failed ({process.returncode}); inspect {log}\n{tail}")
        print(f"multipart: {name}: complete in {receipt['seconds']:.1f}s", flush=True)
        return receipt, lease
    except BaseException:
        if lease is not None:
            lease.release()
        raise


def input_identity(profile, parts):
    environment = dict(os.environ)
    encode_config.apply_profile_env(profile, environment)
    return {"schema": 1, "parts": parts, "encoder": analysis_logs.encoder_version(),
            "source": sim_artifact_cache.source_identity(profile.data["source"]["path"]),
            "settings": sim_artifact_cache.effective_environment(environment)}


def count_frames(profile, name):
    source = profile.section("source")
    with resource_tokens.acquire_tokens("cpu", count=1):
        record, _ = run_logged([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "0",
            "-filter_threads", "2", "-t", str(source["duration"]), "-i", source["path"],
            "-map", source.get("video_map", "0:V:0"), "-vf", f"fps={source['fps']}", "-an",
            "-progress", "pipe:1", "-nostats", "-f", "null", "-",
        ], name + "-count")
    counts = [int(line.split("=", 1)[1]) for line in Path(record["log"]).read_text().splitlines()
              if line.startswith("frame=")]
    if not counts or counts[-1] < 2:
        raise ValueError("source has fewer than two output frames")
    return counts[-1]


AUDIO_NAME = "audio_22k05_s16_mono.wav"


def prepare_shared(profile, fallback_frames=None):
    """Prepare one indexed, lossless raster and filtered audio on the Linux filesystem."""
    if os.environ.get("ASTRA_DESKTOP_PARTS") != "1":
        return None, fallback_frames
    if os.environ.get("ASTRA_DISABLE_SHARED_INPUT") == "1":
        return None, fallback_frames
    env = dict(os.environ)
    encode_config.apply_profile_env(profile, env)
    master, raw = astra_extract.filters(env)
    folder = Path(env["CAVC_WORKSPACE_ROOT"]) / "source-frames"
    copies = 1 if master == raw else 2
    dimensions = int(env["CBRSIM_W"]) * int(env["CBRSIM_H"])
    frames = round(float(env["CBRSIM_DURATION"]) * float(env["CBRSIM_FPS"])) + 2
    estimate = frames * copies * (dimensions * 3 + 8192) + 4 * 1024**3
    if estimate > min(shutil.disk_usage(folder.parent).free,
                      int(env["ASTRA_HOST_FREE"])):
        print("Poco espacio para una extracción compartida. Se leerá por tramos.", flush=True)
        return None, fallback_frames
    if folder.is_dir():
        # A retry with the same source and filters may use this job's completed raster.
        shutil.rmtree(folder)
    folder.mkdir()
    print("Extracción compartida de vídeo y audio en Linux…", flush=True)
    emit("prepare")
    workers = resource_tokens.requested_cpu_workers()
    with resource_tokens.acquire_tokens("cpu", count=workers):
        astra_extract.extract(folder, env, master, raw, workers=workers,
                              audio_name=AUDIO_NAME, shared=True)
    return folder, sum(1 for _ in (folder / "master").glob("*.png"))


def plan_stem(profile, parts, identity):
    return f"{profile.artifact_stem}-multipart{parts}-{token(identity)[:12]}"


def prepare(profile, parts, total=None, identity=None):
    identity = identity if identity is not None else input_identity(profile, parts)
    stem = plan_stem(profile, parts, identity)
    work = ROOT / "tmp" / stem
    plan_file = work / "plan.json"
    if plan_file.is_file():
        plan = json.loads(plan_file.read_text())
        if plan.get("identity") != identity:
            raise ValueError("multipart plan identity differs")
    else:
        total = total if total is not None else count_frames(profile, stem)
        bounds = frame_range(profile.section("source")) or (0, total)
        if bounds[1] > total:
            raise ValueError("profile source range exceeds decoded source frames")
        plan = {"identity": identity, "stem": stem, "profile": str(profile.path),
                "source_frames": total, "ranges": split_frames(bounds[1]-bounds[0], parts, bounds[0])}
        write_json(plan_file, plan)
    bounds = frame_range(profile.section("source")) or (0, plan["source_frames"])
    expected = [list(pair) for pair in split_frames(bounds[1]-bounds[0], parts, bounds[0])]
    if [list(pair) for pair in plan["ranges"]] != expected or plan["stem"] != stem:
        raise ValueError("multipart plan ranges or output stem differ")
    directory = work / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    profiles = []
    for number, (start, end) in enumerate(plan["ranges"], 1):
        data = copy.deepcopy(profile.data)
        # The count is already represented by the plan and frame ranges. Keep
        # per-part profiles identical whether the count came from TOML or CLI.
        data.pop("multipart", None)
        data["source"].update(start_frame=start, end_frame=end)
        data["output"].update(emit_decisions=True, reuse=False)
        path = directory / f"{stem}-part{number:03}.toml"
        text = toml_text({"schema_version": encode_config.SCHEMA_VERSION, **data})
        if not path.exists() or path.read_text() != text:
            path.write_text(text, encoding="utf-8")
        profiles.append(encode_config.load_profile(path))
    return plan, profiles


def part_identity(plan, number, profile):
    return {"multipart": token(plan["identity"]), "part": number,
            "range": list(frame_range(profile.section("source")))}


def completed_part(plan, number, profile):
    marker = ROOT / profile.artifact_dir / "multipart-complete.json"
    if not marker.exists():
        return None
    if os.environ.get("ASTRA_PREVIEW_MP4") == "1" and not (marker.parent / "preview.mp4").is_file():
        return None
    record = json.loads(marker.read_text())
    if record.get("identity") != part_identity(plan, number, profile):
        raise ValueError(f"part {number}: completion identity differs")
    for name in PACKED_FILES:
        path = marker.parent / name
        if not path.is_file() or digest(path) != record["files"][name]:
            raise ValueError(f"part {number}: completed file changed or missing: {path}")
    return record


def encode_part(plan, number, profile, *, env=None):
    with resource_tokens.acquire_stem(profile.sim_stem):
        environment = {**(os.environ if env is None else env),
                       **resource_tokens.held_stem_environment(profile.sim_stem)}
        environment["ASTRA_PROGRESS_PART"] = str(number)
        handoff = ROOT / "tmp" / plan["stem"] / ("handoff-" + uuid.uuid4().hex)
        sim, lease = run_logged([sys.executable, ROOT / "tools/sim.py", profile.path],
                                f"{plan['stem']}-part{number:03}-encode", env=environment, handoff=handoff)
        if lease is None:
            raise RuntimeError("SIM exited without its completion lease")
        try:
            emit("pack_part", part=number, parts=plan["identity"]["parts"])
            pack, _ = run_logged(["make", "moviepack", f"CONFIG={profile.path.relative_to(ROOT)}"],
                                  f"{plan['stem']}-part{number:03}-pack", env=environment)
            import player_constants
            folder = ROOT / profile.artifact_dir
            with (folder / "HEADER.DAT").open("rb") as stream:
                constants = player_constants.parse_header_sector(stream.read(2048))
            start, end = frame_range(profile.section("source"))
            if constants.frames != end-start:
                raise ValueError(f"part {number}: packed frame count differs")
            if environment.get("ASTRA_PREVIEW_MP4") == "1":
                sim_dir = (lease.entry / "data") if (lease.entry / "data" / "preview").is_dir() else (ROOT / profile.output_dir)
                part_fps = profile.section("source").get("fps", 25)
                run_logged([sys.executable, ROOT / "tools/astra_preview.py", sim_dir, str(part_fps), folder / "preview.mp4"],
                           f"{plan['stem']}-part{number:03}-preview", env=environment)
            record = {"identity": part_identity(plan, number, profile), "frames": constants.frames,
                      "profile": str(profile.path), "sim": sim, "pack": pack,
                      "files": {name: digest(folder/name) for name in PACKED_FILES}}
            write_json(folder / "multipart-complete.json", record)
            emit("encoded", part=number, parts=plan["identity"]["parts"])
            if os.environ.get("ASTRA_PRUNE_PART_PNGS") == "1":
                for candidate_dir in (ROOT / profile.output_dir, lease.entry / "data"):
                    if not candidate_dir.is_dir():
                        continue
                    for sub in ("master", "frames", "raw", "preview"):
                        target = candidate_dir / sub
                        if target.is_dir():
                            shutil.rmtree(target, ignore_errors=True)
            return record
        finally:
            lease.release()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Set [multipart] parts = N in the profile or pass --parts N. "
               "Completed parts are reused; the ISO is built when every part is complete. "
               "See README.md: Multipart encoding (CLI).")
    parser.add_argument("profile", type=Path)
    parser.add_argument("--parts", type=int, metavar="N",
                        help=f"Number of parts (1..{MAX_PARTS}); overrides [multipart].parts")
    parser.add_argument("--part", type=int, metavar="K",
                        help="Encode only part K (1-based); omission completes all parts")
    parser.add_argument("--build-only", action="store_true", help="Build from already completed parts")
    parser.add_argument("--debug", action="store_true", help="Include the playback DEBUG HUD")
    parser.add_argument("--security-region", choices=("jp", "us", "eu"), default="jp")
    args = parser.parse_args(argv)
    if args.parts is not None and not 1 <= args.parts <= MAX_PARTS:
        parser.error(f"--parts must be within 1..{MAX_PARTS}")
    if args.part is not None and args.part < 1:
        parser.error("--part must be within 1..--parts (1-based)")
    if args.part is not None and args.build_only:
        parser.error("--part cannot be combined with --build-only")
    try:
        profile = encode_config.load_profile(args.profile)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid encode profile: {exc}")
    if args.parts is None:
        args.parts = profile.section("multipart").get("parts")
    if args.parts is None:
        parser.error("set --parts N or [multipart].parts in the profile")
    if args.part is not None and args.part > args.parts:
        parser.error(f"--part must be within 1..{args.parts} (1-based)")
    return args, profile


def main():
    def _handle_term(_signum, _frame):
        raise SystemExit(130)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_term)
    args, profile = parse_args()
    os.chdir(ROOT)
    (ROOT / "logs").mkdir(exist_ok=True)
    with stem_lock(f"multipart-{profile.artifact_stem}-{args.parts}"):
        selected = {args.part} if args.part is not None else set(range(1, args.parts + 1))
        identity = input_identity(profile, args.parts)
        existing = (ROOT / "tmp" / plan_stem(profile, args.parts, identity) / "plan.json").is_file()
        shared, decoded = (None, None) if existing or args.build_only else prepare_shared(profile)
        plan, profiles = prepare(profile, args.parts, decoded, identity)
        environment = {**os.environ, "ASTRA_PROGRESS_PARTS": str(len(profiles))}
        if shared:
            environment["ASTRA_PREPARED_INPUT"] = str(shared)
        records = [None] * len(profiles)
        pending = []
        for number, part in enumerate(profiles, 1):
            try:
                records[number - 1] = completed_part(plan, number, part)
            except (ValueError, OSError, KeyError) as exc:
                print(f"Parte {number}: se regenerará el punto de guardado inválido ({exc})", flush=True)
            if records[number - 1]:
                print(f"multipart: part {number}/{args.parts}: verified completed part reused", flush=True)
                emit("encoded", part=number, parts=len(profiles))
            elif number in selected and not args.build_only:
                pending.append((number, part))
        if pending and existing:
            shared, decoded = prepare_shared(profile)
            if shared:
                if decoded != plan["source_frames"]:
                    raise ValueError("decoded frame count differs from the resumed plan")
                environment["ASTRA_PREPARED_INPUT"] = str(shared)
        workers = min(len(pending), int(os.environ.get("ASTRA_PART_WORKERS", "1")), 2)
        if workers > 1:
            memory = next((int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                           if line.startswith("MemAvailable:")), 0)
            if memory < 12 * 1024**2 or (os.cpu_count() or 1) < 8:
                workers = 1
                print("Recursos para una parte a la vez. Se continúa secuencialmente.", flush=True)
            else:
                environment["CBRSIM_WORKERS"] = str(max(2, resource_tokens.requested_cpu_workers() // 2))
        def convert(number, part):
            print(f"multipart: part {number}/{args.parts}: frames {plan['ranges'][number - 1]}", flush=True)
            return encode_part(plan, number, part, env=environment)
        if workers > 1:
            print("Codificando hasta dos partes a la vez con turnos para la GPU.", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(convert, number, part): number for number, part in pending}
                for future in as_completed(futures):
                    records[futures[future] - 1] = future.result()
        else:
            for number, part in pending:
                records[number - 1] = convert(number, part)
        missing = [i+1 for i, record in enumerate(records) if record is None]
        if missing:
            print(f"multipart: ISO deferred; unfinished parts: {', '.join(map(str, missing))}", flush=True)
            if args.build_only:
                return 2
            return 0
        os.environ.pop("ASTRA_PROGRESS_PART", None)
        emit("build")
        import multipart_build
        multipart_build.build(plan, profiles, records, debug=args.debug, region=args.security_region)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
