"""Scoped assistant media requests use native canvas admission, delivery and binding."""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import AIModelConfig, AsyncTask, CanvasTaskBinding, Project, ProjectCanvas
from short_drama.domain.assistant.operations.media import AgentMediaRequest
from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState
from short_drama.schemas.assistant.operations.media import AssistantCanvasMediaRequest
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.canvas.generation.generation import CanvasTaskBindOperation
from short_drama.schemas.canvas.generation.tasks.task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.assistant.operations.canvas import AssistantCanvasService
from short_drama.service.canvas.document.canvas import CanvasService
from short_drama.service.canvas.generation.generation import CanvasGenerationService
from short_drama.service.canvas.generation.tasks.task import CanvasTaskService
from short_drama.service.canvas.generation.tasks.task_outputs import generation_metadata


@contextmanager
def canvas_media_actor(session: Session, owner: int) -> Iterator[None]:
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner:
        raise NotFound("画布媒体账号不匹配")
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    session.info["actor"] = actor or ActorContext(owner, "", "", True, 0, "", "assistant-media")
    try:
        yield
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)


def _canvas(session: Session, owner: int, target: dict[str, Any]) -> ProjectCanvas:
    project = session.scalar(
        select(Project)
        .where(Project.id == int(target["project_id"]), Project.owner_user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.archived_at or project.workspace_mode != "infinite_canvas":
        raise NotFound("媒体目标画布作品不存在或不再可访问")
    canvas = session.scalar(
        select(ProjectCanvas)
        .where(
            ProjectCanvas.project_id == project.id,
            ProjectCanvas.source_key == target["canvas_id"],
            ProjectCanvas.archived_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if canvas is None:
        raise NotFound("媒体目标画布已删除或归档")
    return canvas


def _node_state(
    session: Session, native: CanvasService, canvas: ProjectCanvas, key: str, document: dict
) -> dict[str, Any]:
    versions = AssistantCanvasService(session)._versions(native, canvas)
    for index, node in enumerate(document["nodes"]):
        if node["id"] == key and key in versions:
            return {
                "document": deepcopy(node),
                **versions[key],
                "deleted": False,
                "z_index": index,
            }
    raise NotFound("媒体节点已删除")


def canvas_media_may_submit(session: Session, owner: int, intent: AgentMediaRequest) -> bool:
    target = intent.frozen_target
    try:
        canvas = _canvas(session, owner, target)
    except BusinessError:
        return False
    node = session.scalar(
        select(CanvasNode)
        .where(
            CanvasNode.canvas_id == canvas.id,
            CanvasNode.node_key == target["id"],
            CanvasNode.archived_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    private = session.scalar(
        select(CanvasNodeUserState)
        .where(
            CanvasNodeUserState.canvas_id == canvas.id,
            CanvasNodeUserState.node_key == target["id"],
            CanvasNodeUserState.user_id == owner,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (
        node is not None
        and node.kind == intent.media_kind
        and str(node.row_version) == target["admission_row_version"]
        and private is not None
        and str(private.row_version) == target["admission_private_revision"]
        and private.draft_json.get("taskId") == str(intent.generation_task_id)
    )


def prepare_canvas_media_locked(
    session: Session,
    owner: int,
    target: dict[str, Any],
    values: AssistantCanvasMediaRequest,
    trusted_version: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    with canvas_media_actor(session, owner):
        canvas = _canvas(session, owner, target)
        native = CanvasService(session)
        document = native._document(canvas, private=True, lock=True)
        before = _node_state(session, native, canvas, values.target_id, document)
        expected = {
            "row_version": str(values.row_version),
            "private_revision": str(values.private_revision),
        }
        if expected != trusted_version or any(
            before[key] != value for key, value in expected.items()
        ):
            raise Conflict("媒体节点或私人参数已变化，请核对并主动刷新引用")
        node = before["document"]
        if node["type"] != values.media_kind:
            raise WorkflowError("assistant_generation_scope", "媒体类型与获准节点类型不匹配", 403)
        if (
            canvas.row_version == UINT64_MAX
            or int(before["row_version"]) == UINT64_MAX
            or int(before["private_revision"]) >= UINT64_MAX - 1
        ):
            raise Conflict("画布或节点版本不足以完成生成与回填")
        model = session.scalar(
            select(AIModelConfig)
            .where(
                AIModelConfig.id == values.model_config_id,
                AIModelConfig.owner_user_id == owner,
                AIModelConfig.service_type == values.media_kind,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if model is None:
            raise NotFound("本人媒体模型不存在或已停用")
        if str(model.row_version) != str(values.model_version):
            raise Conflict("媒体模型配置已变化，请重新核对")
        metadata = node.get("metadata") or {}
        selection = metadata.get("model")
        if selection and selection != f"host-{model.id}::{model.model_key}":
            raise Conflict("生成模型与节点已保存选择不同，请先明确修改节点模型")
        names = {
            "image": ("size", "quality", "transparentBackground"),
            "video": ("size", "vquality", "videoGenerateAudio", "videoWatermark"),
            "audio": (
                "audioVoice",
                "audioFormat",
                "audioSpeed",
                "audioPitch",
                "audioVolume",
                "audioInstructions",
            ),
        }
        config = {
            key: metadata[key] for key in names[values.media_kind] if metadata.get(key) is not None
        }
        if values.media_kind == "image":
            config["count"] = values.count
        if values.media_kind == "video" and metadata.get("seconds") is not None:
            config["videoSeconds"] = str(metadata["seconds"])
        payload = {
            "project_id": canvas.source_key,
            "type": f"canvas_{values.media_kind}",
            "operation": ("image_to_video" if values.reference_media_ids else "text_to_video")
            if values.media_kind == "video"
            else values.media_kind,
            "prompt": values.prompt,
            "model": f"host-{model.id}::{model.model_key}",
            "logical_model_id": str(model.id),
            "input": {
                "mode": values.media_kind,
                "prompt": values.prompt,
                "config": config,
                "reference_images": [
                    {
                        "id": str(identifier),
                        "storage_key": f"resource:{identifier}",
                        "type": "image",
                    }
                    for identifier in values.reference_media_ids
                ],
                "metadata": {"node_id": values.target_id, "source_node_id": values.target_id},
            },
        }
        return payload, {
            "project_id": str(canvas.project_id),
            "canvas_id": canvas.source_key,
            "kind": "node",
            "id": values.target_id,
            "name": node.get("title", ""),
            "row_version": before["row_version"],
            "private_revision": before["private_revision"],
            "admission_row_version": before["row_version"],
            "admission_private_revision": str(int(before["private_revision"]) + 1),
            "before": before,
            "canvas_revision": str(canvas.row_version),
        }


def admit_canvas_media_locked(
    session: Session, owner: int, intent: AgentMediaRequest, settings: Settings
) -> dict[str, Any]:
    with canvas_media_actor(session, owner):
        payload = deepcopy(intent.frozen_request)
        key = f"assistant-media:{intent.id}"
        payload["input"]["metadata"]["client_operation_id"] = key
        summary, _created = CanvasGenerationService(session, settings).create_locked(
            CanvasRuntimeTaskCreate.model_validate(payload), key, assistant_managed=True
        )
        intent.generation_task_id = int(summary["id"])
        intent.status = "accepted"
        session.flush()
        return summary


def settle_canvas_media_locked(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    native_task: AsyncTask,
    outputs: dict[int, str],
) -> tuple[dict[str, Any], str, dict[str, str] | None]:
    target = intent.frozen_target
    saved = [
        {"output_index": index, "media_id": media_id, "status": "ready"}
        for index, media_id in sorted(outputs.items())
        if 1 <= index <= intent.requested_count
    ]
    result = {
        "canvas_id": target["canvas_id"],
        "adopted": False,
        "bound": False,
        "items": saved,
        "target_version": target["admission_row_version"],
        "private_revision": target["admission_private_revision"],
    }
    complete = native_task.status == "succeeded"
    try:
        with canvas_media_actor(session, owner):
            canvas = _canvas(session, owner, target)
            native = CanvasService(session)
            tasks = CanvasTaskService(session)
            binding = session.scalar(
                select(CanvasTaskBinding)
                .where(
                    CanvasTaskBinding.async_task_id == native_task.id,
                    CanvasTaskBinding.initiated_by == owner,
                    CanvasTaskBinding.canvas_id == canvas.id,
                    CanvasTaskBinding.node_key == target["id"],
                )
                .with_for_update()
            )
            if binding is None:
                raise NotFound("原生画布任务关联缺失，不能回填")
            if native_task.initiated_by != owner or native_task.project_id != canvas.project_id:
                raise NotFound("原生画布媒体不属于本任务账号和作品")
            results = (
                tasks.materialize_locked(canvas, binding, native_task)
                if complete
                else [
                    tasks._result(canvas, binding, native_task, entry["output_index"] - 1)
                    for entry in saved
                ]
            )
            result["items"] = [
                {
                    "id": str(row.id),
                    "output_index": row.result_index + 1,
                    "media_id": str(row.media_id),
                    "status": "ready",
                }
                for row in results
                if row.result_index < intent.requested_count
            ]
            if not complete:
                return (
                    result,
                    "failed",
                    {
                        "code": "assistant_media_partial_failure",
                        "message": (
                            "画布媒体未全部生成成功，已保存产物保留在私人任务和素材，不自动补发"
                        ),
                    },
                )
            if len(result["items"]) != intent.requested_count:
                raise Conflict("已保存画布产物数量与原请求不一致，请核对原任务")
            document = native._document(canvas, private=True, lock=True)
            before = _node_state(session, native, canvas, target["id"], document)
            if (
                before["row_version"] != target["admission_row_version"]
                or before["private_revision"] != target["admission_private_revision"]
                or before["document"]["type"] != intent.media_kind
                or before["document"].get("metadata", {}).get("taskId") != str(native_task.id)
            ):
                raise Conflict("节点或私人参数已变化，产物已交付私人素材，未覆盖节点")
            primary = min(results, key=lambda row: row.result_index)
            preview = deepcopy(document)
            node = next(item for item in preview["nodes"] if item["id"] == target["id"])
            node["metadata"] = generation_metadata(
                node.get("metadata") or {},
                primary,
                str(native_task.id),
                f"attach-node:{native_task.id}:{target['id']}:{primary.result_index}",
            )
            AssistantCanvasService(session)._check_version_ceiling(
                native, canvas, document, preview
            )
            with session.begin_nested():
                receipt = tasks.bind_locked(
                    CanvasTaskBindOperation.model_validate(
                        {
                            "op_id": f"assistant-media-bind:{intent.id}",
                            "params": {
                                "canvas_id": canvas.source_key,
                                "task_id": str(native_task.id),
                                "node_id": target["id"],
                                "output_index": primary.result_index,
                            },
                        }
                    )
                )
                if receipt["result"]["bindingStatus"] != "bound":
                    raise Conflict("原节点已删除或替换，未重新应用旧回执")
                state = _node_state(
                    session,
                    native,
                    canvas,
                    target["id"],
                    native._document(canvas, private=True, lock=True),
                )
            result.update(
                adopted=True,
                bound=True,
                primary_index=primary.result_index,
                target_version=state["row_version"],
                private_revision=state["private_revision"],
                canvas_revision=str(canvas.row_version),
            )
            return result, "succeeded", None
    except BusinessError as error:
        if isinstance(error, NotFound):
            result["target_available"] = False
        return result, "conflict", {"code": error.code, "message": error.message}


def canvas_media_changes_locked(
    session: Session, owner: int, intent: AgentMediaRequest
) -> list[dict[str, Any]]:
    target, result = intent.frozen_target, intent.result or {}
    if not result or intent.generation_task_id is None:
        return []
    with canvas_media_actor(session, owner):
        try:
            canvas = _canvas(session, owner, target)
            native = CanvasService(session)
            state = _node_state(
                session,
                native,
                canvas,
                target["id"],
                native._document(canvas, private=True, lock=True),
            )
        except BusinessError:
            return []
        # Admission itself advances private task metadata. A failed generation may
        # reuse that trusted version only while both versions and task identity match.
        if (
            state["row_version"] != result["target_version"]
            or state["private_revision"] != result["private_revision"]
            or not result.get("bound")
            and state["document"].get("metadata", {}).get("taskId")
            != str(intent.generation_task_id)
        ):
            return []
        return [
            {
                "kind": "canvas_node",
                "id": target["id"],
                "before": target["before"],
                "after": state,
                "media_role": "canvas_binding",
                "adopted": bool(result.get("bound")),
            }
        ]
