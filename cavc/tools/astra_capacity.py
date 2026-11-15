"""CAVC/LORE estimates shared by the Windows UI and the WSL writer."""
import math

CD_BYTES = 360_000 * 2048
DISC_RESERVE = 2 * 1024**2
PART_RESERVE = 512 * 1024
MAX_PART_FRAMES = 4500
MAX_PARTS = 64


def estimate(duration, fps, part_minutes=0, preview_mp4=False, *, frames=None):
    frames = max(2, round(duration * fps)) if frames is None else frames
    limit = 9000 if not part_minutes and preview_mp4 and frames <= 9000 else MAX_PART_FRAMES
    if part_minutes:
        limit = min(limit, part_minutes * 60 * fps)
    parts = max(1, math.ceil(frames / limit))
    seconds = max(duration, frames / fps) * (25 / 24.85073 if fps == 25 else 1.002)
    return parts, math.ceil(seconds * 75) * 2048 + DISC_RESERVE + parts * PART_RESERVE
