import copy

from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.adapters.image.image_operations import (
    operation_vision_supported,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
)
from short_drama.ai.adapters.submission import _openai_image_references, build_submission
from short_drama.ai.adapters.text.chat_protocols import (
    is_compatible_chat,
)
from short_drama.ai.adapters.text.text_adapters import is_canvas_text_request
from short_drama.ai.adapters.tools.submission import (
    canvas_tool_resolved_parameters,
    is_canvas_tool_request,
)
from short_drama.ai.adapters.video.input_capabilities import video_input_capabilities
from short_drama.ai.adapters.video.video_adapters import (
    VIDEO_ADAPTERS,
    canvas_video_resolved_parameters,
)
from short_drama.ai.gateway.endpoints import endpoint as endpoint
from short_drama.ai.types import GenerationError


def resolved_parameters(body):
    from short_drama.ai.adapters.image.agnes_adapters import AGNES_IMAGE

    if body.get("model") == AGNES_IMAGE and "extra_body" in body:
        return {key: body[key] for key in ("size", "ratio", "return_base64") if key in body}
    if isinstance(body.get("input"), dict) and "text" in body["input"] and "voice" in body["input"]:
        return {k: v for k, v in body["input"].items() if k != "text"}
    if "parameters" in body:
        return body["parameters"]
    return {
        k: v
        for k, v in body.items()
        if not k.startswith(("image_file_", "audio_file_"))
        and k
        not in {
            "model",
            "prompt",
            "messages",
            "input",
            "content",
            "image",
            "mask",
        }
    }


def validate_request(snapshot, request_data, adapter=None):
    """Pure admission validation; placeholders are never submitted to a provider."""
    adapter = adapter or select_adapter(snapshot)
    from short_drama.ai.adapters.image.agnes_adapters import is_agnes_video
    from short_drama.ai.adapters.video.video_adapters import validate_agnes_canvas_video_request

    if is_agnes_video(snapshot, adapter):
        body = validate_agnes_canvas_video_request(snapshot, request_data, adapter)
        return {"adapter": adapter, "resolved_parameters": canvas_video_resolved_parameters(body)}
    request = copy.deepcopy(request_data)
    inputs = request.setdefault("input", {})
    if inputs.get("reference_media_ids") and "reference_urls" not in inputs:
        inputs["reference_urls"] = [
            f"https://reference.invalid/{i}" for i, _ in enumerate(inputs["reference_media_ids"])
        ]
    if inputs.get("audio_reference_media_ids") and "audio_reference_urls" not in inputs:
        inputs["audio_reference_urls"] = [
            f"https://reference.invalid/audio/{i}"
            for i, _ in enumerate(inputs["audio_reference_media_ids"])
        ]
    if (
        is_canvas_text_request(snapshot, request)
        and inputs.get("video_reference_media_ids")
        and "video_reference_urls" not in inputs
    ):
        inputs["video_reference_urls"] = [
            f"https://reference.invalid/video/{i}"
            for i, _ in enumerate(inputs["video_reference_media_ids"])
        ]
    for name in ("first", "last"):
        if inputs.get(f"{name}_frame_media_id") and not inputs.get(f"{name}_frame_url"):
            inputs[f"{name}_frame_url"] = f"https://reference.invalid/{name}"
    _, _, body = build_submission(snapshot, request, adapter)
    if is_canvas_tool_request(request):
        return {"adapter": adapter, "resolved_parameters": canvas_tool_resolved_parameters(body)}
    if adapter == "openai_chat.v1" and is_compatible_chat(snapshot):
        return {"adapter": adapter, "resolved_parameters": canvas_tool_resolved_parameters(body)}
    if adapter in RUNNINGHUB_ADAPTERS:
        return {"adapter": adapter, "resolved_parameters": copy.deepcopy(body)}
    if adapter in VIDEO_ADAPTERS:
        return {"adapter": adapter, "resolved_parameters": canvas_video_resolved_parameters(body)}
    return {"adapter": adapter, "resolved_parameters": resolved_parameters(body)}


def capabilities(snapshot):
    """Local protocol support, not proof of account/model entitlement or a paid probe."""
    empty = {
        "known": False,
        "parameters": [],
        "reference_images": False,
        "first_frame": False,
        "last_frame": False,
        "mask": False,
        "vision": False,
    }
    try:
        adapter = select_adapter(snapshot)
    except (GenerationError, KeyError):
        return empty
    kind = snapshot.get("service_type")
    model = snapshot.get("model_key", "")
    edit = model.startswith("qwen-image-edit") or model == "wan2.6-image"
    return {
        "known": True,
        "mask": adapter == "openai_images.v1" and _openai_image_references(model),
        "vision": kind == "text" and operation_vision_supported(snapshot, adapter),
        "parameters": {
            "audio": ["voice"],
            "text": ["temperature", "max_output_tokens"],
            "image": ["aspect", "resolution", "count"],
            "video": ["aspect", "resolution", "duration_ms"],
        }[kind],
        "reference_images": adapter == "ark_images.v1"
        or (adapter == "modelhub_video.v1" and model in {"seedance-2.0-mini", "minimax-h3"})
        or (adapter == "dashscope_images.v1" and edit)
        or (adapter == "openai_images.v1" and _openai_image_references(model)),
        "first_frame": kind == "video",
        "last_frame": adapter == "ark_video.v1"
        or (adapter == "modelhub_video.v1" and model in {"seedance-2.0-mini", "minimax-h3"})
        or (adapter == "dashscope_video.v1" and "kf2v" in snapshot.get("model_key", "")),
        **({"video_input": video_input_capabilities(snapshot, adapter)} if kind == "video" else {}),
    }
