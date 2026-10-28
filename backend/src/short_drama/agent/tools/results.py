"""Discover original save batches independently of recent read/receipt operations."""

from typing import Any

from sqlalchemy.orm import Session

from short_drama.agent.context.subjects import visible_result_reference
from short_drama.agent.tools.canvas import read_canvas_results_locked
from short_drama.agent.tools.contracts import ReadTaskResults
from short_drama.agent.tools.standard import read_standard_results_locked
from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.agent_task_dao import AgentTaskDAO
from short_drama.domain.agent import AgentConversation
from short_drama.domain.agent_task import AgentOperation, AgentTask


def result_sources_locked(
    session: Session,
    task: AgentTask,
    *,
    before_id: int | None = None,
    limit: int = 10,
    allowed_keys: set[str] | None = None,
) -> dict[str, Any]:
    rows, more = AgentTaskDAO(session).result_sources(task.id, before_id=before_id, limit=limit)
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
    source = session.get(AgentOperation, values.operation_id)
    if (
        source is not None
        and source.task_id == task.id
        and source.tool_name not in {"apply_standard_patch", "apply_canvas_patch"}
    ):
        raise WorkflowError(
            "invalid_tool_arguments",
            "operation_id 必须是原始保存操作，不能使用读取操作或回执查询自身的 ID。"
            "请使用 result_sources 中的 operation_id；更早批次用 describe_creation_target 分页。",
            422,
            details={"result_sources": result_sources_locked(session, task)},
        )
    reader = (
        read_canvas_results_locked
        if source is not None
        and source.task_id == task.id
        and source.tool_name == "apply_canvas_patch"
        else read_standard_results_locked
    )
    return reader(session, conversation, task, values)
