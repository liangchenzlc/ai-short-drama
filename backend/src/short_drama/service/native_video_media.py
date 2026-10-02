"""Technical validation is deliberately separate from human voice/acting approval."""

import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .video_render import VideoRenderer, executable


def inspect_native_video(data, settings, dialogue_required):
    with TemporaryDirectory() as temp:
        path = Path(temp) / "result.mp4"
        path.write_bytes(data)
        info = VideoRenderer(settings).probe(path)
        args = [
            executable(settings.render_ffmpeg_path),
            "-nostdin",
            "-v",
            "info",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
        ]
        if info["has_audio"]:
            args += ["-af", "volumedetect"]
        decoded = subprocess.run(
            args + ["-f", "null", "-"],
            capture_output=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if decoded.returncode:
            raise ValueError("生成视频无法完整解码")
        peak = re.search(r"max_volume:\s*([-\w.]+) dB", decoded.stderr.decode(errors="replace"))
        audible = bool(info["has_audio"] and peak and float(peak[1]) > -60)
        info["native_quality"] = {
            "technical_pass": not dialogue_required or audible,
            "dialogue_required": dialogue_required,
            "issue": "missing_or_silent_audio" if dialogue_required and not audible else None,
            "voice_fidelity": "requires_human_review",
            "decode": "passed",
        }
        return info
