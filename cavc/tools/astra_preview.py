"""Encode the decoded CAVC picture and the modeled RF5C164 audio as MP4."""
from pathlib import Path
import subprocess
import sys

import av_config
from astra_replay import render
from astra_progress import emit

folder = Path(sys.argv[1])
picture = render(folder, lambda index, _path, total, *_: emit("preview", index + 1, total))
fps = av_config.playback_fps_for_content(float(sys.argv[2]))
subprocess.run([
    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
    "-framerate", str(fps), "-i", str(folder / "preview" / "%05d.png"),
    "-i", str(folder / picture["audio"]), "-vf", "setsar=32/35", "-c:v", "libx264",
    "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "apad", "-movflags", "+faststart", "-shortest",
    sys.argv[3],
], check=True)

