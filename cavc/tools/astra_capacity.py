"""CAVC/LORE estimates shared by the Windows UI and the WSL writer."""
import math

CD_BYTES = 360_000 * 2048
DISC_RESERVE = 2 * 1024**2
DISC_FIXED_BYTES = 3 * 1024**2          # boot, loader and ISO records of each extra disc
PART_RESERVE = 512 * 1024
MAX_PART_FRAMES = 4500                  # "Automático": 3:00 at 25 FPS, two parts in parallel
MAX_CHAPTER_MINUTES = 15
SCENE_SLACK = 1.12                      # a chapter may stretch 12 % to end on a shot change
MAX_PARTS = 64                          # parts on one physical disc
MAX_DISCS = 8
MAX_TOTAL_PARTS = MAX_PARTS * MAX_DISCS
# Measured encoder peak (array cache in RAM): ~0.5 GiB + ~1.4 MiB per frame.
MEMORY_FIXED = 640 * 1024**2
MEMORY_PER_FRAME = int(1.45 * 1024**2)


def part_limit(fps, part_minutes=0, preview_mp4=False, frames=0):
    """Largest frame count of one part (one chapter between disc reloads)."""
    if part_minutes:
        return max(2, round(min(part_minutes, MAX_CHAPTER_MINUTES) * 60 * fps))
    return 9000 if preview_mp4 and frames <= 9000 else MAX_PART_FRAMES


def estimate(duration, fps, part_minutes=0, preview_mp4=False, *, frames=None):
    frames = max(2, round(duration * fps)) if frames is None else frames
    limit = part_limit(fps, part_minutes, preview_mp4, frames)
    parts = max(1, math.ceil(frames / limit))
    seconds = max(duration, frames / fps) * (25 / 24.85073 if fps == 25 else 1.002)
    return parts, math.ceil(seconds * 75) * 2048 + DISC_RESERVE + parts * PART_RESERVE


def discs(total_bytes):
    """Physical CDs for an estimate when the film may span several discs."""
    if total_bytes <= CD_BYTES:
        return 1
    return math.ceil((total_bytes - DISC_RESERVE) / (CD_BYTES - DISC_FIXED_BYTES))


def part_memory(frames):
    """Conservative peak memory of one part's encoder with in-RAM arrays."""
    return MEMORY_FIXED + frames * MEMORY_PER_FRAME
