"""仅在画布运行时将已授权的持久图片读为内联数据；不接收客户端读取器。"""

import base64
import re
import time
from collections.abc import Callable
from io import BytesIO
from typing import Any

from PIL import Image

from .types import GenerationError

# 固定源默认每图 30 MiB、16 张；总量暂沿用宿主的 100 MiB 内存预算。
# 总量与源可配置资源限额的差异由迁移验收记录管理。
CANVAS_REFERENCE_IMAGE_BYTES = 30 * 1024**2
CANVAS_REFERENCE_TOTAL_BYTES = 100 * 1024**2
CANVAS_REFERENCE_MAX_COUNT = 16

ReferenceLoader = Callable[[int, int, float], bytes]


def is_canvas_image_request(snapshot: dict, request: dict) -> bool:
    return (
        snapshot.get("service_type") == "image"
        and (request.get("source") or {}).get("scene") == "canvas_node"
        and (request.get("canvas_request") or {}).get("input", {}).get("mode") == "image"
        and (request.get("canvas_parameters") or {}).get("mode") == "image"
    )


def uses_canvas_inline_images(snapshot: dict, request: dict, adapter: str) -> bool:
    """只接入源支持内联的 Ark/Qwen；Wanx 仍要求公网 URL。"""
    return is_canvas_image_request(snapshot, request) and (
        adapter == "ark_images.v1"
        or (
            adapter == "dashscope_images.v1"
            and snapshot.get("model_key", "").startswith(("qwen-image", "wan2.6", "wan2.7"))
        )
    )


def saved_mask_bytes(
    loader: ReferenceLoader | None, deadline: float, remaining: int, source_size: tuple[int, int]
) -> bytes:
    if loader is None:
        raise GenerationError("unresolved_media_reference")
    if time.monotonic() >= deadline:
        raise GenerationError("timeout")
    limit = min(CANVAS_REFERENCE_IMAGE_BYTES, remaining)
    if limit <= 0:
        raise GenerationError("reference_images_too_large")
    data = loader(0, limit, deadline)
    if not isinstance(data, bytes):
        raise GenerationError("invalid_mask_image")
    if len(data) > limit:
        raise GenerationError("reference_images_too_large")
    try:
        with Image.open(BytesIO(data)) as mask:
            if mask.format != "PNG" or "A" not in mask.getbands():
                raise GenerationError("invalid_mask_image")
            if mask.size != source_size:
                raise GenerationError("mask_dimensions_mismatch")
            if mask.getchannel("A").getextrema()[0] != 0:
                raise GenerationError("invalid_mask_image")
            mask.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise GenerationError("invalid_mask_image") from None
    if time.monotonic() >= deadline:
        raise GenerationError("timeout")
    return data


def inline_saved_image_references(
    body: dict[str, Any],
    request: dict[str, Any],
    adapter: str,
    reference_loader: ReferenceLoader | None,
    deadline: float,
) -> None:
    inputs = request.get("input", {})
    identifiers = inputs.get("reference_media_ids", [])
    urls = inputs.get("reference_urls", [])
    if not identifiers and not urls:
        return
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or not all(
            isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) for value in identifiers
        )
        or len(identifiers) != len(urls)
        or reference_loader is None
    ):
        raise GenerationError("unresolved_media_reference")
    if len(identifiers) > CANVAS_REFERENCE_MAX_COUNT:
        raise GenerationError("reference_images_too_large")
    remaining = CANVAS_REFERENCE_TOTAL_BYTES
    values = []
    for index in range(len(identifiers)):
        if time.monotonic() >= deadline:
            raise GenerationError("timeout")
        if remaining <= 0:
            raise GenerationError("reference_images_too_large")
        limit = min(CANVAS_REFERENCE_IMAGE_BYTES, remaining)
        data = reference_loader(index, limit, deadline)
        if not isinstance(data, bytes):
            raise GenerationError("invalid_reference_image")
        if len(data) > limit:
            raise GenerationError("reference_images_too_large")
        if time.monotonic() >= deadline:
            raise GenerationError("timeout")
        try:
            with Image.open(BytesIO(data)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                    image.format
                )
                image.verify()
            if mime is None:
                raise ValueError
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise GenerationError("invalid_reference_image") from None
        values.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
        remaining -= len(data)
    if time.monotonic() >= deadline:
        raise GenerationError("timeout")
    if adapter == "ark_images.v1":
        body["image"] = values[0] if len(values) == 1 else values
    else:
        content = body["input"]["messages"][0]["content"]
        images = [item for item in content if "image" in item]
        if len(images) != len(values):
            raise GenerationError("unresolved_media_reference")
        for image, value in zip(images, values, strict=True):
            image["image"] = value
