"""Decode managed local audio before it is admitted to the sound editor."""

import re
import subprocess
from pathlib import Path

from .video_render import VideoRenderer, executable

MAX_AUDIO_BYTES = 100 * 1024**2


def inspect_audio(path, settings):
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise ValueError("音频须小于 100 MiB 且不能为空")
    with path.open("rb") as stream:
        head = stream.read(16)
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        mime, ext = "audio/wav", "wav"
    elif head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 255 and head[1] & 224 == 224):
        mime, ext = "audio/mpeg", "mp3"
    elif head[4:8] == b"ftyp":
        mime, ext = "audio/mp4", "m4a"
    else:
        raise ValueError("仅支持 MP3、WAV、M4A")
    # Decode, not just trust the filename/MIME/container header. Network protocols are disabled.
    result = subprocess.run(
        [
            executable(settings.render_ffmpeg_path),
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-t",
            "3600.001",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    report = result.stderr.decode("utf-8", errors="replace")
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", report)
    if result.returncode or not duration or re.search(r"Stream[^\n]+Video:", report):
        raise ValueError("音频无法解码或包含视频轨道")
    ms = round((int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) * 1000)
    if not 0 < ms <= 3600000:
        raise ValueError("音频时长须在 0 到 60 分钟之间")
    return {"mime": mime, "ext": ext, "duration_ms": ms}


def audio_proxy(path, directory, settings):
    output = directory / "proxy.m4a"
    VideoRenderer(settings).run(
        [
            executable(settings.render_ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        directory,
        stage="preparing",
        progress=0,
    )
    return output
