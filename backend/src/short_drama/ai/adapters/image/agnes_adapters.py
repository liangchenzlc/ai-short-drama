"""Agnes 免费模型的显式画布合同；参数来自已保存的模型与冻结请求。"""

import json
import re
from hashlib import sha256
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit

from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError, GenerationResult
from short_drama.schemas.canvas.generation.generation_parameters import CanvasImageParameters

AGNES_IMAGE = "agnes-image-2.5-flash"
AGNES_VIDEO = "agnes-video-2.5-flash"
IMAGE_RATIOS = frozenset({"1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"})
VIDEO_RATIOS = IMAGE_RATIOS - {"2:3", "3:2"}
REFERENCE_URL_LIMIT = 8192
REFERENCE_STRING_BUDGET = 32768
REFERENCE_TOKEN_KEYS = frozenset({"x-amz-signature", "x-amz-credential", "x-amz-security-token"})


class AgnesReferenceEcho(GenerationError):
    """拒绝输入签名回显，只携带已核对的供应商标识。"""

    def __init__(self, *, submitted: bool, provider_task_id: str | None = None):
        super().__init__("reference_output_rejected", accepted_unknown=submitted)
        self.provider_task_id = provider_task_id


def agnes_has_stored_references(request: dict) -> bool:
    value = request.get("input") or {}
    return any(
        value.get(field)
        for field in (
            "reference_media_ids",
            "video_reference_media_ids",
            "audio_reference_media_ids",
        )
    )


def _reference_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _reference_variants(value: str) -> set[str]:
    variants = {value}
    for _ in range(3):
        decoded = unquote_plus(value)
        if decoded == value:
            break
        variants.add(decoded)
        value = decoded
    if unquote_plus(value) != value:
        raise ValueError("Reference decoding exceeds bounded depth")
    return variants


def agnes_reference_guard(urls: list[str]) -> dict:
    """只保留有界单向指纹；原 URL 和签名不进入数据库。"""
    if not 1 <= len(urls) <= 8:
        raise GenerationError("reference_guard_unavailable")
    url_hashes, tokens = set(), set()
    for url in urls:
        if not isinstance(url, str) or not 1 <= len(url) <= REFERENCE_URL_LIMIT:
            raise GenerationError("reference_guard_unavailable")
        try:
            url_hashes.update(_reference_digest(value) for value in _reference_variants(url))
            query = parse_qsl(urlsplit(url).query, keep_blank_values=True, max_num_fields=64)
        except (ValueError, UnicodeError):
            raise GenerationError("reference_guard_unavailable") from None
        for key, value in query:
            if key.lower() in REFERENCE_TOKEN_KEYS and value:
                tokens.add((len(value), _reference_digest(value)))
    guard = {
        "version": 1,
        "reference_count": len(urls),
        "urls": sorted(url_hashes),
        "tokens": [{"length": length, "sha256": digest} for length, digest in sorted(tokens)],
    }
    validate_agnes_reference_guard(guard)
    return guard


def validate_agnes_reference_guard(guard: object) -> dict:
    """旧任务缺少或损坏指纹时停止外部操作，不重新签名补全。"""

    def valid_digest(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)

    if (
        not isinstance(guard, dict)
        or set(guard) != {"version", "reference_count", "urls", "tokens"}
        or type(guard["version"]) is not int
        or guard["version"] != 1
        or type(guard["reference_count"]) is not int
        or not 1 <= guard["reference_count"] <= 8
        or not isinstance(guard["urls"], list)
        or not 1 <= len(guard["urls"]) <= 32
        or not all(valid_digest(value) for value in guard["urls"])
        or not isinstance(guard["tokens"], list)
        or not 1 <= len(guard["tokens"]) <= 24
        or any(
            not isinstance(item, dict)
            or set(item) != {"length", "sha256"}
            or type(item["length"]) is not int
            or not 1 <= item["length"] <= REFERENCE_URL_LIMIT
            or not valid_digest(item["sha256"])
            for item in guard["tokens"]
        )
    ):
        raise GenerationError("reference_guard_unavailable")
    return guard


def _reference_echo(value: str, guard: dict) -> bool:
    try:
        for variant in _reference_variants(value):
            if _reference_digest(variant) in guard["urls"]:
                return True
            for item in guard["tokens"]:
                length = item["length"]
                if any(
                    _reference_digest(variant[start : start + length]) == item["sha256"]
                    for start in range(len(variant) - length + 1)
                ):
                    return True
    except (ValueError, UnicodeError):
        return True
    return False


def check_agnes_reference_echo(
    value: object, guard: dict, *, submitted: bool, secrets: tuple[str, ...] | list[str] = ()
) -> None:
    """签名复用按值阻断，改路径或查询字段名不能绕过；拒绝扫描大块媒体。"""
    validate_agnes_reference_guard(guard)
    data = vars(value) if isinstance(value, GenerationResult) else value
    identity = (
        data.get("provider_task_id", data.get("video_id")) if isinstance(data, dict) else None
    )
    safe_identity = (
        identity
        if (
            isinstance(identity, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,512}", identity)
            and not any(secret and secret in identity for secret in secrets)
            and not _reference_echo(identity, guard)
        )
        else None
    )
    remaining, nodes = REFERENCE_STRING_BUDGET, 128

    def reject():
        raise AgnesReferenceEcho(submitted=submitted, provider_task_id=safe_identity)

    def visit(item):
        nonlocal remaining, nodes
        nodes -= 1
        if nodes < 0:
            reject()
        if isinstance(item, str):
            if len(item) > REFERENCE_URL_LIMIT or len(item) > remaining:
                reject()
            remaining -= len(item)
            if _reference_echo(item, guard):
                reject()
        elif isinstance(item, dict):
            if len(item) > 64 or any(
                key in item for key in ("b64_json", "base64", "bytes", "dataUrl")
            ):
                reject()
            for key, nested in item.items():
                visit(key)
                visit(nested)
        elif isinstance(item, (list, tuple)):
            if len(item) > 64:
                reject()
            for nested in item:
                visit(nested)
        elif item is not None and not isinstance(item, (bool, int, float)):
            reject()

    visit(data)


def is_agnes_video(snapshot: dict, adapter: str) -> bool:
    return adapter == "canvas_openai_videos.v1" and snapshot.get("model_key") == AGNES_VIDEO


def agnes_image_body(
    prompt: str, references: list[str], count: int, parameters: CanvasImageParameters
) -> dict:
    if (
        count != 1
        or len(references) > 16
        or parameters.mask_media_id
        or parameters.transparent_background
    ):
        raise GenerationError("unsupported_parameters")
    tiers = {
        "auto": "1K",
        "low": "1K",
        "medium": "2K",
        "high": "4K",
        "1k": "1K",
        "2k": "2K",
        "4k": "4K",
    }
    if parameters.quality not in tiers:
        raise GenerationError("unsupported_parameters")
    raw = parameters.size or "1:1"
    body = {
        "model": AGNES_IMAGE,
        "prompt": prompt,
        "size": tiers[parameters.quality],
        "return_base64": True,
        "extra_body": {"response_format": "b64_json"},
    }
    if raw in IMAGE_RATIOS:
        body["ratio"] = raw
    elif raw in {"1k", "2k", "4k"}:
        body["size"] = raw.upper()
    elif re.fullmatch(r"[1-9][0-9]{0,4}x[1-9][0-9]{0,4}", raw):
        body["size"] = raw
    elif raw != "auto":
        raise GenerationError("unsupported_parameters")
    if references:
        # 运行时读取已授权原字节后替换；不能发送私有 MinIO 展示 URL。
        body["extra_body"]["image"] = references
    return body


def _agnes_video_parameters(value: dict) -> dict:
    if (
        not 4 <= value["duration"] <= 12
        or value["resolution"].lower() not in {"", "720", "720p"}
        or value["ratio"] not in {"", *VIDEO_RATIOS}
        or value["referenceVideos"]
        or len(value["referenceImages"]) > 5
        or len(value["referenceAudios"]) > 3
        or value["generate_audio"]
        or value["watermark"]
    ):
        raise GenerationError("unsupported_parameters")
    body = {
        "model": AGNES_VIDEO,
        "prompt": value["prompt"],
        "mode": "text",
        "seconds": str(value["duration"]),
        "size": "720P",
        "aspect_ratio": value["ratio"] or "16:9",
    }
    images, audios = value["referenceImages"], value["referenceAudios"]
    operation = value["operation"]
    metadata = value.get("metadata") or {}
    first, last = metadata.get("videoStartFrameNodeId"), metadata.get("videoEndFrameNodeId")
    if operation in {"reference_to_video", "audio_to_video"}:
        if (not images and not audios) or first or last:
            raise GenerationError("unsupported_video_operation")
        body["mode"] = "reference"
    elif operation == "image_to_video":
        if not images or audios or len(images) > 2:
            raise GenerationError("unsupported_video_operation")
        body["mode"] = "keyframe"
        if first or last:
            for identity in (first, last):
                if identity and sum(item.get("id") == identity for item in images) != 1:
                    raise GenerationError("invalid_reference")
            if len(images) != len({identity for identity in (first, last) if identity}):
                raise GenerationError("invalid_reference")
    elif operation != "text_to_video" or images or audios or first or last:
        raise GenerationError("unsupported_video_operation")
    return body


def agnes_reference_url(url: object) -> str:
    """纯语法检查；不声称 DNS、公网或供应商取图已验证。"""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise GenerationError("reference_media_requires_url")
    checked = validated_url(url, query=True)
    host = urlsplit(checked).hostname
    if host == "invalid" or host.endswith(".invalid"):
        raise GenerationError("reference_media_requires_url")
    return url


def validate_agnes_video_structure(value: dict, frozen_inputs: dict) -> dict:
    """只核对结构和冻结身份，不读取存储、不签名、不构造占位 URL。"""
    body = _agnes_video_parameters(value)
    for group, field in (
        ("referenceImages", "reference_media_ids"),
        ("referenceVideos", "video_reference_media_ids"),
        ("referenceAudios", "audio_reference_media_ids"),
    ):
        identifiers = []
        for item in value[group]:
            if item.get("dataUrl"):
                raise GenerationError("invalid_reference")
            local = item.get("storageKey")
            if local:
                match = re.fullmatch(r"resource:([1-9][0-9]*)", local)
                if match is None or item.get("url"):
                    raise GenerationError("invalid_reference")
                identifiers.append(match.group(1))
            else:
                agnes_reference_url(item.get("url"))
        if identifiers != frozen_inputs.get(field, []):
            raise GenerationError("reference_media_requires_url")
    return body


def agnes_video_body(value: dict) -> dict:
    body = _agnes_video_parameters(value)
    for group in ("referenceImages", "referenceAudios"):
        for item in value[group]:
            if item.get("storageKey") or item.get("dataUrl"):
                raise GenerationError("reference_media_requires_url")
            agnes_reference_url(item.get("url"))
    images, audios = value["referenceImages"], value["referenceAudios"]
    if body["mode"] == "reference":
        for field, items in (("images", images), ("audios", audios)):
            if items:
                body[field] = [item["url"] for item in items]
    elif body["mode"] == "keyframe":
        metadata = value.get("metadata") or {}
        first, last = metadata.get("videoStartFrameNodeId"), metadata.get("videoEndFrameNodeId")
        if first or last:
            for field, identity in (("first_frame", first), ("last_frame", last)):
                if identity:
                    body[field] = next(item["url"] for item in images if item.get("id") == identity)
        else:
            body["first_frame"] = images[0]["url"]
            if len(images) == 2:
                body["last_frame"] = images[1]["url"]
    return body


def agnes_video_poll_url(base: str, task_id: str) -> str:
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        raise GenerationError("invalid_provider_task")
    parts = urlsplit(validated_url(base.strip()))
    path = parts.path.rstrip("/").removesuffix("/v1") + "/agnesapi"
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path,
            urlencode({"video_id": task_id, "model_name": AGNES_VIDEO}),
            "",
        )
    )


def parse_agnes_video_result(
    body: object, adapter: str, *, submitted: bool, task_id: str | None = None
) -> GenerationResult:
    def invalid(code: str = "invalid_response") -> None:
        raise GenerationError(code, accepted_unknown=submitted, retryable=not submitted)

    if not isinstance(body, dict):
        invalid()
    identity = body.get("video_id") if submitted else task_id
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", identity):
        invalid()
    if not submitted and body.get("video_id") not in (None, identity):
        invalid()
    status = body.get("status", "pending")
    if not isinstance(status, str):
        invalid()
    status = status.strip().lower()
    if status == "failed":
        return GenerationResult(
            "failed",
            adapter,
            provider_task_id=identity,
            error={
                "code": "provider_failed",
                "message": "The provider could not complete generation.",
            },
        )
    if status == "completed":
        url = body.get("url")
        if not isinstance(url, str) or not url:
            invalid("missing_output")
        validated_url(url, query=True)
        return GenerationResult(
            "succeeded",
            adapter,
            provider_task_id=identity,
            outputs=[{"url": url, "media_type": "video"}],
        )
    if status not in {"pending", "queued", "running", "processing", "in_progress"}:
        invalid()
    return GenerationResult("submitted", adapter, provider_task_id=identity)


def agnes_queue_rejected(status: int, data: bytes) -> bool:
    """只识别已实测的无任务队列拒绝；其他 5xx 保留受理未知。"""
    if status != 503:
        return False
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError):
        return False
    return (
        isinstance(body, dict)
        and body.get("code") == "video_queue_full"
        and body.get("data") is None
        and not any(body.get(key) for key in ("id", "task_id", "video_id"))
    )
