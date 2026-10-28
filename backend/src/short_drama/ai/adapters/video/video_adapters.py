"""视频提交协议构造；能力与结果处理按职责分离。"""

import math

from short_drama.ai.adapters.image.agnes_adapters import (
    agnes_video_body,
    is_agnes_video,
)
from short_drama.ai.adapters.runninghub.workflow import (
    RUNNINGHUB_WORKFLOW,
    workflow_body,
)
from short_drama.ai.adapters.video.config import API_PREFIXES as API_PREFIXES
from short_drama.ai.adapters.video.config import (
    NEWAPI_VIDEO_GENERATIONS,
    OPENAI_VIDEOS,
    REFERENCE_GROUPS,
    _invalid,
    _normalize_video_resolution,
    _resolution_name,
    canvas_video_endpoint,
    is_seedance_25,
)
from short_drama.ai.adapters.video.config import SAFE_OPERATIONS as SAFE_OPERATIONS
from short_drama.ai.adapters.video.config import VIDEO_ADAPTERS as VIDEO_ADAPTERS
from short_drama.ai.adapters.video.config import _boolean as _boolean
from short_drama.ai.adapters.video.config import _duration_value as _duration_value
from short_drama.ai.adapters.video.config import _ratio_allowed as _ratio_allowed
from short_drama.ai.adapters.video.config import is_seedance_2 as is_seedance_2
from short_drama.ai.adapters.video.config import is_seedance_model as is_seedance_model
from short_drama.ai.adapters.video.config import (
    normalize_canvas_video_config as normalize_canvas_video_config,
)
from short_drama.ai.adapters.video.inputs import _profile as _profile
from short_drama.ai.adapters.video.inputs import frozen_video_input as frozen_video_input
from short_drama.ai.adapters.video.inputs import (
    validate_agnes_canvas_video_request as validate_agnes_canvas_video_request,
)
from short_drama.ai.adapters.video.inputs import validate_canvas_video_input
from short_drama.ai.adapters.video.results import _first as _first
from short_drama.ai.adapters.video.results import _path as _path
from short_drama.ai.adapters.video.results import (
    canvas_video_poll_endpoint as canvas_video_poll_endpoint,
)
from short_drama.ai.adapters.video.results import (
    canvas_video_resolved_parameters as canvas_video_resolved_parameters,
)
from short_drama.ai.adapters.video.results import complete_video_urls as complete_video_urls
from short_drama.ai.adapters.video.results import (
    parse_canvas_video_result as parse_canvas_video_result,
)


def _supports(snapshot: dict, name: str) -> bool:
    profile = snapshot.get("canvas_video_capability")
    if not profile:
        return True
    option = profile.get(name) or {}
    if not isinstance(option, dict) or type(option.get("supported", False)) is not bool:
        _invalid()
    return option.get("supported", False)


def image_roles(value: dict, model: str) -> list[str]:
    metadata = value.get("metadata") or {}
    start, end = metadata.get("videoStartFrameNodeId"), metadata.get("videoEndFrameNodeId")
    result = []
    for index, item in enumerate(value["referenceImages"]):
        role = "reference_image"
        if value["operation"] != "reference_to_video":
            if start and item.get("id") == start:
                role = "first_frame"
            elif end and item.get("id") == end:
                role = "last_frame"
            elif (
                is_seedance_25(model)
                and not start
                and not end
                and not value["referenceVideos"]
                and not value["referenceAudios"]
            ):
                if index == 0:
                    role = "first_frame"
                elif index == 1 and len(value["referenceImages"]) == 2:
                    role = "last_frame"
        result.append(role)
    return result


def _media_value(item: dict) -> str:
    return item.get("storageKey") or item.get("dataUrl") or item.get("url") or ""


def build_canvas_video_submission(
    snapshot: dict, request: dict, adapter: str
) -> tuple[str, dict, dict]:
    value = validate_canvas_video_input(snapshot, request, adapter)
    model = snapshot["model_key"]
    if is_agnes_video(snapshot, adapter):
        return (
            canvas_video_endpoint(snapshot["base_url"], "/v1/videos"),
            {},
            agnes_video_body(value),
        )
    if adapter == RUNNINGHUB_WORKFLOW:
        return (
            canvas_video_endpoint(snapshot["base_url"], "/task/openapi/create"),
            {},
            workflow_body(value, model),
        )
    body = {"model": model, "prompt": value["prompt"]}
    if adapter == OPENAI_VIDEOS:
        body["seconds"] = str(value["duration"])
        if value["ratio"]:
            body["size"] = value["ratio"]
        if value["resolution"]:
            body["resolution_name"] = value["resolution"]
        variants = snapshot.get("canvas_video_variants")
        if variants not in (None, "", False, 0):
            if type(variants) not in {str, int, float, bool} or (
                type(variants) is float and not math.isfinite(variants)
            ):
                _invalid()
            body["variants"] = variants
        if value["referenceImages"]:
            body["input_reference"] = value["referenceImages"][0]
        path = "/v1/videos"
    elif adapter == NEWAPI_VIDEO_GENERATIONS:
        if any(
            item.get("storageKey")
            or item.get("dataUrl")
            or item.get("url", "").startswith("asset://")
            for _, group in REFERENCE_GROUPS
            for item in value[group]
        ):
            _invalid("reference_media_requires_url")
        body.update(
            seconds=str(value["duration"]),
            aspect_ratio=value["ratio"] or "16:9",
            generate_audio=value["generate_audio"],
        )
        if value["resolution"]:
            body["resolution"] = value["resolution"]
        for kind, group in REFERENCE_GROUPS:
            if value[group]:
                body[f"{kind}_urls"] = [_media_value(item) for item in value[group]]
        path = "/v1/video/generations"
    else:
        body["prompt"] = value["prompt"].strip()
        value["resolution"] = _resolution_name(
            snapshot.get("canvas_video_capability") or {}, value["resolution"]
        ) or _normalize_video_resolution(value["resolution"])
        roles = image_roles(value, model)
        reference_mode = (
            value["operation"] == "reference_to_video"
            or len(value["referenceImages"]) > 1
            or bool(value["referenceVideos"] or value["referenceAudios"])
            or (is_seedance_25(model) and bool(value["referenceImages"]))
        )
        if reference_mode:
            ratio, seconds = value["ratio"], value["duration"]
            if is_seedance_25(model) and (
                any(role in {"first_frame", "last_frame"} for role in roles)
                or (
                    value["referenceVideos"]
                    and value["operation"]
                    in {"extend", "inpaint", "replace_element", "style_transfer"}
                )
            ):
                ratio = "adaptive"
            if (
                is_seedance_25(model)
                and value["referenceVideos"]
                and value["operation"] in {"inpaint", "replace_element", "style_transfer"}
            ):
                seconds = -1
            content = []
            for item, role in zip(value["referenceImages"], roles, strict=True):
                content.append(
                    {"type": "image_url", "image_url": {"url": _media_value(item)}, "role": role}
                )
            for kind, group in REFERENCE_GROUPS[1:]:
                for item in value[group]:
                    content.append(
                        {
                            "type": f"{kind}_url",
                            f"{kind}_url": {"url": _media_value(item)},
                            "role": f"reference_{kind}",
                        }
                    )
            metadata = {"ratio": ratio}
            if _supports(snapshot, "generateAudio"):
                metadata["generate_audio"] = value["generate_audio"]
            task_type = {
                "reference_to_video": "reference",
                "extend": "extend",
                "inpaint": "edit",
                "replace_element": "edit",
                "style_transfer": "edit",
            }.get(value["operation"])
            if (
                is_seedance_25(model)
                and task_type
                and (task_type == "reference" or value["referenceVideos"])
            ):
                metadata["omni_reference_task_type"] = task_type
            if _supports(snapshot, "watermark"):
                metadata["watermark"] = value["watermark"]
            body.update(
                seconds=str(seconds),
                resolution=value["resolution"],
                content=content,
                metadata=metadata,
            )
        else:
            body.update(duration=value["duration"], resolution=value["resolution"])
            if _supports(snapshot, "generateAudio"):
                body["generate_audio"] = value["generate_audio"]
            if value["referenceImages"]:
                body["image"] = {"url": _media_value(value["referenceImages"][0])}
            else:
                body["aspect_ratio"] = value["ratio"]
        path = "/videos"
    return canvas_video_endpoint(snapshot["base_url"], path), {}, body
