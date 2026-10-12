"""固定源内置 RunningHub Workflow/App 的 JSON 提交与 POST 轮询合同。"""

import base64
import binascii
import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import ValidationError

from short_drama.schemas.canvas_runninghub import RunningHubWorkflow

from .canvas_runninghub_fields import fields_for_mode, node_info, validate_slots
from .canvas_runninghub_values import scalar
from .transport import validated_url
from .types import GenerationError, GenerationResult

RUNNINGHUB_PLUGIN = "runninghub-workflow-provider"
RUNNINGHUB_ADAPTERS = {
    f"canvas_runninghub_workflow_{kind}.v1": kind for kind in ("image", "video", "audio")
}
RUNNINGHUB_VIDEO = "canvas_runninghub_workflow_video.v1"
RUNNINGHUB_POLL_SECONDS = 2.5
URL_CONTAINERS = {
    "fileurl",
    "file_url",
    "url",
    "downloadurl",
    "download_url",
    "src",
    "output",
    "outputs",
    "results",
    "files",
    "data",
    "images",
    "videos",
    "audio",
    "audios",
    "result",
}


def runninghub_root(base: str) -> str:
    value = validated_url(base.strip() or "https://www.runninghub.cn").rstrip("/")
    for suffix in ("/openapi/v2", "/openapi"):
        if value.lower().endswith(suffix):
            return value[: -len(suffix)].rstrip("/")
    return value


def runninghub_descriptor(snapshot: dict, request: dict, adapter: str) -> RunningHubWorkflow:
    kind = RUNNINGHUB_ADAPTERS.get(adapter)
    if (
        kind != snapshot.get("service_type")
        or request.get("canvas_request", {}).get("input", {}).get("mode") != kind
        or (request.get("source") or {}).get("scene") not in {"canvas_node", "canvas_model_test"}
    ):
        raise GenerationError("unsupported_protocol")
    try:
        descriptor = RunningHubWorkflow.model_validate(request["canvas_runninghub"])
    except (KeyError, TypeError, ValidationError):
        raise GenerationError("unsupported_workflow_parameters") from None
    identity = (
        descriptor.webapp_id or descriptor.workflow_id
        if descriptor.kind == "app"
        else descriptor.workflow_id or snapshot.get("model_key", "")
    )
    if not identity.strip():
        raise GenerationError("workflow_identity_missing")
    return descriptor


def runninghub_submission(
    snapshot: dict, request: dict, adapter: str, *, files: dict | None = None
) -> tuple[str, dict, dict]:
    descriptor = runninghub_descriptor(snapshot, request, adapter)
    inputs = request["canvas_request"]["input"]
    fields = fields_for_mode(descriptor.fields, inputs["mode"])
    if files is not None or fields or descriptor.workflow_json:
        validate_slots(fields, inputs)
    if files is None:
        files = {}
        for group in ("referenceImages", "referenceVideos", "referenceAudios", "mask"):
            references = [inputs.get("mask")] if group == "mask" else inputs.get(group, [])
            for reference in references:
                if reference:
                    files[
                        reference.get("id") or reference.get("storageKey") or reference.get("url")
                    ] = "reference-placeholder"
        # 文件名只能由真实上传回执取得；纯准入只验证字段及媒体槽位。
        media_fields = [
            field for field in fields if field.field_type.upper() in {"IMAGE", "VIDEO", "AUDIO"}
        ]
        fields = [
            field.model_copy(update={"options": []}) if field in media_fields else field
            for field in fields
        ]
    info = node_info(fields, files, inputs, descriptor.workflow_json)
    body = {}
    suffix = "/task/openapi/create"
    if descriptor.kind == "app":
        suffix = "/task/openapi/ai-app/run"
        body["webappId"] = (descriptor.webapp_id or descriptor.workflow_id).strip()
    else:
        body["workflowId"] = (descriptor.workflow_id or snapshot["model_key"]).strip()
    if info:
        body["nodeInfoList"] = info
    return runninghub_root(snapshot["base_url"]) + suffix, {}, body


def workflow_prompt(body: dict, *, required: bool) -> dict:
    try:
        raw = body["data"]["prompt"] if required else (body.get("data") or {}).get("prompt")
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() or required else {}
        if raw is None and not required:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError
        return raw
    except (KeyError, TypeError, ValueError):
        raise GenerationError("invalid_workflow_schema") from None


def _code(value) -> int | None:
    if type(value) in {int, float} and value >= 0 and int(value) == value:
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\+?\d+", value.strip()):
        return int(value)
    return None


def _status_code(value) -> int | None:
    text = scalar(value).strip().lower()
    number = _code(value)
    if number in {0, 804, 813, 805, 806}:
        return number
    text = re.sub(r"[_\- ]", "", text)
    statuses = {
        0: "success succeeded complete completed done finished finish 3",
        813: "queued queue pending waiting created submitted",
        804: "running processing executing inprogress started working 1 2",
        805: "failed failure error rejected cancelled canceled expired aborted 4 5",
    }
    return next((code for code, words in statuses.items() if text in words.split()), None)


def payload_code(body: dict) -> int | None:
    primary = None
    for key in ("code", "statusCode", "status_code"):
        if (value := _code(body.get(key))) is not None:
            primary = 0 if key != "code" and 200 <= value < 300 else value
            break
    for key in ("status", "state", "taskStatus", "task_status"):
        value = _status_code(body.get(key))
        if value is not None:
            if primary is None or primary == 0 or value != 0:
                primary = value
            break
    if primary is None:
        primary = next(
            (
                value
                for key in ("errorCode", "error_code")
                if (value := _code(body.get(key))) not in {None, 0}
            ),
            None,
        )
    nested = body.get("data")
    if isinstance(nested, dict) and (value := payload_code(nested)) is not None:
        if primary is None or primary == 0 or value != 0:
            return value
    return primary


def _identity(body: dict) -> str | None:
    nested = body.get("data")
    for container in (nested, body):
        if isinstance(container, dict):
            for key in ("taskId", "task_id", "taskID", "id"):
                value = container.get(key)
                if type(value) is int:
                    value = str(value)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def validate_task_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[^\x00-\x20\x7f]{1,512}", value):
        raise GenerationError("invalid_provider_task")
    return value


def output_urls(value) -> list[str]:
    result = []

    def visit(current):
        if isinstance(current, str):
            if current.startswith(("http://", "https://", "data:", "/")) or relative_output(
                current
            ):
                if current not in result:
                    result.append(current)
        elif isinstance(current, list):
            for child in current:
                visit(child)
        elif isinstance(current, dict):
            for key, child in current.items():
                if key.strip().lower() in URL_CONTAINERS:
                    visit(child)

    visit(value)
    return result


def relative_output(value: str) -> bool:
    text = value.strip().lower()
    if (
        not text
        or "://" in text
        or text.startswith("//")
        or ".." in unquote(text.split("?", 1)[0]).split("/")
    ):
        return False
    return text.startswith(("output/", "assets/", "input/")) or text.split("?", 1)[0].endswith(
        (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".mp4",
            ".webm",
            ".mov",
            ".m4v",
            ".mp3",
            ".wav",
            ".ogg",
            ".m4a",
            ".flac",
        )
    )


def output_url(root: str, value: str) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if ".." in unquote(parts.path).split("/"):
        raise GenerationError("invalid_response", retryable=True)
    if parts.netloc.lower() == "rh-images-1252422369.cos.ap-beijing.myqcloud.com":
        value = urlunsplit(
            (parts.scheme, "rh-images.xiaoyaoyou.com", parts.path, parts.query, parts.fragment)
        )
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/"):
        value = root.rstrip("/") + value
    elif relative_output(value):
        value = root.rstrip("/") + "/" + value
    return validated_url(value, query=True)


def parse_runninghub_result(
    body: dict, adapter: str, *, submitted: bool, task_id: str | None = None, root=""
) -> GenerationResult:
    def invalid():
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )

    if not isinstance(body, dict):
        invalid()
    identity = _identity(body) if submitted else task_id
    if identity:
        try:
            validate_task_id(identity)
        except GenerationError:
            invalid()
    code = payload_code(body)
    if submitted:
        if code not in {None, 0}:
            return GenerationResult(
                "failed",
                adapter,
                error={
                    "code": "provider_rejected",
                    "message": "RunningHub 工作流提交失败，请核对积分 Key 与工作流。",
                },
            )
        if not identity:
            invalid()
        return GenerationResult("submitted", adapter, provider_task_id=identity)
    urls = output_urls(body.get("data"))
    if code is None and urls:
        code = 0
    if code is None:
        invalid()
    if code in {805, 806}:
        return GenerationResult(
            "failed",
            adapter,
            provider_task_id=identity,
            error={
                "code": "provider_failed",
                "message": "RunningHub 工作流执行失败，请查看原任务。",
            },
        )
    if code != 0:
        return GenerationResult("submitted", adapter, provider_task_id=identity)
    if not urls:
        invalid()
    outputs = []
    for value in urls:
        if value.startswith("data:"):
            try:
                header, encoded = value.split(",", 1)
                if not header.lower().endswith(";base64"):
                    raise ValueError
                data = base64.b64decode(encoded, validate=True)
                kind = header[5:].split("/", 1)[0]
                if data and kind in {"image", "video", "audio"}:
                    outputs.append({"base64": encoded, "media_type": kind})
            except (ValueError, binascii.Error):
                invalid()
        else:
            url = output_url(root, value)
            path = urlsplit(url).path.lower()
            kind = (
                "image"
                if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
                else "video"
                if path.endswith((".mp4", ".webm", ".mov", ".m4v"))
                else "audio"
                if path.endswith((".mp3", ".wav", ".ogg", ".m4a", ".flac"))
                else RUNNINGHUB_ADAPTERS[adapter]
            )
            outputs.append({"url": url, "media_type": kind})
    if not outputs:
        raise GenerationError("workflow_output_type_unsupported")
    # URL 的类型要等实际响应 MIME 再确定；交付时仅保留最后一个 video/audio。
    return GenerationResult("succeeded", adapter, provider_task_id=identity, outputs=outputs)


def runninghub_output_kind(url: str, declared: str) -> str | None:
    mime = declared.split(";", 1)[0].strip().lower()
    if not mime or mime == "application/octet-stream":
        suffix = urlsplit(url).path.rsplit(".", 1)[-1].lower()
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
            "mp4": "video/mp4",
            "webm": "video/webm",
            "mov": "video/quicktime",
            "m4v": "video/mp4",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
            "m4a": "audio/mp4",
            "flac": "audio/flac",
        }.get(suffix, "")
    kind = mime.split("/", 1)[0]
    return kind if kind in {"image", "video", "audio"} else None


def uploaded_filename(body: dict) -> str:
    if not isinstance(body, dict):
        raise GenerationError("invalid_workflow_upload")
    if payload_code(body) not in {None, 0}:
        raise GenerationError("workflow_upload_rejected")
    for container in (body, *(body.get(key) for key in ("data", "result", "file", "files"))):
        if isinstance(container, dict):
            for key in ("fileName", "file_name", "filename", "name"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    raise GenerationError("invalid_workflow_upload")
