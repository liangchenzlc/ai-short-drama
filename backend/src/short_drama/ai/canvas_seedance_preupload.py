"""BeefAPI Seedance 参考素材预上传；失败发生于付费视频提交之前。"""

import base64
import hashlib
import ipaddress
import json
import re
import time
from collections.abc import Callable
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image

from .canvas_beefapi_client import PRODUCTION_ORIGIN, CanvasBeefAPIError, canonical_origin
from .canvas_video_adapters import REFERENCE_GROUPS, canvas_video_endpoint
from .transport import RawBody, SafeTransport, validated_url
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
SKIP_PUT_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "x-api-key",
        "x-goog-api-key",
    }
)


def validate_beefapi_snapshot(snapshot: dict, settings) -> None:
    try:
        origin = canonical_origin(getattr(settings, "canvas_beefapi_test_origin", ""))
    except CanvasBeefAPIError:
        raise GenerationError("invalid_canvas_provider") from None
    base, root = urlsplit(validated_url(snapshot["base_url"])), urlsplit(origin)
    if snapshot.get("canvas_channel_key") != "beefapi" or (base.scheme, base.netloc) != (
        root.scheme,
        root.netloc,
    ):
        raise GenerationError("invalid_canvas_provider")


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise GenerationError("reference_upload_timeout")


def _secure_upload_url(value: str, settings) -> str:
    normalized = validated_url(value, query=True)
    parsed = urlsplit(normalized)
    test_origin = getattr(settings, "canvas_beefapi_test_origin", "")
    if parsed.scheme != "https":
        try:
            controlled_test = (
                test_origin
                and canonical_origin(test_origin) != PRODUCTION_ORIGIN
                and (
                    parsed.hostname in {"localhost", "enterprise.localhost"}
                    or ipaddress.ip_address(parsed.hostname).is_loopback
                )
            )
        except (ValueError, CanvasBeefAPIError):
            controlled_test = False
        if not controlled_test:
            raise GenerationError("invalid_upload_url")
    return normalized


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


def _upload_json(
    transport: SafeTransport, method: str, url: str, headers: dict, body: dict, deadline: float
) -> tuple[int, dict]:
    _check_deadline(deadline)
    try:
        status, _, raw = transport.request(
            method, url, headers=headers, body=body, max_bytes=1024**2, deadline=deadline
        )
    except GenerationError as error:
        raise GenerationError("reference_upload_failed", retryable=error.retryable) from None
    if status in {404, 501}:
        return status, {}
    if not 200 <= status < 300:
        raise GenerationError("reference_upload_failed", retryable=status == 429 or status >= 500)
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
    except (ValueError, UnicodeError):
        raise GenerationError("invalid_upload_response") from None
    return status, result


def prepare_beefapi_seedance_references(
    transport: SafeTransport,
    snapshot: dict,
    request: dict,
    headers: dict,
    loader: CanvasReferenceLoader | None,
    deadline: float,
) -> None:
    """串行上传；仅第一个 session-create 404/501 可退回原 inline 合同。"""
    validate_beefapi_snapshot(snapshot, transport.settings)
    value = request["canvas_request"]["input"]
    started = False
    for kind, group in REFERENCE_GROUPS:
        for index, item in enumerate(value.get(group) or []):
            if not _local_reference(item):
                continue
            data, mime = read_canvas_reference(transport, kind, index, item, loader, deadline)
            digest = hashlib.sha256(data).hexdigest()
            status, session = _upload_json(
                transport,
                "POST",
                canvas_video_endpoint(snapshot["base_url"], "/video-references/uploads"),
                headers,
                {"kind": kind, "bytes": len(data), "sha256": digest, "mime": mime},
                deadline,
            )
            if status in {404, 501}:
                if started:
                    raise GenerationError("reference_upload_failed")
                inline_canvas_references(transport, request, loader, deadline)
                return
            started = True
            method = session.get("upload_method", "PUT")
            if (
                not isinstance(session.get("ticket"), str)
                or not session["ticket"].strip()
                or not isinstance(method, str)
                or method.strip().upper() not in {"", "PUT"}
            ):
                raise GenerationError("invalid_upload_response")
            upload_url = _secure_upload_url(session.get("upload_url"), transport.settings)
            required = session.get("required_headers") or {}
            if not isinstance(required, dict) or len(required) > 64:
                raise GenerationError("invalid_upload_response")
            put_headers = {}
            for name, header_value in required.items():
                if (
                    not isinstance(name, str)
                    or not re_header_name(name)
                    or not isinstance(header_value, str)
                    or any(ord(c) < 32 or ord(c) == 127 for c in header_value)
                ):
                    raise GenerationError("invalid_upload_response")
                if name.lower() not in SKIP_PUT_HEADERS and not name.lower().startswith(
                    "x-canvas-"
                ):
                    put_headers[name] = header_value
            put_deadline = min(deadline, time.monotonic() + 900)
            try:
                put_status, _, _ = transport.request(
                    "PUT",
                    upload_url,
                    headers=put_headers,
                    body=RawBody(data, put_headers.get("Content-Type", mime)),
                    max_bytes=1024**2,
                    deadline=put_deadline,
                    query=True,
                )
            except GenerationError as error:
                raise GenerationError(
                    "reference_upload_failed", retryable=error.retryable
                ) from None
            if not 200 <= put_status < 300:
                raise GenerationError("reference_upload_failed")
            complete_status, completed = _upload_json(
                transport,
                "POST",
                canvas_video_endpoint(snapshot["base_url"], "/video-references/uploads/complete"),
                headers,
                {"ticket": session["ticket"]},
                deadline,
            )
            if (
                complete_status in {404, 501}
                or completed.get("kind") != kind
                or type(completed.get("bytes")) is not int
                or completed["bytes"] != len(data)
                or completed.get("sha256") != digest
            ):
                raise GenerationError("reference_upload_incomplete")
            canonical = canonical_media_mime(kind, completed.get("mime"), b"")
            url = _secure_upload_url(completed.get("url"), transport.settings)
            item.update(storageKey="", dataUrl="", url=url, type=canonical, bytes=len(data))


def re_header_name(value: str) -> bool:
    return bool(re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", value))
