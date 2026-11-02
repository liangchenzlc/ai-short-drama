"""Discover original save batches independently of recent read/receipt operations."""

from typing import Any

from sqlalchemy.orm import Session

from short_drama.agent.context.canvas_results import read_canvas_results_locked
from short_drama.agent.context.delegation import delegated_inputs_locked, receipt_is_handed_off
from short_drama.agent.context.delegation_results import visible_business_outputs_locked
from short_drama.agent.context.operation_receipts import scoped_operation_locked
from short_drama.agent.context.standard_results import read_standard_results_locked
from short_drama.agent.context.subjects import visible_result_reference
from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.schemas.assistant.operations.tool_inputs import ReadTaskResults


def result_sources_locked(
    session: Session,
    task: AgentTask,
    *,
    before_id: int | None = None,
    limit: int = 10,
    allowed_keys: set[str] | None = None,
    operation: AgentOperation | None = None,
) -> dict[str, Any]:
    scope = session.get(AgentExecutionScope, operation.scope_id) if operation is not None else None
    scope_ids = {scope.id, scope.parent_scope_id} if scope is not None else None
    handoff = (
        delegated_inputs_locked(session, operation.run_id, operation.scope_id)
        if scope is not None and scope.parent_scope_id is not None
        else None
    )
    delivered_ids = (
        {
            item.id
            for item in visible_business_outputs_locked(
                session, session.get(AgentRun, operation.run_id), operation.scope_id
            )
        }
        if operation is not None
        else set()
    )
    rows, more = AgentTaskDAO(session).result_sources(
        task.id,
        before_id=before_id,
        limit=limit,
        run_id=operation.run_id if operation is not None else None,
        scope_ids=scope_ids,
        delivered_ids=delivered_ids,
    )
    if allowed_keys is None:
        run = AgentTaskDAO(session).latest_run(task.id)
        scope = (run.checkpoint or {}).get("subject_scope_keys") if run else None
        allowed_keys = set(scope) if scope is not None else None

    return {
        "items": [
            {
                "operation_id": str(row.id),
                "tool_name": row.tool_name,
                "result_refs": [
                    ref
                    for ref in row.result_refs
                    if allowed_keys is None or visible_result_reference(ref, allowed_keys)
                ],
                "read_with": {"operation_id": str(row.id)},
            }
            for row in rows
            if operation is None
            or row.scope_id == operation.scope_id
            or receipt_is_handed_off(handoff, row)
            or row.id in delivered_ids
        ],
        "next_result_before_id": str(rows[-1].id) if more else None,
    }


def read_results_locked(
    session: Session,
    conversation: AgentConversation,
    task: AgentTask,
    operation: AgentOperation,
    values: ReadTaskResults,
    settings: Settings | None,
) -> dict[str, Any]:
    source = scoped_operation_locked(session, task, operation, values.operation_id)
    if source.tool_name not in {"workbench__apply_standard_patch", "workbench__apply_canvas_patch"}:
        raise WorkflowError(
            "invalid_tool_arguments",
            "operation_id 必须是原始保存操作，不能使用读取操作或回执查询自身的 ID。"
            "请使用 result_sources 中的 operation_id；更早批次用 describe_creation_target 分页。",
            422,
            details={"result_sources": result_sources_locked(session, task, operation=operation)},
        )
    reader = (
        read_canvas_results_locked
        if source.tool_name == "workbench__apply_canvas_patch"
        else read_standard_results_locked
    )
    return reader(session, conversation, task, values)
