"""Runtime storage choices, independent of source profiles and codec decisions."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


DEFAULT_WORKSPACE = Path(__file__).resolve().parents[1] / "tmp" / "workspace"
GIB = 1024**3


def desktop_workspace(default: Path, frames: int, width: int, height: int) -> Path:
    """Keep small jobs on Linux and use a roomier local volume when needed."""
    system = Path("/mnt/c")
    expected = 5 * GIB + max(0, frames) * width * height * 2
    if not system.is_mount() or shutil.disk_usage(system).free >= expected:
        return default
    try:
        result = subprocess.check_output([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3').DeviceID",
        ], timeout=8, text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return default
    candidates = []
    for line in result.splitlines():
        drive = line.strip().lower()
        if len(drive) != 2 or drive[1] != ":" or not "d" <= drive[0] <= "z":
            continue
        path = Path("/mnt") / drive[0]
        try:
            if path.is_mount():
                candidates.append((shutil.disk_usage(path).free, path))
        except OSError:
            continue
    for free, path in sorted(candidates, reverse=True):
        if free < expected:
            break
        scratch = path / "MegaCD-Estudio-Temporales"
        try:
            scratch.mkdir(exist_ok=True)
            if os.access(scratch, os.W_OK):
                return scratch
        except OSError:
            continue
    return default


def workspace_root() -> Path:
    # Preserve existing launchers while allowing ordinary disk filesystems.
    return Path(os.environ.get("CAVC_WORKSPACE_ROOT") or os.environ.get(
        "SEGACD_TMPFS_ROOT") or DEFAULT_WORKSPACE).expanduser().resolve()


def scratch_root() -> Path:
    path = Path(os.environ.get("CAVC_SCRATCH_ROOT") or os.environ.get(
        "TMPDIR") or workspace_root() / "scratch").expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def array_storage() -> str:
    value = os.environ.get("CAVC_ARRAY_STORAGE", "memory")
    if value not in ("memory", "disk"):
        raise ValueError("CAVC_ARRAY_STORAGE must be memory or disk")
    return value
