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
from fractions import Fraction

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
MAX_PARTS = encode_config.MAX_MULTIPART_PARTS      # parts on one physical disc
MAX_DISCS = 8
MAX_TOTAL_PARTS = MAX_PARTS * MAX_DISCS            # parts of one film over several discs
CD_BYTES = 360_000 * 2048
DISC_FIXED_BYTES = 3 * 1024**2                     # boot, loader, ISO records and reserve
PART_FIXED_BYTES = 0x5000 + 0x10000 + 64 * 1024    # Main/Sub players and per-file padding
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
    if type(parts) is not int or not 1 <= parts <= MAX_TOTAL_PARTS:
        raise ValueError(f"parts must be within 1..{MAX_TOTAL_PARTS}")
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


def scene_cuts(profile, name, total):
    """Frame indices where a new shot starts, strongest first per region.

    One pass over the prepared source: blackdetect marks fades to black and
    the scene score marks hard cuts. Returns {frame: strength} with black
    gaps weighted above ordinary cuts.
    """
    source = profile.section("source")
    fps = Fraction(str(source["fps"]))
    work = ROOT / "tmp" / name
    work.mkdir(parents=True, exist_ok=True)
    metadata = work / "scene-cuts.txt"
    threshold = os.environ.get("ASTRA_SCENE_THRESHOLD", "0.30")
    with resource_tokens.acquire_tokens("cpu", count=1):
        record, _ = run_logged([
            "ffmpeg", "-hide_banner", "-nostats", "-threads", "0",
            "-t", str(source["duration"]), "-i", source["path"],
            "-map", source.get("video_map", "0:V:0"), "-an",
            "-vf", (f"fps={source['fps']},blackdetect=d=0.12:pix_th=0.10,"
                    f"select='gt(scene,{threshold})',"
                    f"metadata=print:file='{metadata.as_posix()}'"),
            "-f", "null", "-",
        ], name + "-scenes")
    cuts = {}
    current = None
    for line in metadata.read_text(errors="replace").splitlines() if metadata.exists() else ():
        if line.startswith("frame:") and "pts_time:" in line:
            current = round(Fraction(line.split("pts_time:")[1].split()[0]) * fps)
        elif current is not None and "lavfi.scene_score=" in line:
            score = float(line.split("=", 1)[1])
            if 2 <= current <= total - 2:
                cuts[current] = max(cuts.get(current, 0.0), score)
    for line in Path(record["log"]).read_text(errors="replace").splitlines():
        if "black_start:" in line and "black_end:" in line:
            fields = dict(part.split(":", 1) for part in line.split() if ":" in part)
            try:
                start = Fraction(fields["black_start"]) * fps
                end = Fraction(fields["black_end"]) * fps
            except (KeyError, ValueError, ZeroDivisionError):
                continue
            middle = round((start + end) / 2)
            if 2 <= middle <= total - 2:
                cuts[middle] = max(cuts.get(middle, 0.0), 2.0)
    return cuts


def snap_ranges(frames, parts, offset, cuts, limit):
    """Even split moved to nearby shot changes without exceeding `limit` frames."""
    nominal = split_frames(frames, parts, offset)
    if parts == 1 or not cuts:
        return nominal
    window = min(max(25, frames // parts // 7), 1200)    # about +-14 %, at most ~48 s at 25 FPS
    result = []
    start = offset
    end_total = offset + frames
    for index in range(1, parts):
        ideal = nominal[index][0]
        remaining = parts - index
        low = max(start + 2, end_total - remaining * limit, ideal - window)
        high = min(start + limit, end_total - 2 * remaining, ideal + window)
        if low > high:
            # Keep the feasible even boundary; the window offered no room.
            boundary = min(max(ideal, start + 2), start + limit)
        else:
            candidates = [(frame, strength) for frame, strength in cuts.items()
                          if low <= frame <= high]
            if candidates:
                # Strongest change wins; distance to the even split breaks ties.
                boundary = max(candidates, key=lambda item: (
                    round(item[1], 1), -abs(item[0] - ideal)))[0]
            else:
                boundary = min(max(ideal, low), high)
        result.append((start, boundary))
        start = boundary
    result.append((start, end_total))
    if any(end - begin < 2 or end - begin > limit for begin, end in result):
        return nominal
    return result


def valid_ranges(ranges, bounds, parts):
    if len(ranges) != parts or not ranges:
        return False
    if ranges[0][0] != bounds[0] or ranges[-1][1] != bounds[1]:
        return False
    return all(b - a >= 2 and b - a <= cavc_routing.MAX_FRAMES and (index == 0 or ranges[index - 1][1] == a)
               for index, (a, b) in enumerate(ranges))


def part_bytes(profile):
    folder = ROOT / profile.artifact_dir
    return sum((folder / name).stat().st_size for name in ("HEADER.DAT", "BODY.DAT")) + PART_FIXED_BYTES


def group_discs(sizes, capacity=CD_BYTES, per_disc=MAX_PARTS):
    """Consecutive parts per physical disc, balanced, each within capacity."""
    usable = capacity - DISC_FIXED_BYTES
    if any(size > usable for size in sizes):
        raise ValueError("one part alone exceeds a CD; choose shorter chapters")
    total = sum(sizes)
    count = max(-(-total // usable), -(-len(sizes) // per_disc), 1)
    while count <= MAX_DISCS:
        groups, current, used = [], [], 0
        target = total / count
        consumed = 0
        for index, size in enumerate(sizes):
            left = count - len(groups) - 1
            over = used + size > usable or len(current) >= per_disc
            # Close the disc at the part boundary nearest its balanced share.
            past = current and left > 0 and consumed + size / 2 > target * (len(groups) + 1)
            if current and (over or past):
                groups.append(current)
                current, used = [], 0
            current.append(index)
            used += size
            consumed += size
        groups.append(current)
        if len(groups) == count and all(sum(sizes[i] for i in g) <= usable and len(g) <= per_disc
                                        for g in groups):
            return groups
        count += 1
    raise ValueError(f"the film needs more than {MAX_DISCS} CDs")


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
    identity = {"schema": 1, "parts": parts, "encoder": analysis_logs.encoder_version(),
                "source": sim_artifact_cache.source_identity(profile.data["source"]["path"]),
                "settings": sim_artifact_cache.effective_environment(environment)}
    if os.environ.get("ASTRA_SCENE_SPLIT") == "1":
        # Only new options extend the identity, so earlier checkpoints resume.
        identity["split"] = {"scene": True, "limit": int(os.environ.get("ASTRA_PART_LIMIT_FRAMES", "0"))}
    return identity


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
    """Prepare one indexed, lossless raster and filtered audio for all parts."""
    if os.environ.get("ASTRA_DESKTOP_PARTS") != "1":
        return None, fallback_frames
    if os.environ.get("ASTRA_DISABLE_SHARED_INPUT") == "1":
        return None, fallback_frames
    env = dict(os.environ)
    encode_config.apply_profile_env(profile, env)
    master, raw = astra_extract.filters(env)
    folder = Path(env["CAVC_WORKSPACE_ROOT"]) / "source-frames"
    volumes = {folder.parent: 5, ROOT: 2}
    # The WSL virtual disk can grow on the Windows system volume even when
    # Linux reports much more space inside its virtual filesystem.
    system_volume = Path("/mnt/c")
    if system_volume.is_mount():
        other_drive = folder.is_relative_to(Path("/mnt")) and not folder.is_relative_to(system_volume)
        volumes[system_volume] = 2 if other_drive else 5

    def retain_space():
        if any(shutil.disk_usage(volume).free < reserve * 1024**3
               for volume, reserve in volumes.items()):
            raise astra_extract.SharedInputSpaceError("shared extraction storage reserve")

    try:
        retain_space()
        if folder.is_dir():
            shutil.rmtree(folder)
        folder.mkdir()
        print("Extracción compartida de vídeo y audio en Linux…", flush=True)
        emit("prepare")
        workers = resource_tokens.requested_cpu_workers()
        with resource_tokens.acquire_tokens("cpu", count=workers):
            astra_extract.extract(folder, env, master, raw, workers=workers,
                                  audio_name=AUDIO_NAME, shared=True, disk_guard=retain_space)
    except astra_extract.SharedInputSpaceError:
        if folder.is_dir():
            shutil.rmtree(folder)
        print("La extracción compartida alcanzó la reserva de espacio. Se leerá por tramos.", flush=True)
        return None, fallback_frames
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
        ranges = split_frames(bounds[1]-bounds[0], parts, bounds[0])
        split = "even"
        if identity.get("split", {}).get("scene") and parts > 1:
            limit = identity["split"]["limit"] or max(b - a for a, b in ranges)
            limit = min(limit, cavc_routing.MAX_FRAMES)
            cuts = scene_cuts(profile, stem, total)
            ranges = snap_ranges(bounds[1]-bounds[0], parts, bounds[0], cuts, limit)
            split = "scene"
            moved = sum(1 for a, b in zip(ranges, split_frames(bounds[1]-bounds[0], parts, bounds[0]))
                        if a != b)
            print(f"multipart: {len(cuts)} cambios de escena; {moved} cortes ajustados a una escena", flush=True)
        plan = {"identity": identity, "stem": stem, "profile": str(profile.path),
                "source_frames": total, "split": split, "ranges": [list(r) for r in ranges]}
        write_json(plan_file, plan)
    bounds = frame_range(profile.section("source")) or (0, plan["source_frames"])
    if plan.get("split") == "scene":
        if not valid_ranges([tuple(r) for r in plan["ranges"]], bounds, parts) or plan["stem"] != stem:
            raise ValueError("multipart plan ranges or output stem differ")
    else:
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
                        help=f"Number of parts (1..{MAX_TOTAL_PARTS}); overrides [multipart].parts")
    parser.add_argument("--multi-disc", action="store_true",
                        help="Split the parts over as many 80-minute CDs as needed")
    parser.add_argument("--part", type=int, metavar="K",
                        help="Encode only part K (1-based); omission completes all parts")
    parser.add_argument("--build-only", action="store_true", help="Build from already completed parts")
    parser.add_argument("--debug", action="store_true", help="Include the playback DEBUG HUD")
    parser.add_argument("--security-region", choices=("jp", "us", "eu"), default="jp")
    args = parser.parse_args(argv)
    if args.parts is not None and not 1 <= args.parts <= MAX_TOTAL_PARTS:
        parser.error(f"--parts must be within 1..{MAX_TOTAL_PARTS}")
    if args.parts is not None and args.parts > MAX_PARTS and not args.multi_disc:
        parser.error(f"more than {MAX_PARTS} parts require --multi-disc")
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
        memory = next((int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                       if line.startswith("MemAvailable:")), 0) * 1024
        longest = max((end - start for start, end in plan["ranges"]), default=0)
        from astra_capacity import part_memory
        need = part_memory(longest)
        # Up to ~3:20 chapters keep the measured 12 GiB rule for two parts in flight;
        # longer chapters need two estimated peaks.
        floor = 12 * 1024**3 if longest <= 5200 else 2 * need
        if workers > 1 and (memory < floor or (os.cpu_count() or 1) < 8):
            workers = 1
            print("Recursos para una parte a la vez. Se continúa secuencialmente.", flush=True)
        elif workers > 1:
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
        # A rebuild may group the parts differently: drop earlier disc images
        # and receipts of this plan so only the new set is published.
        for stale in [*(ROOT / "out").glob(f"{plan['stem']}*.iso"),
                      *(ROOT / "out").glob(f"{plan['stem']}*.cue"),
                      *(ROOT / "out" / plan["stem"]).glob("multipart*.json")]:
            stale.unlink(missing_ok=True)
        if not args.multi_disc:
            if len(profiles) > MAX_PARTS:
                raise ValueError(f"one disc holds at most {MAX_PARTS} parts")
            multipart_build.build(plan, profiles, records, debug=args.debug, region=args.security_region)
            return 0
        capacity = int(os.environ.get("ASTRA_TEST_DISC_BYTES", CD_BYTES))   # tests only
        groups = group_discs([part_bytes(profile) for profile in profiles], capacity)
        if len(groups) == 1:
            multipart_build.build(plan, profiles, records, debug=args.debug, region=args.security_region)
            return 0
        print(f"multipart: {len(groups)} discos: " + "; ".join(
            f"disco {i}: partes {g[0]+1}-{g[-1]+1}" for i, g in enumerate(groups, 1)), flush=True)
        for index, group in enumerate(groups, 1):
            multipart_build.build(plan, [profiles[i] for i in group], [records[i] for i in group],
                                  debug=args.debug, region=args.security_region,
                                  disc=(index, len(groups)), numbers=[i + 1 for i in group])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
