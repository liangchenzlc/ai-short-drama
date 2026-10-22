"""Shared local render executable discovery and file digests."""

import hashlib
import shutil
from pathlib import Path


def executable(configured):
    found = shutil.which(configured)
    if found:
        return found
    # Portable development tools are intentionally untracked.
    name = Path(configured).name
    if name in ("ffmpeg", "ffprobe"):
        for candidate in Path(".tools/ffmpeg").glob(f"*/bin/{name}.exe"):
            return str(candidate.resolve())
    raise RuntimeError("未找到 FFmpeg 或 ffprobe，请配置合成服务后重试")


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
