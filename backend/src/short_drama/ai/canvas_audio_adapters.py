"""固定源async-audio的任务、轮询和结果合同；不重复供应商提交。"""

import base64
import binascii
import re
from urllib.parse import quote

from pydantic import ValidationError

from short_drama.schemas.canvas_generation_parameters import CanvasAudioProviderOptions

from .canvas_video_adapters import canvas_video_endpoint
from .transport import validated_url
from .types import GenerationError, GenerationResult

ASYNC_AUDIO = "canvas_async_audio.v1"
ASYNC_AUDIO_POLL_SECONDS = 2.5


def canvas_audio_voice(model: str, voice: str) -> str:
    """固定源音色解析，空结果由调用方按manifest继续回落。"""
    model = model.rsplit("::", 1)[-1].strip().lower()
    voice = voice.strip()
    if "minimax-music" in model:
        return ""
    if "minimax-speech" in model and voice.lower() in {
        "",
        "中文",
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    }:
        return "male-qn-qingse"
    return voice or "alloy"


def canvas_audio_options(snapshot: dict, request: dict, adapter: str) -> CanvasAudioProviderOptions:
    namespace = "async-audio" if adapter == ASYNC_AUDIO else "openai-audio"
    metadata = ((request.get("canvas_request") or {}).get("input") or {}).get("metadata") or {}
    defaults = snapshot.get("canvas_audio_defaults") or {}
    if not isinstance(metadata, dict) or not isinstance(defaults, dict):
        raise GenerationError("unsupported_parameters")
    providers = metadata.get("providerOptions")
    if providers is None:
        providers = {}
    if not isinstance(providers, dict):
        raise GenerationError("unsupported_parameters")
    declarations = [value for key, value in providers.items() if key.strip() == namespace]
    if len(declarations) > 1:
        raise GenerationError("unsupported_parameters")
    declared = declarations[0] if declarations else {}
    if declared is None:
        declared = {}
    if not isinstance(declared, dict):
        raise GenerationError("unsupported_parameters")
    try:
        return CanvasAudioProviderOptions.model_validate({**defaults, **declared})
    except ValidationError:
        raise GenerationError("unsupported_parameters") from None


def canvas_audio_poll_endpoint(snapshot: dict, task_id: str) -> str:
    if not isinstance(task_id, str) or not re.fullmatch(r"[^\x00-\x20\x7f]{1,512}", task_id):
        raise GenerationError("invalid_provider_task")
    return canvas_video_endpoint(snapshot["base_url"], "/v1/audio/tasks/" + quote(task_id, safe=""))


def _path(body: dict, path: str):
    value = body
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(body: dict, paths: tuple[str, ...]):
    return next(
        (value for path in paths if (value := _path(body, path)) not in (None, "", False)), None
    )


def _audio_output(value, *, submitted: bool) -> dict | None:
    """源manifest支持媒体对象/数组；画布音频结果仍取第一个产物。"""
    values = value if isinstance(value, list) else [value]
    for item in values:
        inline = None
        if isinstance(item, dict):
            inline = _first(item, ("dataUrl", "data_url", "b64_json"))
            item = inline or _first(
                item, ("url", "file_url", "fileUrl", "audio_url", "audioUrl", "uri")
            )
        if item in (None, ""):
            continue
        try:
            if not isinstance(item, str):
                raise ValueError
            item = item.strip()
            if item.startswith("data:"):
                header, encoded = item.split(",", 1)
                if not header.lower().endswith(";base64"):
                    raise ValueError
            elif inline:
                encoded = item
            else:
                validated_url(item, query=True)
                return {"url": item, "media_type": "audio"}
            if not base64.b64decode(encoded, validate=True):
                raise ValueError
            return {"base64": encoded, "media_type": "audio"}
        except (GenerationError, ValueError, binascii.Error):
            raise GenerationError(
                "invalid_response", accepted_unknown=submitted, retryable=not submitted
            ) from None
    return None


def parse_canvas_audio_result(
    body: dict, *, submitted: bool, task_id: str | None = None
) -> GenerationResult:
    def invalid() -> None:
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )

    if not isinstance(body, dict):
        invalid()
    identity = _first(body, ("id", "task_id", "data.id")) or task_id
    if type(identity) is int:
        identity = str(identity)
    if identity is not None and (
        not isinstance(identity, str) or not re.fullmatch(r"[^\x00-\x20\x7f]{1,512}", identity)
    ):
        invalid()
    status = _first(body, ("status", "data.status"))
    if status is not None and not isinstance(status, str):
        invalid()
    status = status.strip().lower() if status else "pending"
    if body.get("error") or status in {
        "failed",
        "failure",
        "error",
        "expired",
        "cancelled",
        "canceled",
        "aborted",
    }:
        return GenerationResult(
            "failed",
            ASYNC_AUDIO,
            provider_task_id=identity,
            error={"code": "provider_failed", "message": "The provider could not complete audio."},
        )
    url = _first(
        body, ("audio_url", "audioUrl", "result_url", "url", "data.audio_url", "output.url")
    )
    output = _audio_output(url, submitted=submitted)
    succeeded = status in {
        "succeeded",
        "success",
        "completed",
        "complete",
        "done",
        "task_status_succeed",
    }
    processing = status in {
        "running",
        "processing",
        "in_progress",
        "executing",
        "task_status_running",
    }
    if succeeded or (output and not processing):
        if output:
            outputs = [output]
        elif identity:
            outputs = [{"canvas_audio_content_task_id": identity, "media_type": "audio"}]
        else:
            invalid()
        return GenerationResult(
            "succeeded", ASYNC_AUDIO, provider_task_id=identity, outputs=outputs
        )
    if not identity:
        invalid()
    return GenerationResult("submitted", ASYNC_AUDIO, provider_task_id=identity)


def complete_audio_urls(snapshot: dict, result: GenerationResult) -> GenerationResult:
    for output in result.outputs:
        if "canvas_audio_content_task_id" in output:
            identity = output.pop("canvas_audio_content_task_id")
            output["url"] = canvas_audio_poll_endpoint(snapshot, identity) + "/content"
    return result
