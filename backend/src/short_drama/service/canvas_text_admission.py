"""将文字节点已授权的媒体身份及实际元信息冻结为闭合执行合同。"""

import re
from copy import deepcopy

from pydantic import ValidationError

from short_drama.ai.canvas_text_adapters import (
    TEXT_IMAGE_ADAPTERS,
    TEXT_VIDEO_ADAPTERS,
    text_image_limits,
    text_video_limits,
)
from short_drama.ai.canvas_tool_adapters import CANVAS_TOOL_ADAPTERS
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.access import scope_of
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas_text_parameters import CanvasTextReferences
from short_drama.schemas.canvas_tool_contracts import CanvasToolReferences


def freeze_text_references(
    generation, prepared: dict, project_id: int, adapter: str, capability_cache: dict | None
) -> None:
    canvas = prepared["canvas_request"]
    references = canvas["input"].get("referenceImages") or []
    videos = canvas["input"].get("referenceVideos") or []
    tool = bool(canvas["input"].get("agentRequests"))
    if canvas["input"]["mode"] != "text" or (not tool and not (references or videos)):
        return
    if (
        adapter not in (CANVAS_TOOL_ADAPTERS if tool else TEXT_IMAGE_ADAPTERS)
        or videos
        and adapter not in TEXT_VIDEO_ADAPTERS
        or canvas["operation"] != "text"
    ):
        raise WorkflowError(
            "canvas_generation_option_unsupported", "该文字协议尚未支持媒体引用", 422
        )
    capability = (capability_cache or {}).get("canvas_text_capability")
    try:
        count, per_image, prompt_chars = text_image_limits(capability)
        video_count, per_video = text_video_limits(capability)
    except GenerationError:
        raise WorkflowError(
            "canvas_text_capability_invalid", "已保存的文字模型媒体能力配置无效", 422
        ) from None
    if len(references) > count:
        raise WorkflowError(
            "reference_limit_exceeded", f"当前文字模型最多支持 {count} 张参考图片", 422
        )
    if len(videos) > video_count:
        raise WorkflowError(
            "reference_limit_exceeded", f"当前文字模型最多支持 {video_count} 个参考视频", 422
        )
    if len(canvas["prompt"]) > prompt_chars:
        raise WorkflowError("canvas_text_prompt_too_long", "文字提示词超过已保存模型的上限", 422)
    normalized = {
        "referenceImages": deepcopy(references),
        "referenceVideos": deepcopy(videos),
    }
    frozen = {"image": [], "video": []}
    for kind, field, limit in (
        ("image", "referenceImages", per_image),
        ("video", "referenceVideos", per_video),
    ):
        label = "图片" if kind == "image" else "视频"
        for reference in normalized[field]:
            match = re.fullmatch(r"resource:([1-9][0-9]*)", reference.get("storageKey") or "")
            if match is None:
                raise WorkflowError(
                    "canvas_reference_not_saved", f"参考{label}必须先保存为当前项目的稳定资源", 422
                )
            identifier = match.group(1)
            try:
                parse_identifier(identifier)
            except ValueError:
                raise WorkflowError(
                    "canvas_reference_not_saved", f"参考{label}资源身份无效", 422
                ) from None
            media = generation._validate_media(identifier, kind)
            if scope_of(generation.session, media) != (None, project_id):
                raise NotFound(f"参考{label}不存在或不属于当前项目")
            if not media.storage_locator.startswith("minio://"):
                raise WorkflowError("canvas_reference_not_saved", f"参考{label}必须是永久资源", 422)
            if type(media.byte_size) is not int or not 0 < media.byte_size <= limit:
                raise WorkflowError(
                    "reference_images_too_large"
                    if kind == "image"
                    else "reference_media_too_large",
                    f"参考{label}超过已保存模型的大小上限",
                    422,
                )
            item = {
                "media_id": identifier,
                "mime_type": media.format_code,
                "byte_size": media.byte_size,
            }
            if kind == "video":
                item["duration_ms"] = media.duration_ms
            frozen[kind].append(item)
            for name in ("url", "dataUrl", "bytes", "width", "height", "durationMs"):
                reference.pop(name, None)
            reference.update(type=media.format_code, bytes=media.byte_size)
            for name, value in (("width", media.width), ("height", media.height)):
                if value is not None:
                    reference[name] = value
            if kind == "video":
                reference["durationMs"] = media.duration_ms
    try:
        envelope = (
            CanvasToolReferences(mode="text", references=frozen["image"])
            if tool and not videos
            else CanvasTextReferences(
                mode="text", references=frozen["image"], videos=frozen["video"] or None
            )
        )
    except ValidationError:
        raise WorkflowError(
            "invalid_reference_media" if videos else "invalid_reference_image",
            "文字引用需要有效图片或具有真实时长的 MP4、WebM、MOV 视频"
            if videos
            else "文字引用需要 PNG、JPEG、WebP 或 GIF 图片资源",
            422,
        ) from None
    prepared["canvas_text_references"] = envelope.model_dump(mode="json", exclude_none=True)
    prepared["input"]["reference_media_ids"] = [item["media_id"] for item in frozen["image"]]
    if videos:
        prepared["input"]["video_reference_media_ids"] = [
            item["media_id"] for item in frozen["video"]
        ]
    for field, value in normalized.items():
        if field in canvas["input"] or value:
            canvas["input"][field] = value
