"""将画布参数映射到已实现的供应商合同，未支持的操作明确拒绝。"""

import re

from pydantic import ValidationError

from short_drama.ai.adapters.runninghub.adapters import RUNNINGHUB_ADAPTERS
from short_drama.ai.adapters.text.text_adapters import TEXT_IMAGE_ADAPTERS, TEXT_VIDEO_ADAPTERS
from short_drama.ai.adapters.text.text_thinking import thinking_parameters
from short_drama.ai.adapters.tools.adapters import CANVAS_TOOL_ADAPTERS
from short_drama.ai.adapters.video.video_adapters import normalize_canvas_video_config
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import GenerationRequestError, WorkflowError
from short_drama.schemas.canvas.generation.tasks.task_runtime import CanvasRuntimeTaskCreate
from short_drama.schemas.generations.admission.generation import GENERATION_SCHEMAS
from short_drama.service.canvas.generation.generation_parameters import (
    boolean,
    frozen_canvas_parameters,
    number,
    unsupported,
)
from short_drama.service.canvas.generation.video.video_admission import VIDEO_ADAPTERS

OPTIONS = frozenset(
    "size quality transparentBackground count videoSeconds vquality videoGenerateAudio "
    "videoWatermark videoArkPrivateAssetUpload audioVoice audioFormat audioSpeed audioPitch "
    "audioVolume audioInstructions systemPrompt".split()
)
VIDEO_OPTIONS = (
    "size",
    "videoSeconds",
    "vquality",
    "videoGenerateAudio",
    "videoWatermark",
)


def normalize_video_request(
    request: CanvasRuntimeTaskCreate, capability_cache: dict | None = None
) -> CanvasRuntimeTaskCreate:
    """用服务端模型能力冻结源选项；原正文仍用于幂等身份。"""
    capability = capability_cache or {}
    snapshot = {
        key: capability[key]
        for key in ("canvas_channel_key", "canvas_video_capability")
        if key in capability
    }
    try:
        config = normalize_canvas_video_config(
            snapshot, {"canvas_request": request.model_dump(mode="json", by_alias=True)}
        )
    except GenerationError as error:
        raise GenerationRequestError(error.code) from None
    normalized = request.model_copy(deep=True)
    normalized.input.config = config
    options = {}
    for option in VIDEO_OPTIONS:
        value = config.get(option)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if option in {"videoGenerateAudio", "videoWatermark"}:
            options[option] = boolean(value, option)
        elif option == "videoSeconds":
            seconds = number(value, option, 0)
            options[option] = int(seconds) if seconds.is_integer() else seconds
        else:
            options[option] = value
    normalized.input.capability_options = options
    return normalized


def _video_input(request: CanvasRuntimeTaskCreate, references: list[str], adapter: str) -> dict:
    inputs = {"prompt": request.prompt, "reference_media_ids": []}
    if request.operation == "reference_to_video":
        if adapter != "modelhub_video.v1":
            unsupported("reference_to_video")
        if not references:
            unsupported("reference_to_video referenceImages")
        inputs["reference_media_ids"] = references
        return inputs
    if request.operation == "text_to_video":
        if references:
            unsupported("text_to_video referenceImages")
        return inputs
    if not references or adapter not in {"ark_video.v1", "dashscope_video.v1", "modelhub_video.v1"}:
        unsupported("image_to_video referenceImages")
    metadata = request.input.metadata.model_dump(mode="json", by_alias=True)
    start, end = metadata.get("videoStartFrameNodeId"), metadata.get("videoEndFrameNodeId")
    if start or end:
        selected = []
        for role, node_id in (("first", start), ("last", end)):
            if not node_id:
                continue
            matches = [
                index
                for index, reference in enumerate(request.input.reference_images)
                if reference.id == node_id
            ]
            if len(matches) != 1 or matches[0] in selected:
                unsupported(f"video{role.title()}FrameNodeId")
            selected.append(matches[0])
            inputs[f"{role}_frame_media_id"] = references[matches[0]]
        if "first_frame_media_id" not in inputs or len(selected) != len(references):
            unsupported("video frame/referenceImages roles")
    else:
        if len(references) > 2:
            unsupported("image_to_video referenceImages")
        inputs["first_frame_media_id"] = references[0]
        if len(references) == 2:
            inputs["last_frame_media_id"] = references[1]
    return inputs


def generation_payload(
    request: CanvasRuntimeTaskCreate,
    project_id: int,
    adapter: str | None = None,
    capability_cache: dict | None = None,
) -> dict:
    tool = request.input.agent_requests is not None
    if tool and adapter not in CANVAS_TOOL_ADAPTERS:
        unsupported("editor tool protocol")
    config = request.input.config
    for key in config:
        if key not in OPTIONS:
            unsupported(key)
    if adapter in RUNNINGHUB_ADAPTERS:
        if (
            request.provider not in {None, "runninghub"}
            or request.input.capability_options
            or request.input.text_history
            or request.input.agent_requests
            or request.input.text_options.thinking
        ):
            unsupported("workflow execution scope")
        return (
            GENERATION_SCHEMAS[request.input.mode]
            .model_validate(
                {
                    "project_id": str(project_id),
                    "config_id": request.logical_model_id,
                    "input": {
                        "text" if request.input.mode == "audio" else "prompt": request.prompt
                    },
                    **({"parameters": {"voice": "alloy"}} if request.input.mode == "audio" else {}),
                }
            )
            .model_dump(mode="json", exclude_none=True)
        )
    if request.input.execution is not None:
        unsupported("workflow protocol")
    if request.input.mode == "video" and adapter in VIDEO_ADAPTERS:
        if (
            request.provider
            or request.input.mask is not None
            or request.input.text_options.thinking
        ):
            unsupported("provider/mask/thinking")
        request = normalize_video_request(request)
        frozen_canvas_parameters(request, adapter)
        # The trusted transform freezes canvas references after standard preparation.
        # This placeholder keeps the public standard VideoInput contract unchanged.
        return (
            GENERATION_SCHEMAS["video"]
            .model_validate(
                {
                    "project_id": str(project_id),
                    "config_id": request.logical_model_id,
                    "input": {"prompt": request.prompt},
                }
            )
            .model_dump(mode="json", exclude_none=True)
        )
    if request.provider or request.input.capability_options:
        unsupported("provider/capabilityOptions")
    if request.input.mask is not None and (
        request.input.mode != "image" or adapter != "openai_images.v1"
    ):
        unsupported("mask")
    if request.input.reference_audios:
        unsupported("referenceAudios")
    if request.input.reference_videos and (
        request.input.mode != "text" or adapter not in TEXT_VIDEO_ADAPTERS
    ):
        unsupported("referenceVideos")
    if request.input.text_options.thinking:
        if request.input.mode != "text":
            unsupported("thinking")
        try:
            thinking_parameters(
                adapter, True, (capability_cache or {}).get("canvas_text_capability")
            )
        except GenerationError:
            unsupported("thinking capability")
    kind = request.input.mode
    references = []
    for reference in request.input.reference_images:
        match = re.fullmatch(r"resource:([1-9][0-9]*)", reference.storage_key or "")
        if match is None:
            raise WorkflowError(
                "canvas_reference_not_saved", "参考图片必须先保存为当前项目的稳定资源", 422
            )
        references.append(match.group(1))
    if (
        request.operation
        not in {
            "text": {"text", "storyboard"},
            "image": {"image"},
            "video": {"text_to_video", "image_to_video"}
            | ({"reference_to_video"} if adapter is not None else set()),
            "audio": {"audio"},
        }[kind]
    ):
        unsupported(request.operation)
    parameters = {}
    if kind == "text":
        if references and adapter not in (CANVAS_TOOL_ADAPTERS if tool else TEXT_IMAGE_ADAPTERS):
            unsupported("text referenceImages")
        messages = [item.model_dump(mode="json") for item in request.input.text_history]
        system = config.get("systemPrompt")
        if system and not tool:
            if not isinstance(system, str):
                unsupported("systemPrompt")
            messages.insert(0, {"role": "system", "content": system})
        if request.operation == "storyboard":
            from short_drama.service.canvas.document.storyboard.storyboard import (
                storyboard_system_prompt,
            )

            messages.append(
                {
                    "role": "system",
                    "content": storyboard_system_prompt(
                        request.model_dump(mode="json", by_alias=True, exclude_none=True)
                    ),
                }
            )
        messages.append({"role": "user", "content": request.prompt})
        inputs = {"messages": messages}
    elif kind == "image":
        if adapter is None:
            if config.get("transparentBackground") not in {None, "false", False}:
                unsupported("transparentBackground")
        else:
            frozen_canvas_parameters(request, adapter)
        count = config.get("count", "1")
        if isinstance(count, str) and count.isdecimal():
            count = int(count)
        parameters["count"] = count
        if adapter is None and config.get("size") not in {None, "auto", ""}:
            parameters["aspect"] = config["size"]
        if adapter is None and config.get("quality") not in {None, "auto", ""}:
            parameters["resolution"] = config["quality"]
        inputs = {"prompt": request.prompt, "reference_media_ids": references}
    elif kind == "video":
        if adapter is None:
            for option in ("videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload"):
                if config.get(option) not in {None, "false", False}:
                    unsupported(option)
        else:
            frozen_canvas_parameters(request, adapter)
        seconds = config.get("videoSeconds")
        if seconds is not None:
            if not isinstance(seconds, str) or not seconds.isdecimal():
                unsupported("videoSeconds")
            parameters["duration_ms"] = int(seconds) * 1000
        if config.get("size") not in {None, "auto", ""}:
            parameters["aspect"] = config["size"]
        if config.get("vquality"):
            resolution = str(config["vquality"])
            parameters["resolution"] = resolution + "p" if resolution.isdecimal() else resolution
        inputs = (
            {"prompt": request.prompt, "reference_media_ids": references}
            if adapter is None
            else _video_input(request, references, adapter)
        )
    else:
        if references:
            unsupported("audio referenceImages")
        if adapter is None:
            for option, neutral in (
                ("audioFormat", "mp3"),
                ("audioSpeed", "1"),
                ("audioPitch", "0"),
                ("audioVolume", "1"),
                ("audioInstructions", ""),
            ):
                if config.get(option, neutral) != neutral:
                    unsupported(option)
        else:
            frozen_canvas_parameters(request, adapter)
        voice = config.get("audioVoice", "alloy")
        if adapter in {"openai_speech.v1", "canvas_async_audio.v1"} and isinstance(voice, str):
            voice = voice.strip() or "alloy"
        parameters["voice"] = voice
        inputs = {"text": request.prompt}
    try:
        return (
            GENERATION_SCHEMAS[kind]
            .model_validate(
                {
                    "project_id": str(project_id),
                    "config_id": request.logical_model_id,
                    "input": inputs,
                    "parameters": parameters,
                }
            )
            .model_dump(mode="json", exclude_none=True)
        )
    except ValidationError:
        unsupported("generation parameters/input")
