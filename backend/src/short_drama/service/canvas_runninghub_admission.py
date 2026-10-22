"""仅从本人保存的模型冻结 RunningHub 选择、字段与永久媒体。"""

import re
from copy import deepcopy

from pydantic import ValidationError

from short_drama.ai.canvas_runninghub_fields import (
    apply_parameters,
    collect_fields,
    fields_for_mode,
    freeze_random_fields,
    validate_slots,
)
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import GenerationRequestError, NotFound, WorkflowError
from short_drama.db.access import scope_of
from short_drama.schemas.canvas_runninghub import RunningHubWorkflow

from .canvas_video_admission import _https_reference


def freeze_runninghub_request(
    generation, prepared: dict, config, secrets: dict, project_id: int | None
):
    request = prepared["canvas_request"]
    inputs = request["input"]
    try:
        descriptor = RunningHubWorkflow.model_validate(
            (config.runtime_profile or {}).get("runninghub_workflow") or {}
        )
    except ValidationError:
        raise WorkflowError(
            "canvas_workflow_config_invalid", "已保存的工作流字段配置无效", 422
        ) from None
    descriptor.workflow_id = descriptor.workflow_id.strip() or config.model_key.strip()
    if descriptor.kind == "app":
        descriptor.webapp_id = descriptor.webapp_id.strip() or descriptor.workflow_id
    selected = inputs.get("execution")
    if selected is not None:
        if (
            selected["kind"] != descriptor.kind
            or selected.get("workflowId", "").strip()
            != (descriptor.workflow_id if descriptor.kind == "workflow" else "")
            or selected.get("webappId", "").strip()
            != (descriptor.webapp_id if descriptor.kind == "app" else "")
        ):
            raise WorkflowError(
                "canvas_workflow_selection_changed",
                "所选工作流与本人已保存条目不一致，请重新选择",
                409,
            )
    elif (prepared.get("source") or {}).get("scene") != "canvas_model_test":
        raise WorkflowError(
            "canvas_workflow_selection_required", "请选择本人已保存的工作流条目", 422
        )
    fields = descriptor.fields or collect_fields(descriptor.workflow_json, inputs["mode"])
    try:
        fields = fields_for_mode(fields, inputs["mode"])
        fields = apply_parameters(
            fields,
            descriptor.workflow_json,
            (inputs.get("metadata") or {}).get("workflowParameters") or {},
        )
        fields = freeze_random_fields(fields)
        if fields or descriptor.workflow_json:
            validate_slots(fields, inputs)
    except GenerationError as error:
        raise GenerationRequestError(error.code) from None
    descriptor.fields = fields
    references = [
        *inputs.get("referenceImages", []),
        *inputs.get("referenceVideos", []),
        *inputs.get("referenceAudios", []),
    ]
    if inputs.get("mask"):
        references.append(inputs["mask"])
    if references and not secrets.get("secretKey", "").strip():
        raise WorkflowError(
            "canvas_workflow_upload_key_missing",
            "参考素材上传需要已保存的素材上传 API Key（企业级）",
            422,
        )
    if not secrets.get("apiKey", "").strip():
        raise WorkflowError("canvas_workflow_key_missing", "请先保存 RunningHub 积分 API Key", 422)
    for kind, group, field in (
        ("image", "referenceImages", "reference_media_ids"),
        ("video", "referenceVideos", "video_reference_media_ids"),
        ("audio", "referenceAudios", "audio_reference_media_ids"),
        ("image", "mask", "mask_media_ids"),
    ):
        identifiers = []
        references = (
            [inputs["mask"]]
            if group == "mask" and inputs.get("mask")
            else []
            if group == "mask"
            else inputs.get(group, [])
        )
        for reference in references:
            key = reference.get("storageKey") or ""
            match = re.fullmatch(r"resource:([1-9][0-9]*)", key)
            if not match:
                if key or not _https_reference(reference.get("url") or ""):
                    raise WorkflowError(
                        "canvas_reference_not_saved",
                        "参考媒体需要当前项目的稳定资源或 HTTPS 链接",
                        422,
                    )
                continue
            identifier = match.group(1)
            media = generation._validate_media(identifier, kind)
            if project_id is None or scope_of(generation.session, media) != (None, project_id):
                raise NotFound("参考媒体不存在或不属于当前项目")
            if not media.storage_locator.startswith("minio://"):
                raise WorkflowError(
                    "canvas_reference_not_saved", "参考媒体必须先保存为永久资源", 422
                )
            identifiers.append(identifier)
            reference.pop("url", None)
            reference["type"] = media.format_code
            for name, value in (
                ("bytes", media.byte_size),
                ("width", media.width),
                ("height", media.height),
                ("durationMs", media.duration_ms),
            ):
                reference.pop(name, None)
                if value is not None:
                    reference[name] = value
        prepared["input"][field] = identifiers
    prepared["canvas_runninghub"] = deepcopy(
        descriptor.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
