"""CAVC integration for Astra 8. The encoder runs in the local WSL distro."""
from datetime import datetime
import hashlib
import msvcrt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


CAVC_ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "cavc"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class CavcCancelled(Exception):
    pass


def read_cavc_info(image):
    if not image:
        return {}
    try:
        return json.loads(Path(image).with_name("CAVC.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cleanup_job_artifacts(job):
    """Remove transient WSL/CAVC job files and staged out entries after transfer."""
    shutil.rmtree(job, ignore_errors=True)
    for base in (CAVC_ROOT / "tmp", CAVC_ROOT / "out"):
        if base.is_dir():
            for match in base.glob(f"{job.name}*"):
                if match.is_dir():
                    shutil.rmtree(match, ignore_errors=True)
                else:
                    match.unlink(missing_ok=True)
    logs_dir = CAVC_ROOT / "logs"
    if logs_dir.is_dir():
        for match in logs_dir.glob(f"*{job.name}*"):
            if match.is_file():
                match.unlink(missing_ok=True)


def _prune_incomplete_job(job, *, keep_log=False):
    """Retain only verified completed multipart checkpoints; purge single-part or partial debris."""
    out_base = CAVC_ROOT / "out"
    completed_markers = list(out_base.glob(f"{job.name}*/multipart-complete.json")) if out_base.is_dir() else []
    if not completed_markers:
        if not keep_log:
            _cleanup_job_artifacts(job)
            return
        for sub in ("sim", "disc", "cancel", "progress.jsonl", f"{job.name}.toml"):
            target = job / sub
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for base in (CAVC_ROOT / "tmp", CAVC_ROOT / "out"):
            if base.is_dir():
                for match in base.glob(f"{job.name}*"):
                    if match == job:
                        continue
                    if match.is_dir():
                        shutil.rmtree(match, ignore_errors=True)
                    else:
                        match.unlink(missing_ok=True)
        return

    # At least one multipart checkpoint exists: remove only unverified part dirs and transient files.
    for sub in ("sim", "disc", "cancel"):
        target = job / sub
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
    if out_base.is_dir():
        for match in out_base.glob(f"{job.name}*"):
            if match.is_dir() and "-part" in match.name:
                if not (match / "multipart-complete.json").is_file():
                    shutil.rmtree(match, ignore_errors=True)
            elif match.is_file() and match.suffix.lower() in (".iso", ".cue"):
                match.unlink(missing_ok=True)
    tmp_base = CAVC_ROOT / "tmp"
    if tmp_base.is_dir():
        for match in tmp_base.glob(f"{job.name}-multipart*"):
            if match.is_dir():
                for sub in list(match.glob("handoff-*")) + [match / "multipart.pending.iso", match / "disc"]:
                    if sub.is_dir():
                        shutil.rmtree(sub, ignore_errors=True)
                    else:
                        sub.unlink(missing_ok=True)


def cavc_cache_bytes():
    """Return total bytes stored in CAVC/LORE temporary, output, and log caches."""
    total = 0
    for name in ("tmp", "out", "logs"):
        folder = CAVC_ROOT / name
        if not folder.is_dir():
            continue
        for item in folder.rglob("*"):
            if ".locks" in item.parts:
                continue
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                pass
    return total


def clear_cavc_cache():
    """Remove cached CAVC/LORE temporary jobs, incomplete outputs, and logs. Returns bytes freed."""
    before = cavc_cache_bytes()
    for name in ("tmp", "out", "logs"):
        folder = CAVC_ROOT / name
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.name == ".locks":
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    distro = os.environ.get("MEGACD_CAVC_DISTRO", "Ubuntu-24.04")
    try:
        subprocess.run(
            ["wsl.exe", "-d", distro, "--exec", "bash", "-c",
             "rm -rf ~/.local/share/mega-cd-estudio/cavc81-workspace/astra_* 2>/dev/null || true"],
            cwd=CAVC_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW, timeout=5, check=False,
        )
    except OSError:
        pass
    after = cavc_cache_bytes()
    return max(0, before - after)


def convert_cavc(sources, output, settings, cancel, report):
    temporary = CAVC_ROOT / "tmp"
    temporary.mkdir(exist_ok=True)
    settings = {k: v for k, v in settings.items() if k != "auto_burn"}
    payload = {**settings, "sources": sources}
    if settings.get("preview"):
        job = Path(tempfile.mkdtemp(prefix="astra_", dir=temporary))
        return _run_cavc(job, payload, output, cancel, report)

    # The packer verifies the source and each completed part by SHA-256.
    identities = []
    for source in sources:
        path = Path(source["path"]).resolve()
        stat = path.stat()
        identities.append((str(path), stat.st_size, stat.st_mtime_ns))
    identity_settings = {k: v for k, v in payload.items() if k != "part_workers"}
    key = hashlib.sha256(json.dumps([identity_settings, identities], sort_keys=True,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    job = temporary / f"astra_{key}"
    locks = temporary / ".locks"
    locks.mkdir(exist_ok=True)
    with (locks / key).open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Ya se está convirtiendo esta selección con los mismos ajustes.") from error
        try:
            job.mkdir(exist_ok=True)
            return _run_cavc(job, payload, output, cancel, report)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _run_cavc(job, settings, output, cancel, report):
    codec = settings.get("codec", "CAVC")
    if codec not in ("CAVC", "LORE"):
        raise ValueError(f"Códec desconocido: {codec}")
    if codec == "LORE" and (settings["region"] != "eu" or settings["fps"] != 25):
        raise ValueError("LORE requiere una consola PAL y un reloj de 25 FPS nominales.")
    (job / "cancel").unlink(missing_ok=True)
    (job / "result.json").unlink(missing_ok=True)
    (job / "job.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    distro = os.environ.get("MEGACD_CAVC_DISTRO", "Ubuntu-24.04")
    command = ["wsl.exe", "-d", distro, "--exec", "bash", "tools/astra_launch.sh",
               job.relative_to(CAVC_ROOT).as_posix()]
    environment = {**os.environ, "WSL_UTF8": "1"}
    log = job / "encoder.log"
    output = Path(output).resolve()
    cleanup_ok = bool(settings.get("preview"))
    keep_log = False
    try:
        progress = job / "progress.jsonl"
        progress.write_bytes(b"")
        report(1, f"Iniciando {codec} en WSL")
        reported = 1
        part_progress = {}
        with log.open("wb") as writer, progress.open("rb") as reader:

            def read_progress():
                nonlocal reported
                latest = None
                while True:
                    position = reader.tell()
                    line = reader.readline()
                    if not line or not line.endswith(b"\n"):
                        reader.seek(position)
                        break
                    try:
                        event = json.loads(line)
                        percent = int(event["percent"])
                        if "part" in event:
                            part = int(event["part"])
                            part_progress[part] = max(part_progress.get(part, 0), percent - 8)
                            percent = 8 + sum(part_progress.values()) // int(event["parts"])
                        reported = max(reported, min(99, percent))
                        latest = str(event["stage"])
                    except (ValueError, KeyError, TypeError):
                        continue
                if latest is not None:
                    report(reported, latest)
            with subprocess.Popen(command, cwd=CAVC_ROOT, env=environment, stdout=writer,
                                  stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW) as child:
                while child.poll() is None:
                    if cancel.is_set():
                        (job / "cancel").touch()
                    else:
                        read_progress()
                    time.sleep(.25)
                code = child.returncode
                if not cancel.is_set():
                    read_progress()
        if cancel.is_set():
            raise CavcCancelled()
        if code:
            cleanup_ok = False
            keep_log = True
            tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:])
            raise RuntimeError(f"{codec} terminó con código {code}.\n\n{tail}\n\nRegistro: {log}")

        result = json.loads((job / "result.json").read_text(encoding="utf-8"))
        report(99, "Guardando los archivos verificados")
        output.mkdir(parents=True, exist_ok=True)
        dest = output / f"CD_{codec}_{settings['region'].upper()}_{datetime.now():%Y%m%d_%H%M%S}_{job.name[-6:]}"
        staging = Path(tempfile.mkdtemp(prefix=".cavc-", dir=output))
        try:
            for name in ("iso", "cue", "preview"):
                if result.get(name):
                    source = CAVC_ROOT / result[name]
                    shutil.copy2(source, staging / source.name)
                    result[name] = source.name
            shutil.copy2(log, staging / f"Registro_{codec}.txt")
            (staging / "CAVC.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            if cancel.is_set():
                raise CavcCancelled()
            staging.rename(dest)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        cleanup_ok = True
        return dest, result
    except FileNotFoundError as exc:
        cleanup_ok = False
        raise RuntimeError(f"No se encontró el entorno o un archivo de {codec}. "
                           f"Comprueba la instalación con Preparar_CAVC.bat.\n\n{exc}") from exc
    finally:
        if cleanup_ok:
            _cleanup_job_artifacts(job)
        else:
            _prune_incomplete_job(job, keep_log=keep_log)

