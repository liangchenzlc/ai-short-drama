"""将文字节点已授权的图片身份、类型和大小冻结为闭合执行合同。"""

import re
from copy import deepcopy

from pydantic import ValidationError

from short_drama.ai.canvas_text_adapters import text_image_limits
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.access import scope_of
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas_text_parameters import CanvasTextReferences


def freeze_text_references(
    generation, prepared: dict, project_id: int, adapter: str, capability_cache: dict | None
) -> None:
    canvas = prepared["canvas_request"]
    references = canvas["input"].get("referenceImages") or []
    if canvas["input"]["mode"] != "text" or not references:
        return
    if adapter != "openai_chat.v1" or canvas["operation"] != "text":
        raise WorkflowError(
            "canvas_generation_option_unsupported", "该文字协议尚未支持图片引用", 422
        )
    capability = (capability_cache or {}).get("canvas_text_capability")
    try:
        count, per_image, prompt_chars = text_image_limits(capability)
    except GenerationError:
        raise WorkflowError(
            "canvas_text_capability_invalid", "已保存的文字模型图片能力配置无效", 422
        ) from None
    if len(references) > count:
        raise WorkflowError(
            "reference_limit_exceeded", f"当前文字模型最多支持 {count} 张参考图片", 422
        )
    if len(canvas["prompt"]) > prompt_chars:
        raise WorkflowError("canvas_text_prompt_too_long", "文字提示词超过已保存模型的上限", 422)
    frozen, normalized = [], deepcopy(references)
    for reference in normalized:
        match = re.fullmatch(r"resource:([1-9][0-9]*)", reference.get("storageKey") or "")
        if match is None:
            raise WorkflowError(
                "canvas_reference_not_saved", "参考图片必须先保存为当前项目的稳定资源", 422
            )
        identifier = match.group(1)
        try:
            parse_identifier(identifier)
        except ValueError:
            raise WorkflowError("canvas_reference_not_saved", "参考图片资源身份无效", 422) from None
        media = generation._validate_media(identifier, "image")
        if scope_of(generation.session, media) != (None, project_id):
            raise NotFound("参考图片不存在或不属于当前项目")
        if not media.storage_locator.startswith("minio://"):
            raise WorkflowError("canvas_reference_not_saved", "参考图片必须是永久资源", 422)
        if type(media.byte_size) is not int or not 0 < media.byte_size <= per_image:
            raise WorkflowError(
                "reference_images_too_large", "参考图片超过已保存模型的大小上限", 422
            )
        frozen.append(
            {
                "media_id": identifier,
                "mime_type": media.format_code,
                "byte_size": media.byte_size,
            }
        )
        for field in ("url", "dataUrl", "bytes", "width", "height", "durationMs"):
            reference.pop(field, None)
        reference.update(type=media.format_code, bytes=media.byte_size)
        for field, value in (("width", media.width), ("height", media.height)):
            if value is not None:
                reference[field] = value
    try:
        envelope = CanvasTextReferences(mode="text", references=frozen)
    except ValidationError:
        raise WorkflowError(
            "invalid_reference_image", "文字引用需要 PNG、JPEG、WebP 或 GIF 图片资源", 422
        ) from None
    prepared["canvas_text_references"] = envelope.model_dump(mode="json")
    prepared["input"]["reference_media_ids"] = [item["media_id"] for item in frozen]
    prepared["canvas_request"]["input"]["referenceImages"] = normalized
