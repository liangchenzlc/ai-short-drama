"""固定源 runninghub-workflow 声明式视频合同；与内置工作流分别执行。"""

import base64
import binascii
import json
import re
from copy import deepcopy

from short_drama.ai.types import GenerationError, GenerationResult
from short_drama.schemas.canvas.models.model_catalog import validate_public_json

RUNNINGHUB_WORKFLOW = "canvas_runninghub_workflow.v1"
OPTION_KEYS = frozenset(
    {"version", "model", "input", "workflow", "client_id", "webhook", "webhook_events_filter"}
)
URL_KEYS = (
    "url",
    "file_url",
    "fileUrl",
    "image_url",
    "imageUrl",
    "video_url",
    "videoUrl",
    "audio_url",
    "audioUrl",
    "uri",
)
PROCESSING = frozenset({"running", "processing", "in_progress", "executing", "task_status_running"})
SUCCEEDED = frozenset(
    {"succeeded", "success", "completed", "complete", "done", "task_status_succeed"}
)
FAILED = frozenset({"failed", "failure", "error", "expired", "cancelled", "canceled", "aborted"})


def _empty(value) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, (dict, list)) and not value)
    )


def _coalesce(*values):
    return next((value for value in values if not _empty(value)), None)


def workflow_body(value: dict, model: str) -> dict:
    providers = (value.get("metadata") or {}).get("providerOptions") or {}
    if not isinstance(providers, dict) or any(not isinstance(key, str) for key in providers):
        raise GenerationError("unsupported_parameters")
    declarations = [
        options for key, options in providers.items() if key.strip() == "runninghub-workflow"
    ]
    options = declarations[0] if declarations else {}
    if options is None:
        options = {}
    if len(declarations) > 1 or not isinstance(options, dict) or set(options) - OPTION_KEYS:
        raise GenerationError("unsupported_parameters")
    try:
        validate_public_json(options)
        json.dumps(options, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise GenerationError("unsupported_parameters") from None
    body = {
        "version": options.get("version"),
        "model": _coalesce(options.get("model"), model),
        "input": _coalesce(
            options.get("input"),
            {
                "prompt": value["prompt"],
                **{
                    kind: [item.get("url") or item.get("dataUrl") or "" for item in value[group]]
                    for kind, group in (
                        ("images", "referenceImages"),
                        ("videos", "referenceVideos"),
                        ("audios", "referenceAudios"),
                    )
                },
            },
        ),
        "prompt": options.get("workflow"),
        **{key: options.get(key) for key in ("client_id", "webhook", "webhook_events_filter")},
    }
    return deepcopy({key: item for key, item in body.items() if not _empty(item)})


def _path(body: dict, path: str):
    value = body
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first(body: dict, paths: tuple[str, ...]):
    return _coalesce(*(_path(body, path) for path in paths))


def _error(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "ok", "success", "succeeded", "true"}
    return not (type(value) in {int, float} and value == 0)


def _media(value, *, submitted: bool) -> list[dict]:
    outputs = []
    for item in value if isinstance(value, list) else [value]:
        inline = None
        if isinstance(item, dict):
            inline = _first(item, ("dataUrl", "data_url", "b64_json"))
            item = inline if not _empty(inline) else _first(item, URL_KEYS)
        if not isinstance(item, str) or not item.strip():
            continue
        item = item.strip()
        if not item.startswith("data:") and inline is None:
            outputs.append({"url": item, "media_type": "video"})
            continue
        try:
            if item.startswith("data:"):
                header, encoded = item.split(",", 1)
                if not header.endswith(";base64"):
                    raise ValueError
            else:
                encoded = item
            if not base64.b64decode(encoded, validate=True):
                raise ValueError
        except (ValueError, binascii.Error):
            raise GenerationError(
                "invalid_response", accepted_unknown=submitted, retryable=not submitted
            ) from None
        outputs.append({"base64": encoded, "media_type": "video"})
    return outputs


def parse_workflow_result(body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
    def invalid():
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )

    if not isinstance(body, dict):
        invalid()
    identity = _coalesce(_first(body, ("id", "request_id", "prompt_id")), task_id)
    if type(identity) is int:
        identity = str(identity)
    if identity is not None and (
        not isinstance(identity, str) or not re.fullmatch(r"[^\x00-\x20\x7f]{1,512}", identity)
    ):
        invalid()
    status = _first(body, ("status", "state", "data.status"))
    status = status.strip().lower() if isinstance(status, str) else "pending"
    if status in FAILED or _error(_path(body, "error.code")):
        message = _first(body, ("error.message", "message", "fail_reason"))
        return GenerationResult(
            "failed",
            RUNNINGHUB_WORKFLOW,
            provider_task_id=identity,
            error={
                "code": "provider_failed",
                "message": message if isinstance(message, str) else "工作流执行失败",
            },
        )
    outputs = _media(body.get("data"), submitted=submitted)
    if status in SUCCEEDED or (outputs and status not in PROCESSING):
        if not outputs:
            raise GenerationError("missing_output", accepted_unknown=submitted)
        return GenerationResult(
            "succeeded", RUNNINGHUB_WORKFLOW, provider_task_id=identity, outputs=outputs
        )
    if not identity:
        invalid()
    return GenerationResult("submitted", RUNNINGHUB_WORKFLOW, provider_task_id=identity)
