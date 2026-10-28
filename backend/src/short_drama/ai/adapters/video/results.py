"""视频轮询响应、输出地址和实际参数解析。"""

import re
from urllib.parse import quote, urljoin

from short_drama.ai.adapters.image.agnes_adapters import (
    agnes_video_poll_url,
    is_agnes_video,
)
from short_drama.ai.adapters.runninghub.workflow import (
    RUNNINGHUB_WORKFLOW,
    parse_workflow_result,
)
from short_drama.ai.adapters.video.config import (
    NEWAPI_VIDEO_GENERATIONS,
    _invalid,
    canvas_video_endpoint,
)
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError, GenerationResult


def canvas_video_poll_endpoint(snapshot: dict, task_id: str, adapter: str) -> str:
    if is_agnes_video(snapshot, adapter):
        return agnes_video_poll_url(snapshot["base_url"], task_id)
    if adapter == RUNNINGHUB_WORKFLOW:
        if not isinstance(task_id, str) or not re.fullmatch(r"[^\x00-\x20\x7f]{1,512}", task_id):
            _invalid("invalid_provider_task")
        return canvas_video_endpoint(snapshot["base_url"], "/task/openapi/status")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        _invalid("invalid_provider_task")
    route = "/v1/video/generations" if adapter == NEWAPI_VIDEO_GENERATIONS else "/v1/videos"
    return canvas_video_endpoint(snapshot["base_url"], route + "/" + quote(task_id, safe=""))


def _path(body: dict, path: str):
    value = body
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first(body: dict, paths: tuple[str, ...]):
    return next(
        (value for path in paths if (value := _path(body, path)) not in (None, "", False)), None
    )


def parse_canvas_video_result(
    body: dict, adapter: str, *, submitted: bool, task_id=None
) -> GenerationResult:
    if adapter == RUNNINGHUB_WORKFLOW:
        return parse_workflow_result(body, submitted=submitted, task_id=task_id)
    if not isinstance(body, dict):
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )
    id_paths = (
        ("data.task_id", "data.taskId", "task_id", "taskId", "data.id", "id")
        if adapter == NEWAPI_VIDEO_GENERATIONS
        else ("id", "task_id", "taskId", "data.id")
    )
    identity = _first(body, id_paths) or task_id
    if type(identity) is int:
        identity = str(identity)
    if identity is not None and (
        not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", identity)
    ):
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )
    status = _first(body, ("status", "state", "data.status"))
    status = status.strip().lower() if isinstance(status, str) else "pending"
    urls = (
        (
            "data.result_url",
            "data.video_url",
            "data.output_url",
            "data.url",
            "data.metadata.url",
            "data.data.video_url",
            "data.data.output_url",
            "data.data.result_url",
            "data.data.url",
            "data.data.metadata.url",
            "video_url",
            "videoUrl",
            "result_url",
            "output_url",
            "url",
            "metadata.url",
            "output.url",
        )
        if adapter == NEWAPI_VIDEO_GENERATIONS
        else ("url", "video_url", "output.url")
    )
    result_url = _first(body, urls)
    failed = status in {
        "failed",
        "failure",
        "error",
        "expired",
        "cancelled",
        "canceled",
        "aborted",
    } or bool(_path(body, "error.code"))
    if failed:
        return GenerationResult(
            "failed",
            adapter,
            provider_task_id=identity,
            error={
                "code": "provider_failed",
                "message": "The provider could not complete generation.",
            },
        )
    completed = status in {
        "succeeded",
        "success",
        "completed",
        "complete",
        "done",
        "task_status_succeed",
    }
    if result_url and status not in {
        "running",
        "processing",
        "in_progress",
        "executing",
        "task_status_running",
    }:
        completed = True
    if completed:
        if result_url:
            if not isinstance(result_url, str):
                raise GenerationError("invalid_response", accepted_unknown=submitted)
            return GenerationResult(
                "succeeded",
                adapter,
                provider_task_id=identity,
                outputs=[{"url": result_url, "media_type": "video"}],
            )
        if adapter == NEWAPI_VIDEO_GENERATIONS:
            raise GenerationError("missing_output", accepted_unknown=submitted)
        if not identity:
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        return GenerationResult(
            "succeeded",
            adapter,
            provider_task_id=identity,
            outputs=[{"canvas_content_task_id": identity, "media_type": "video"}],
        )
    if not identity:
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )
    return GenerationResult("submitted", adapter, provider_task_id=identity)


def complete_video_urls(snapshot: dict, result: GenerationResult) -> GenerationResult:
    for output in result.outputs:
        if "canvas_content_task_id" in output:
            identity = output.pop("canvas_content_task_id")
            route = "/v1/videos/"
            output["url"] = canvas_video_endpoint(
                snapshot["base_url"], route + quote(identity, safe="") + "/content"
            )
        if "url" not in output:
            continue
        raw = output["url"]
        if raw.startswith("/") and not raw.startswith("//"):
            raw = urljoin(snapshot["base_url"], raw)
        validated_url(raw, query=True)
        output["url"] = raw
    return result


def canvas_video_resolved_parameters(body: dict) -> dict:
    result = {
        key: value
        for key, value in body.items()
        if key
        in {
            "seconds",
            "duration",
            "size",
            "aspect_ratio",
            "resolution",
            "resolution_name",
            "generate_audio",
            "watermark",
            "variants",
        }
    }
    if isinstance(body.get("metadata"), dict):
        result["metadata"] = {
            key: value
            for key, value in body["metadata"].items()
            if key in {"ratio", "generate_audio", "watermark", "omni_reference_task_type"}
        }
    return result
