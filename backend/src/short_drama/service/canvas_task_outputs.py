"""以执行记录及已归档媒体构建原版产物，不读取客户端结果 URL。"""

from copy import deepcopy
from hashlib import sha256

from pydantic import ValidationError

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.canvas_library_dao import CanvasLibraryDAO
from short_drama.domain import CanvasLibraryAsset, CanvasLibraryAssetReference, CanvasResult
from short_drama.schemas.canvas_library import CanvasLibraryDocument
from short_drama.utils.snowflake import next_id

from .canvas_service import iso


def materialize_effect_key(task_id: str | int, index: int) -> str:
    return f"materialize:{task_id}:{index}"


def generated_asset_id(task_id: str | int, index: int) -> str:
    return "generation_" + sha256(materialize_effect_key(task_id, index).encode()).hexdigest()


def durable_output(
    service, canvas, task, record, index: int, *, binding=None
) -> tuple[str, dict, int | None]:
    if task.service_type == "text":
        if index != 0:
            raise WorkflowError("invalid_params", "文本任务只能绑定 outputIndex 0", 422)
        content = (record.text_content or "").strip()
        if not content:
            raise WorkflowError("output_not_ready", "文本任务还没有可绑定的结果", 409)
        return "text", {"content": content}, None
    output = service.tasks.output(record.id, index)
    if output is None:
        raise WorkflowError("output_not_ready", "任务产物尚未归档，不能绑定到画布", 409)
    asset, media = output
    kind = task.service_type
    if (
        asset.media_type != kind
        or media.project_id != canvas.project_id
        or media.created_by != service.canvases.actor_id
        or not media.storage_locator.startswith("minio://")
        or not media.format_code.startswith(kind + "/")
    ):
        raise WorkflowError("resource_mismatch", "任务媒体的作者、项目或类型不匹配", 409)
    url = f"/api/v1/canvas-runtime/resources/{media.id}/file"
    storage_key = f"resource:{media.id}"
    data = {"storageKey": storage_key, "bytes": media.byte_size, "mimeType": media.format_code}
    data["dataUrl" if kind == "image" else "url"] = url
    if kind in {"image", "video"}:
        data.update(width=media.width, height=media.height)
    if media.duration_ms is not None:
        data["durationMs"] = media.duration_ms
    library = CanvasLibraryDAO(service.session)
    key = generated_asset_id(task.id, index)
    existing = library.asset(key, lock=True)
    if existing is None:
        # 已附着的旧版身份仍有效，不为切换交付入口重建同一份产物。
        existing = library.asset(f"generated:{task.id}:{index}", lock=True)
    if existing is not None:
        references = library.references(existing.id)
        if (
            existing.user_id != service.canvases.actor_id
            or existing.project_id != canvas.project_id
            or existing.kind != kind
            or (existing.payload_json.get("data") or {}).get("storageKey") != storage_key
            or not any(reference.media_id == media.id for reference in references)
        ):
            raise WorkflowError("resource_mismatch", "已交付素材与本次任务产物不一致", 409)
        key = existing.source_key
    values = service.canvases.audit() if existing is None else None
    title = {"image": "生成图片", "video": "生成视频", "audio": "生成音频"}[kind]
    version_key = f"version:{task.id}" + (f":{index}" if index else "")
    metadata = {
        "source": "generation-task",
        "generationEffectKey": materialize_effect_key(task.id, index),
        "canvasId": canvas.source_key,
        "taskId": str(task.id),
        "outputIndex": index,
    }
    if binding is not None:
        metadata["nodeId"] = binding.node_key
    try:
        document = CanvasLibraryDocument.model_validate(
            {
                "id": key,
                "kind": kind,
                "title": title,
                "category": "material",
                "status": "confirmed",
                "primaryVersionId": sha256(version_key.encode()).hexdigest()[:32],
                "coverUrl": url,
                "tags": ["生成"],
                "source": "生成任务",
                "data": data,
                "metadata": metadata,
                **(
                    {
                        "createdAt": iso(values["created_at"]),
                        "updatedAt": iso(values["updated_at"]),
                    }
                    if values is not None
                    else {}
                ),
            }
        )
    except ValidationError:
        raise WorkflowError(
            "resource_metadata_incomplete", "任务媒体缺少有效的实际元数据，不能绑定到画布", 409
        ) from None
    if values is not None:
        service.session.add(
            CanvasLibraryAsset(
                **values,
                user_id=service.canvases.actor_id,
                project_id=canvas.project_id,
                source_key=key,
                kind=kind,
                title=document.title,
                category=document.category,
                status=document.status,
                payload_json=document.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
        )
        service.session.flush()
        service.session.add(
            CanvasLibraryAssetReference(
                id=next_id(),
                library_asset_id=values["id"],
                media_id=media.id,
                binary_id=None,
            )
        )
    content = {
        "content": url,
        "storageKey": storage_key,
        "resourceId": str(media.id),
        "assetId": key,
        "mimeType": media.format_code,
        **{
            key: value
            for key, value in {
                "bytes": media.byte_size,
                "naturalWidth": media.width,
                "naturalHeight": media.height,
                "durationMs": media.duration_ms,
            }.items()
            if value is not None
        },
    }
    return kind, content, media.id


def generation_metadata(
    metadata: dict, result: CanvasResult, task_id: str, effect_key: str
) -> dict:
    value = deepcopy(metadata)
    for key, item in result.content_json.items():
        if key == "resourceId":
            continue
        if isinstance(item, str) and item or type(item) in {float, int} and item > 0:
            value[key] = item
    if result.kind != "text":
        value.update(nodeRole="result", resultOrigin="generated")
    value.update(status="success", taskId=task_id, taskStatus="succeeded", taskProgress=100)
    for key in (
        "errorDetails",
        "generationErrorCode",
        "resourceReloadAvailable",
        "failedPromptFingerprint",
        "failedInputFingerprint",
    ):
        value[key] = None
    keys = value.get("generationEffectKeys")
    keys = (
        [key for key in keys if isinstance(key, str) and key.strip()]
        if isinstance(keys, list)
        else []
    )
    if effect_key not in keys:
        keys.append(effect_key)
    value["generationEffectKeys"] = keys
    return value
