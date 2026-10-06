"""按源选项冻结画布媒体参数；不接收客户端凭据或任意供应商 JSON。"""

import math
import re

from pydantic import ValidationError

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_generation_parameters import (
    CanvasAudioParameters,
    CanvasImageParameters,
    CanvasVideoParameters,
)
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate


def unsupported(option: str) -> None:
    raise WorkflowError(
        "canvas_generation_option_unsupported",
        f"当前生成协议尚未支持画布参数 {option}，未发送模型请求",
        422,
    ) from None


def number(value: str | bool | int | float | None, option: str, default: float) -> float:
    if value is None or value == "":
        return default
    if type(value) not in {str, int, float}:
        unsupported(option)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        unsupported(option)
    if not math.isfinite(result):
        unsupported(option)
    return result


def boolean(value: str | bool | int | float | None, option: str) -> bool | None:
    if value is None or value == "":
        return None
    if type(value) is bool:
        return value
    if type(value) is str and value in {"true", "false"}:
        return value == "true"
    unsupported(option)


def mask_media_id(request: CanvasRuntimeTaskCreate, adapter: str | None) -> str | None:
    mask = request.input.mask
    if mask is None:
        return None
    if request.input.mode != "image" or adapter != "openai_images.v1":
        unsupported("mask")
    if not request.input.reference_images:
        unsupported("mask referenceImages")
    match = re.fullmatch(r"resource:([1-9][0-9]*)", mask.storage_key or "")
    if match is None:
        raise WorkflowError("canvas_reference_not_saved", "蒙版必须先保存为稳定图片资源", 422)
    return match.group(1)


def frozen_canvas_parameters(
    request: CanvasRuntimeTaskCreate, adapter: str | None = None
) -> dict | None:
    """准入 transform 在设置 canvas_node 来源后保存此闭合 envelope。"""
    config = request.input.config
    if request.input.mode == "image":
        if adapter not in {None, "openai_images.v1", "ark_images.v1", "dashscope_images.v1"}:
            unsupported("image options")
        values = {"mode": "image"}
        for field, default in (("size", "1:1"), ("quality", "auto")):
            raw = config.get(field)
            if raw is not None and not isinstance(raw, str):
                unsupported(field)
            values[field] = raw.strip().lower() if raw else default
            if not values[field]:
                values[field] = default
        transparent = boolean(config.get("transparentBackground"), "transparentBackground") is True
        if transparent and adapter != "openai_images.v1":
            unsupported("transparentBackground")
        values["transparent_background"] = transparent
        values["mask_media_id"] = mask_media_id(request, adapter)
        try:
            return CanvasImageParameters.model_validate(values).model_dump(
                mode="json", exclude_none=True
            )
        except ValidationError as error:
            unsupported(str(error.errors()[0]["loc"][0]))
    if request.input.mode == "audio":
        if adapter not in {None, "openai_speech.v1"}:
            unsupported("audioFormat/audioSpeed/audioInstructions")
        if number(config.get("audioPitch"), "audioPitch", 0) != 0:
            unsupported("audioPitch")
        if number(config.get("audioVolume"), "audioVolume", 1) != 1:
            unsupported("audioVolume")
        values = {
            "mode": "audio",
            "format": "mp3" if config.get("audioFormat") in {None, ""} else config["audioFormat"],
            "speed": number(config.get("audioSpeed"), "audioSpeed", 1),
            "instructions": ""
            if config.get("audioInstructions") is None
            else config["audioInstructions"],
        }
        try:
            return CanvasAudioParameters.model_validate(values).model_dump(mode="json")
        except ValidationError as error:
            option = {
                "format": "audioFormat",
                "speed": "audioSpeed",
                "instructions": "audioInstructions",
            }
            unsupported(option.get(error.errors()[0]["loc"][0], "audio options"))
    if request.input.mode == "video":
        from .canvas_video_admission import VIDEO_ADAPTERS

        if (
            boolean(config.get("videoArkPrivateAssetUpload"), "videoArkPrivateAssetUpload") is True
            and adapter not in VIDEO_ADAPTERS
        ):
            unsupported("videoArkPrivateAssetUpload")
        generate_audio = boolean(config.get("videoGenerateAudio"), "videoGenerateAudio")
        watermark = boolean(config.get("videoWatermark"), "videoWatermark")

        if (
            generate_audio is not None
            and adapter not in {"ark_video.v1", "modelhub_video.v1"} | VIDEO_ADAPTERS
        ):
            unsupported("videoGenerateAudio")
        if watermark is not None and adapter != "ark_video.v1" and adapter not in VIDEO_ADAPTERS:
            unsupported("videoWatermark")
        return CanvasVideoParameters(
            mode="video", generate_audio=generate_audio, watermark=watermark
        ).model_dump(mode="json", exclude_none=True)
    return None
