"""Official MiniMax H3 V2 task protocol, verified against its published OpenAPI."""

import re
from urllib.parse import quote

from short_drama.ai.adapters.image.common import invalid_response, unsupported
from short_drama.ai.adapters.video.config import canvas_video_endpoint
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.official_models import protocol_models
from short_drama.ai.types import GenerationError, GenerationResult

MINIMAX_VIDEO = "minimax_video.v2"
MINIMAX_MODELS = protocol_models("minimax-video")


def minimax_capabilities(model: str) -> dict:
    spec = MINIMAX_MODELS.get(model)
    supported = spec is not None
    return {
        "first_frame": supported,
        "reference_images": supported,
        "duration_seconds": list(range(spec["duration_min"], spec["duration_max"] + 1))
        if spec
        else [],
        "resolutions": list(spec["resolutions"]) if spec else [],
        "audio_references": False,
        "generate_audio": False,
    }


def build_minimax_submission(snapshot: dict, request: dict) -> tuple[str, dict, dict]:
    model = snapshot["model_key"]
    inputs = request.get("input", {})
    params = {
        key: value for key, value in request.get("parameters", {}).items() if value is not None
    }
    if snapshot.get("service_type") != "video" or model not in MINIMAX_MODELS:
        raise GenerationError("unsupported_protocol")
    if inputs.keys() - {
        "prompt",
        "first_frame_media_id",
        "last_frame_media_id",
        "first_frame_url",
        "last_frame_url",
        "reference_media_ids",
        "reference_urls",
    }:
        unsupported()
    if params.keys() - {"aspect", "resolution", "duration_ms"}:
        unsupported()
    canvas = request.get("canvas_parameters") or {}
    if any(value is not None for key, value in canvas.items() if key != "mode"):
        unsupported()
    prompt = inputs.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        unsupported()
    capabilities = minimax_capabilities(model)
    duration_ms = params.get("duration_ms", 5000)
    resolution = params.get("resolution", "768P").upper()
    if (
        type(duration_ms) is not int
        or duration_ms % 1000
        or duration_ms // 1000 not in capabilities["duration_seconds"]
        or resolution not in [item.upper() for item in capabilities["resolutions"]]
    ):
        unsupported()
    first, last = inputs.get("first_frame_url"), inputs.get("last_frame_url")
    refs = inputs.get("reference_urls", [])
    if (first or last) and refs or len(refs) > 9:
        unsupported()
    for field in ("first_frame", "last_frame"):
        if inputs.get(f"{field}_media_id") and not inputs.get(f"{field}_url"):
            raise GenerationError("unresolved_media_reference")
    if inputs.get("reference_media_ids") and not refs:
        raise GenerationError("unresolved_media_reference")
    content = [{"type": "text", "text": prompt}]
    for role, url in [("first_frame", first), ("last_frame", last)] + [
        ("reference_image", ref) for ref in refs
    ]:
        if url:
            validated_url(url, query=True)
            content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
    ratio = "adaptive" if first or last else params.get("aspect", "16:9")
    if ratio not in {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        unsupported()
    if model == "MiniMax-H3-Lite" and ratio == "adaptive" and refs:
        unsupported()
    if len(content) == 1 and ratio == "adaptive":
        unsupported()
    body = {
        "model": model,
        "content": content,
        "duration": duration_ms // 1000,
        "resolution": resolution,
        "ratio": ratio,
    }
    return canvas_video_endpoint(snapshot["base_url"], "/v2/video_generation"), {}, body


def minimax_poll_endpoint(snapshot: dict, task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        raise GenerationError("invalid_provider_task")
    return canvas_video_endpoint(
        snapshot["base_url"], "/v2/query/video_generation/" + quote(task_id, safe="")
    )


def parse_minimax_result(body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
    if not isinstance(body, dict):
        invalid_response(submitted=submitted)
    if body.get("error"):
        return GenerationResult(
            "failed",
            MINIMAX_VIDEO,
            error={"code": "provider_rejected", "message": "The provider rejected generation."},
        )
    try:
        if submitted:
            identity = body["task_id"]
            if not isinstance(identity, str) or not identity:
                invalid_response(submitted=True)
            return GenerationResult("submitted", MINIMAX_VIDEO, provider_task_id=identity)
        task = body["task"]
        identity = task["id"]
        if identity != task_id:
            invalid_response(submitted=False)
        status = task["status"]
        if status in {"queued", "running"}:
            return GenerationResult("submitted", MINIMAX_VIDEO, provider_task_id=identity)
        if status in {"failed", "cancelled"}:
            return GenerationResult(
                "failed",
                MINIMAX_VIDEO,
                provider_task_id=identity,
                error={
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                },
            )
        if status != "succeeded":
            invalid_response(submitted=False)
        url = validated_url(task["content"]["url"], query=True)
        return GenerationResult(
            "succeeded",
            MINIMAX_VIDEO,
            provider_task_id=identity,
            outputs=[{"url": url, "media_type": "video"}],
            usage=task.get("usage") or {},
        )
    except (KeyError, TypeError, AttributeError):
        invalid_response(submitted=submitted)
