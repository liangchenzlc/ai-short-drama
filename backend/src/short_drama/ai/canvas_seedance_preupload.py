"""画布参考素材的内联与读取助手：校验、读取与 base64 内联；失败发生于付费视频提交之前。"""

import base64
import time
from collections.abc import Callable
from io import BytesIO

from PIL import Image

from .canvas_video_adapters import REFERENCE_GROUPS
from .transport import SafeTransport
from .types import GenerationError

CanvasReferenceLoader = Callable[[str, int, int, float], bytes]
MEDIA_LIMITS = {"image": 30 * 1024**2, "video": 200 * 1024**2, "audio": 15 * 1024**2}
JSON_LIMIT = 64 * 1024**2
JSON_OVERHEAD = 64 * 1024
MIMES = {
    "image": {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"},
    "video": {"video/mp4", "video/quicktime"},
    "audio": {"audio/mpeg", "audio/wav"},
}


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise GenerationError("reference_upload_timeout")


def canonical_media_mime(kind: str, declared: str, data: bytes) -> str:
    mime = str(declared or "").split(";", 1)[0].strip().lower()
    mime = {
        "image/jpg": "image/jpeg",
        "audio/mp3": "audio/mpeg",
        "audio/wave": "audio/wav",
        "audio/x-wav": "audio/wav",
    }.get(mime, mime)
    if mime in MIMES[kind]:
        return mime
    if kind == "image":
        try:
            with Image.open(BytesIO(data)) as image:
                mime = {
                    "PNG": "image/png",
                    "JPEG": "image/jpeg",
                    "WEBP": "image/webp",
                    "GIF": "image/gif",
                    "BMP": "image/bmp",
                    "TIFF": "image/tiff",
                }.get(image.format)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            mime = None
    elif kind == "video" and len(data) >= 12 and data[4:8] == b"ftyp":
        mime = "video/quicktime" if data[8:12] == b"qt  " else "video/mp4"
    elif kind == "audio":
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            mime = "audio/wav"
        elif data.startswith(b"ID3") or (
            len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        ):
            mime = "audio/mpeg"
    if mime not in MIMES[kind]:
        raise GenerationError("invalid_reference_media")
    return mime


def _local_reference(item: dict) -> bool:
    return bool(item.get("storageKey")) or str(
        item.get("dataUrl") or item.get("url") or ""
    ).startswith("data:")


def read_canvas_reference(
    transport: SafeTransport,
    kind: str,
    index: int,
    item: dict,
    loader: CanvasReferenceLoader | None,
    deadline: float,
) -> tuple[bytes, str]:
    _check_deadline(deadline)
    limit = MEDIA_LIMITS[kind]
    if item.get("bytes") and item["bytes"] > limit:
        raise GenerationError("reference_media_too_large")
    if item.get("storageKey"):
        if loader is None:
            raise GenerationError("unresolved_media_reference")
        try:
            data = loader(kind, index, limit, deadline)
        except GenerationError as error:
            raise GenerationError("reference_load_failed", retryable=error.retryable) from None
        except (OSError, ValueError, TypeError):
            raise GenerationError("reference_load_failed") from None
        declared = item.get("type") or item.get("mimeType") or ""
    elif str(item.get("dataUrl") or item.get("url") or "").startswith("data:"):
        raw = item.get("dataUrl") or item.get("url")
        if len(raw) > (limit + 2) // 3 * 4 + 256:
            raise GenerationError("reference_media_too_large")
        try:
            head, encoded = raw.split(",", 1)
            if not head.endswith(";base64"):
                raise ValueError
            data = base64.b64decode(encoded, validate=True)
            declared = head[5:-7]
        except (ValueError, TypeError):
            raise GenerationError("invalid_reference_media") from None
    else:
        data, declared = transport.download_media(item["url"], limit, deadline=deadline)
    _check_deadline(deadline)
    if not isinstance(data, bytes) or not data or len(data) > limit:
        raise GenerationError("reference_media_too_large")
    return data, canonical_media_mime(kind, declared, data)


def inline_canvas_references(
    transport: SafeTransport, request: dict, loader: CanvasReferenceLoader | None, deadline: float
) -> None:
    value = request["canvas_request"]["input"]
    total = JSON_OVERHEAD
    for kind, group in REFERENCE_GROUPS:
        for index, item in enumerate(value.get(group) or []):
            if not _local_reference(item):
                continue
            if item.get("bytes") and total + (item["bytes"] + 2) // 3 * 4 + 128 > JSON_LIMIT:
                raise GenerationError("reference_inline_too_large")
            data, mime = read_canvas_reference(transport, kind, index, item, loader, deadline)
            encoded = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            total += len(encoded)
            if total > JSON_LIMIT:
                raise GenerationError("reference_inline_too_large")
            item.update(storageKey="", dataUrl=encoded, url="", type=mime, bytes=len(data))
