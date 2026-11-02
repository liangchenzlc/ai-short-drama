"""Existing frozen scope and source rules, kept with their actual responsibility."""

from sqlalchemy import select

from short_drama.agent.tools.contracts import MEDIA_TOOL as MEDIA_TOOL
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Project
from short_drama.domain.assistant.operations.media import AgentMediaRequest
from short_drama.domain.assistant.tasks.task import AgentOperation


def batch_receipt_locked(session, task, operation):
    intents = session.scalars(
        select(AgentMediaRequest)
        .where(
            AgentMediaRequest.task_id == task.id,
            AgentMediaRequest.operation_id == operation.id,
        )
        .order_by(AgentMediaRequest.item_index)
    ).all()
    if not intents:
        raise WorkflowError(
            "assistant_media_receipt_missing", "媒体批次账本缺失，不能确认完成", 409
        )
    return {
        "operation_id": str(operation.id),
        "project_id": (task.execution_target or {}).get("project_id"),
        **(
            {"canvas_id": task.execution_target["canvas_id"]}
            if (task.execution_target or {}).get("mode") == "infinite_canvas"
            else {}
        ),
        "admitted": True,
        "settled": all(
            item.status in {"succeeded", "failed", "conflict", "not_submitted"} for item in intents
        ),
        "items": [
            {
                "request_id": str(item.id),
                "item_index": item.item_index,
                "media_kind": item.media_kind,
                "target_kind": item.frozen_target["kind"],
                "target_id": item.frozen_target["id"],
                "generation_id": str(item.generation_task_id) if item.generation_task_id else None,
                "count": item.requested_count,
                "status": item.status,
                "reservation_state": item.reservation_state,
                "result": item.result,
                "error": item.error,
            }
            for item in intents
        ],
    }


def generation_status_locked(session, conversation, task, values):
    operation = session.scalar(
        select(AgentOperation).where(
            AgentOperation.id == values.operation_id,
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name == MEDIA_TOOL,
        )
    )
    if operation is None:
        raise NotFound("本任务中没有此媒体批次")
    if (operation.result or {}).get("already_applied"):
        original_id = operation.result.get("operation_id")
        original = (
            session.scalar(
                select(AgentOperation).where(
                    AgentOperation.id == int(original_id),
                    AgentOperation.task_id == task.id,
                    AgentOperation.tool_name == MEDIA_TOOL,
                )
            )
            if original_id
            else None
        )
        if original is None or original.id == operation.id:
            raise NotFound("媒体批次原始回执不存在")
        operation = original
    project_id = (operation.result or {}).get("project_id")
    if (
        project_id is None
        or session.scalar(
            select(Project.id).where(
                Project.id == int(project_id),
                Project.owner_user_id == conversation.owner_user_id,
                Project.archived_at.is_(None),
            )
        )
        is None
    ):
        raise NotFound("媒体批次作品已删除或不再可访问")
    return batch_receipt_locked(session, task, operation)
