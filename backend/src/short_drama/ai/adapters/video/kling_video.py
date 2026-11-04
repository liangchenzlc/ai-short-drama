"""Kling 4 官方 API Key 接口；不套用旧版 model_name / JWT 协议。"""

import re
from urllib.parse import quote

from short_drama.ai.adapters.image.common import invalid_response, unsupported
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.official_models import protocol_models
from short_drama.ai.types import GenerationError, GenerationResult

KLING_VIDEO = "kling_video.v4"
KLING_MODELS = protocol_models("kling-video")


def kling_capabilities(model: str) -> dict:
    spec = KLING_MODELS.get(model)
    return {
        "first_frame": spec is not None,
        "reference_images": False,
        "duration_seconds": list(range(spec["duration_min"], spec["duration_max"] + 1))
        if spec
        else [],
        "resolutions": list(spec["resolutions"]) if spec else [],
        "audio_references": False,
        "generate_audio": False,
    }


def build_kling_submission(snapshot: dict, request: dict) -> tuple[str, dict, dict]:
    model = snapshot["model_key"]
    if snapshot.get("service_type") != "video" or model not in KLING_MODELS:
        raise GenerationError("unsupported_protocol")
    inputs = request.get("input", {})
    params = {
        key: value for key, value in request.get("parameters", {}).items() if value is not None
    }
    if inputs.keys() - {
        "prompt",
        "first_frame_media_id",
        "last_frame_media_id",
        "first_frame_url",
        "last_frame_url",
        "reference_media_ids",
    }:
        unsupported()
    if params.keys() - {"aspect", "resolution", "duration_ms"} or inputs.get("reference_media_ids"):
        unsupported()
    canvas = request.get("canvas_parameters") or {}
    if any(value is not None for key, value in canvas.items() if key != "mode"):
        unsupported()
    prompt = inputs.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        unsupported()
    duration = params.get("duration_ms", 10000)
    resolution = params.get("resolution", "1080p").lower()
    caps = kling_capabilities(model)
    if (
        type(duration) is not int
        or duration % 1000
        or duration // 1000 not in caps["duration_seconds"]
        or resolution not in caps["resolutions"]
    ):
        unsupported()
    frames = []
    for role in ("first_frame", "last_frame"):
        url = inputs.get(f"{role}_url")
        if inputs.get(f"{role}_media_id") and not url:
            raise GenerationError("unresolved_media_reference")
        if url:
            frames.append({"type": role, "url": validated_url(url, query=True)})
    settings = {"resolution": resolution, "duration": duration // 1000}
    if frames:
        path = "/image-to-video/" + model
        body = {"contents": [{"type": "prompt", "text": prompt}, *frames], "settings": settings}
    else:
        ratio = params.get("aspect", "16:9")
        if ratio not in {"21:9", "16:9", "9:16", "1:1"}:
            unsupported()
        settings["aspect_ratio"] = ratio
        path = "/text-to-video/" + model
        body = {"prompt": prompt, "settings": settings}
    return validated_url(snapshot["base_url"].strip()).rstrip("/") + path, {}, body


def kling_poll_endpoint(snapshot: dict, task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        raise GenerationError("invalid_provider_task")
    return (
        validated_url(snapshot["base_url"].strip()).rstrip("/")
        + "/tasks?task_ids="
        + quote(task_id, safe="")
    )


def parse_kling_result(body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
    if not isinstance(body, dict):
        invalid_response(submitted=submitted)
    if body.get("code") != 0:
        return GenerationResult(
            "failed",
            KLING_VIDEO,
            error={"code": "provider_rejected", "message": "The provider rejected generation."},
        )
    try:
        task = (
            body["data"]
            if submitted
            else next(item for item in body["data"]["result"] if item["id"] == task_id)
        )
        identity = task["id"]
        if not isinstance(identity, str) or not identity:
            invalid_response(submitted=submitted)
        status = task["status"]
        if status == "failed":
            return GenerationResult(
                "failed",
                KLING_VIDEO,
                provider_task_id=identity,
                error={
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                },
            )
        if status in {"submitted", "processing"} or submitted and status == "succeeded":
            return GenerationResult("submitted", KLING_VIDEO, provider_task_id=identity)
        if status != "succeeded":
            invalid_response(submitted=submitted)
        outputs = [
            {"url": validated_url(item["url"], query=True), "media_type": "video"}
            for item in task["outputs"]
            if item["type"] == "video"
        ]
        if not outputs:
            invalid_response(submitted=submitted)
        return GenerationResult(
            "succeeded",
            KLING_VIDEO,
            provider_task_id=identity,
            outputs=outputs,
            usage={"billing": task["billing"]} if task.get("billing") else {},
        )
    except (KeyError, TypeError, AttributeError, StopIteration):
        invalid_response(submitted=submitted)
