"""Bounded resource inspection and the source single-range download semantics."""

import hashlib
import json
import math
import mimetypes
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from short_drama.core.exceptions import ConfigurationError, WorkflowError

from .video_render import executable


@dataclass(frozen=True)
class ResourceInspection:
    mime_type: str
    size: int
    checksum: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    video_metadata: dict | None = None


def inspect_resource(stream: BinaryIO, name: str, kind: str, declared: str, settings):
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    stream.seek(0)
    mime = declared.partition(";")[0].strip().lower()
    if not mime or mime == "application/octet-stream":
        mime = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json"}.get(
            Path(name).suffix.lower(), mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*", mime)
        or len(mime) > 127
    ):
        raise WorkflowError("canvas_resource_type_invalid", "文件 MIME 类型无效", 422)
    if kind != "file" and not mime.startswith(kind + "/"):
        raise WorkflowError("canvas_resource_type_invalid", "文件类型与上传种类不符", 422)
    fields = {}
    if kind == "image" and mime != "image/svg+xml":
        try:
            with Image.open(stream) as picture:
                fields = {"width": picture.width, "height": picture.height}
                mime = Image.MIME.get(picture.format, mime)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise WorkflowError(
                "canvas_resource_invalid", "无法读取图片，请检查文件", 422
            ) from None
        finally:
            stream.seek(0)
    elif kind in {"audio", "video"}:
        fields = probe_resource(stream, kind, settings)
    stream.seek(0)
    return ResourceInspection(mime, size, checksum, **fields)


def probe_resource(stream: BinaryIO, kind: str, settings):
    try:
        binary = executable(settings.render_ffprobe_path)
    except RuntimeError:
        raise ConfigurationError("画布音视频上传需要配置可用的 ffprobe") from None
    with TemporaryDirectory(prefix="canvas-probe-") as directory:
        path = Path(directory) / "media.bin"
        with path.open("wb") as target:
            stream.seek(0)
            shutil.copyfileobj(stream, target, length=1024 * 1024)
        try:
            result = subprocess.run(
                [
                    binary,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "mov,matroska,webm,avi,mp3,wav,flac,ogg,aac,mpeg,mpegts,asf,aiff,amr",
                    "-show_entries",
                    "stream=codec_type,width,height,avg_frame_rate:format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode or len(result.stdout) > 1024 * 1024:
                raise ValueError
            data = json.loads(result.stdout)
            track = next(item for item in data.get("streams", []) if item.get("codec_type") == kind)
            duration = float(data.get("format", {}).get("duration", 0))
            if not math.isfinite(duration) or duration <= 0 or duration * 1000 > 2**53 - 1:
                raise ValueError
            fields = {"duration_ms": max(1, round(duration * 1000))}
            if kind == "video":
                if not all(
                    type(track.get(key)) is int and 0 < track[key] < 2**32
                    for key in ("width", "height")
                ):
                    raise ValueError
                fields.update(width=track["width"], height=track["height"])
                fields["video_metadata"] = {
                    "probe_version": 1,
                    "fps": track.get("avg_frame_rate", "0/1"),
                    "has_audio": any(
                        item.get("codec_type") == "audio" for item in data.get("streams", [])
                    ),
                    **fields,
                }
            return fields
        except (OSError, subprocess.TimeoutExpired):
            raise WorkflowError(
                "canvas_resource_probe_unavailable", "音视频检测暂时失败，请重试", 503
            ) from None
        except (ValueError, KeyError, TypeError, StopIteration):
            raise WorkflowError(
                "canvas_resource_invalid", "音视频缺少有效轨道或时长", 422
            ) from None
        finally:
            stream.seek(0)


def resource_byte_range(value: str | None, size: int) -> tuple[int, int, int]:
    """Invalid syntax is ignored, a valid unsatisfiable range returns 416, as in BeefTV."""
    value = (value or "").strip()
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value) if len(value) <= 128 else None
    if not match or not any(match.groups()):
        return 200, 0, size
    left, right = match.groups()
    start, end = int(left or "0"), int(right or "0")
    if start > 2**63 - 1 or end > 2**63 - 1 or size <= 0:
        return 416, 0, 0
    if not left:
        if end <= 0:
            return 416, 0, 0
        start, end = max(0, size - end), size - 1
    elif not right:
        end = size - 1
    if start >= size or end < start:
        return 416, 0, 0
    return 206, start, min(size - 1, end) - start + 1
