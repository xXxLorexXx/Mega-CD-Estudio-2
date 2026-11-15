"""Select repeated display frames on an existing 25 Hz content clock."""

from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PIL import Image


def _load_luma(path):
    if isinstance(path, np.ndarray):
        return path.astype(np.int16)
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.int16)


@dataclass(frozen=True)
class DisplayPlan:
    holds: np.ndarray
    cuts: np.ndarray


def plan_display(raw_frames, locked, known_cuts=(), *, allow_holds=True, cold_caps=None):
    """Use three updates per five ticks in sustained high motion.

    Cuts and mandatory player frames are always displayed on time.
    Measurements use only the active image and ignore faint pixel changes.
    """
    count = len(raw_frames)
    activity = np.zeros(count)
    pressure = np.zeros(count) if cold_caps is not None else None
    cuts = np.zeros(count, dtype=bool)
    cuts[list(known_cuts)] = True
    previous = None

    def _measure_transition(frame, luma, prev):
        active = (luma.max(axis=1) > 20) | (prev.max(axis=1) > 20)
        delta = np.abs(luma[active] - prev[active])
        if delta.size:
            activity[frame] = np.mean(delta >= 12)
            cuts[frame] |= np.mean(delta >= 30) >= 0.36
        if pressure is not None:
            rows, cols = luma.shape[0] // 8, luma.shape[1] // 8
            delta_tiles = np.abs(luma - prev).reshape(rows, 8, cols, 8)
            tile_error = delta_tiles.mean(axis=(1, 3))
            visible_change = (delta_tiles >= 12).sum(axis=(1, 3))
            changed = (tile_error >= 5) & (visible_change >= 10)
            need = np.minimum(tile_error[changed] / 12.0, 1.0).sum()
            pressure[frame] = need / max(1, cold_caps[frame])

    if isinstance(raw_frames, np.ndarray):
        for frame in range(count):
            luma = raw_frames[frame].astype(np.int16)
            if previous is not None:
                _measure_transition(frame, luma, previous)
            previous = luma
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending = deque()
            iterator = iter(raw_frames)
            for _ in range(16):
                path = next(iterator, None)
                if path is not None:
                    pending.append(pool.submit(_load_luma, path))
            for frame in range(count):
                luma = pending.popleft().result()
                path = next(iterator, None)
                if path is not None:
                    pending.append(pool.submit(_load_luma, path))
                if previous is not None:
                    _measure_transition(frame, luma, previous)
                previous = luma

    holds = np.zeros(count, dtype=bool)
    if not allow_holds:
        return DisplayPlan(holds, cuts)
    phase = 0
    for frame in range(1, count - 1):
        if locked[frame] or cuts[frame] or cuts[frame + 1]:
            phase = 0
            continue
        start, stop = max(1, frame - 3), min(count, frame + 4)
        window = activity[start:stop][~cuts[start:stop]]
        if pressure is not None:
            # Adjacent changes compete for the same stock of new patterns.
            demand = pressure[start:stop][~cuts[start:stop]]
            load = np.median(demand) if demand.size else 0
            broad_motion = window.size and np.median(window) >= 0.22
            if not broad_motion and load < 1.0:
                phase = 0
                continue
            holds[frame] = phase == 0 or (phase == 2 and (broad_motion or load >= 1.30))
            phase = (phase + 1) % 5
            continue
        if not window.size or np.median(window) < 0.22:
            phase = 0
            continue
        holds[frame] = phase in (0, 2)
        phase = (phase + 1) % 5
    return DisplayPlan(holds, cuts)


def preload_requests(plan, locked, indices, detail):
    """Reuse the normal raw-pattern prefetch path for holds and hard cuts."""
    requests = {}
    for frame in np.flatnonzero(plan.holds):
        requests[int(frame)] = (int(frame + 1), 120)
    for cut in np.flatnonzero(plan.cuts):
        for frame in range(max(1, int(cut) - 4), int(cut)):
            if not locked[frame] and not plan.cuts[frame]:
                target, limit = requests.get(frame, (int(cut), 64))
                if not plan.holds[frame] or target == cut:
                    requests[frame] = (int(cut), limit)

    keys = {}
    for deadline, _ in requests.values():
        if deadline in keys:
            continue
        # Patterns shared by several cells repair more image per transfer.
        # Among equally common patterns, give detailed contours precedence.
        patterns = [row.tobytes() for row in indices[deadline]]
        frequency = Counter(patterns)
        importance = {}
        for pattern, value in zip(patterns, detail[deadline], strict=True):
            importance[pattern] = max(importance.get(pattern, 0.0), float(value))
        keys[deadline] = tuple(sorted(
            frequency, key=lambda key: (-frequency[key], -importance[key])))
    return {
        frame: (deadline, keys[deadline], limit)
        for frame, (deadline, limit) in requests.items()
    }
