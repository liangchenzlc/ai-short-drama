"""画布执行记录不能继承项目共享权限，也不能由普通编辑改写出处。"""

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    Project,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt

TABLES = frozenset(
    {
        "canvas_task_bindings",
        "canvas_task_media_references",
        "canvas_task_text_deltas",
        "canvas_results",
    }
)


def _parent(session: Session, model, identifier: int):
    row = next(
        (item for item in session.new if isinstance(item, model) and item.id == identifier), None
    )
    if row is None:
        row = session.scalar(select(model).where(model.id == identifier).with_for_update())
    if row is None:
        raise NotFound("画布任务关联不存在或无权访问")
    return row


def guard_canvas_generation(session: Session, entity, actor: ActorContext, new: bool) -> None:
    from .access import require_project

    state = inspect(entity)
    name = state.mapper.local_table.name
    if not new:
        if (
            session.scalar(
                select(type(entity).id).where(type(entity).id == entity.id).with_for_update()
            )
            is None
        ):
            raise NotFound("画布任务关联不存在或无权访问")
        mutable = (
            {
                "attachment_status",
                "attachment_receipt_id",
                "attached_at",
                "row_version",
                "updated_at",
                "updated_by",
            }
            if name == "canvas_results"
            else set()
        )
        if entity in session.deleted or any(
            attr.history.has_changes() and attr.key not in mutable for attr in state.attrs
        ):
            raise WorkflowError(
                "canvas_generation_immutable", "任务出处和执行结果不能改写或删除", 403
            )

    binding = (
        entity
        if name == "canvas_task_bindings"
        else _parent(session, CanvasTaskBinding, entity.task_binding_id)
    )
    if binding.initiated_by != actor.user_id or entity.created_by != actor.user_id:
        raise NotFound("画布任务关联不存在或无权访问")
    if (entity.project_id, entity.canvas_id) != (binding.project_id, binding.canvas_id):
        raise NotFound("任务关联不属于此画布")
    require_project(session, binding.project_id)
    project = _parent(session, Project, binding.project_id)
    canvas = _parent(session, ProjectCanvas, binding.canvas_id)
    if (
        project.workspace_mode != "infinite_canvas"
        or canvas.project_id != binding.project_id
        or canvas.archived_at is not None
    ):
        raise NotFound("活动画布不存在")
    if name == "canvas_task_bindings":
        task = _parent(session, AsyncTask, binding.async_task_id)
        if (
            task.initiated_by != actor.user_id
            or task.project_id != binding.project_id
            or task.request_hash != binding.request_hash
        ):
            raise NotFound("生成任务不属于此作者和项目")
    if name == "canvas_results":
        if new and entity.attachment_status != "detached":
            raise WorkflowError("canvas_generation_immutable", "新结果必须先保存为未附着状态", 403)
        if not new:
            # Expired ORM attributes do not always retain history.deleted after assignment.
            # Compare the current stored columns without refreshing pending changes away.
            stored = session.execute(
                select(
                    CanvasResult.attachment_status,
                    CanvasResult.attachment_receipt_id,
                    CanvasResult.attached_at,
                    CanvasResult.row_version,
                )
                .where(CanvasResult.id == entity.id)
                .with_for_update()
            ).one()
            actual = (
                entity.attachment_status,
                entity.attachment_receipt_id,
                entity.attached_at,
                entity.row_version,
            )
            if actual != tuple(stored) and not (
                stored.attachment_status == "detached"
                and entity.attachment_status == "attached"
                and entity.attachment_receipt_id is not None
                and entity.attached_at is not None
                and entity.row_version == stored.row_version + 1
            ):
                raise WorkflowError("canvas_generation_immutable", "结果附着回执不可改写", 403)
        if entity.attachment_receipt_id is not None:
            receipt = _parent(session, CanvasWriteReceipt, entity.attachment_receipt_id)
            if (
                receipt.actor_user_id,
                receipt.project_id,
                receipt.canvas_id,
                receipt.operation_kind,
            ) != (actor.user_id, entity.project_id, entity.canvas_id, "canvas.task.bind"):
                raise NotFound("结果附着回执不属于此作者和画布")
            if (
                receipt.result_json.get("taskId"),
                receipt.result_json.get("nodeId"),
                receipt.result_json.get("outputIndex"),
            ) != (str(binding.async_task_id), binding.node_key, entity.result_index):
                raise NotFound("结果附着回执不属于此任务输出")
    if name == "canvas_task_text_deltas":
        record = _parent(session, AIGenerationRecord, entity.generation_record_id)
        if record.task_id != binding.async_task_id:
            raise NotFound("文本增量不属于此任务调用")
