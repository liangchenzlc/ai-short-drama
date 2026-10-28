from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.core.fence import owns_execution as owns_execution
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.runtime.read_tools import _describe, _read_history, _read_selected
from short_drama.agent.skill.contracts import (
    SKILL_LOAD,
    SKILL_RESOURCE,
    SKILL_SEARCH,
)
from short_drama.agent.skill.runtime import (
    finish_resource_locked,
    load_skill_locked,
    search_skills_locked,
)
from short_drama.agent.task.state import read_operation
from short_drama.agent.tools.canvas import (
    execute_canvas_locked,
    read_canvas_results_locked,
)
from short_drama.agent.tools.contracts import (
    CANVAS_TOOL,
    IMAGE_CONTENT_TOOL,
    IMAGE_PRIMARY_TOOL,
    IMAGE_READ_TOOL,
    MEDIA_STATUS_TOOL,
    MEDIA_TOOL,
    MEMORY_WRITE_TOOLS,
    STANDARD_READ_TOOL,
    STANDARD_TOOL,
)
from short_drama.agent.tools.contracts import MEMORY_READ_LIMIT as MEMORY_READ_LIMIT
from short_drama.agent.tools.contracts import TOOL_SCHEMAS as TOOL_SCHEMAS
from short_drama.agent.tools.contracts import DescribeTarget as DescribeTarget
from short_drama.agent.tools.contracts import MemoryCatalog as MemoryCatalog
from short_drama.agent.tools.contracts import ReadHistory as ReadHistory
from short_drama.agent.tools.contracts import ReadMemory as ReadMemory
from short_drama.agent.tools.contracts import ReadReceipt as ReadReceipt
from short_drama.agent.tools.contracts import ReadSelectedSubjects as ReadSelectedSubjects
from short_drama.agent.tools.contracts import SearchMemory as SearchMemory
from short_drama.agent.tools.contracts import tool_manifest as tool_manifest
from short_drama.agent.tools.image import (
    execute_image_primary_locked,
    read_image_content_locked,
    read_image_group_locked,
)
from short_drama.agent.tools.media import (
    generation_status_locked,
    reserve_batch_locked,
)
from short_drama.agent.tools.memory_ops import execute_memory_locked
from short_drama.agent.tools.standard import (
    execute_standard_locked,
    read_standard_results_locked,
)
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.domain.assistant.undo.undo import AgentBatchUndo
from short_drama.service.assistant.memory.memory_store import (
    checked_scope,
    memory_catalog_locked,
    read_memories_locked,
    search_memory_locked,
)
from short_drama.service.assistant.tasks.policy import generation_policy_locked


def execute_locked(
    session, conversation, task, operation, *, settings=None, resource_prepared=None
):
    name = operation.tool_name
    if task.effective_state.get("engine") == ENGINE_VERSION:
        from short_drama.agent.tools.autonomous import (
            HANDLERS,
        )
        from short_drama.agent.tools.autonomous import (
            execute_locked as execute_autonomous,
        )
        from short_drama.agent.tools.scope import autonomous_scope

        autonomous_scope(session, conversation, task, operation)
        if name in HANDLERS:
            return execute_autonomous(session, conversation, task, operation, settings)
        run = session.get(AgentRun, operation.run_id)
        if name not in (run.checkpoint or {}).get("available_tool_names", []):
            raise WorkflowError("unknown_tool", "此工具不在当前任务能力目录中", 422)
    if name not in TOOL_SCHEMAS:
        raise WorkflowError("unknown_tool", "当前任务没有此工具能力", 422)
    schema, _description = TOOL_SCHEMAS[name]
    values = schema.model_validate(operation.arguments)
    if name == SKILL_SEARCH:
        return search_skills_locked(session, conversation, task, values, settings)
    if name == SKILL_LOAD:
        run = session.get(AgentRun, operation.run_id)
        available = (run.checkpoint or {}).get("available_tool_names", [])
        return load_skill_locked(session, conversation, task, operation, values, available)
    if name == SKILL_RESOURCE:
        if not resource_prepared:
            raise WorkflowError(
                "assistant_skill_read_unprepared", "资料读取尚未准备，请使用原调用重试", 409
            )
        if resource_prepared.get("error"):
            raise resource_prepared["error"]
        return finish_resource_locked(
            session, conversation, task, operation, values, resource_prepared
        )
    if name == MEDIA_TOOL:
        return reserve_batch_locked(session, conversation, task, operation, values, settings)
    if name == MEDIA_STATUS_TOOL:
        return generation_status_locked(session, conversation, task, values)
    if name == "get_generation_policy":
        return generation_policy_locked(session, conversation.owner_user_id).model_dump(mode="json")
    if name == IMAGE_READ_TOOL:
        return read_image_group_locked(session, conversation, task, values)
    if name == IMAGE_CONTENT_TOOL:
        return read_image_content_locked(session, conversation, task, operation, values)
    if name == IMAGE_PRIMARY_TOOL:
        return execute_image_primary_locked(session, conversation, task, operation, values)
    if name in MEMORY_WRITE_TOOLS:
        return execute_memory_locked(session, conversation, task, operation, values)
    if name == STANDARD_TOOL:
        return execute_standard_locked(
            session, conversation, task, operation, values, settings=settings
        )
    if name == CANVAS_TOOL:
        return execute_canvas_locked(session, conversation, task, operation, values)
    if name == STANDARD_READ_TOOL:
        source = session.scalar(
            select(AgentOperation.tool_name).where(
                AgentOperation.id == values.operation_id, AgentOperation.task_id == task.id
            )
        )
        if source == CANVAS_TOOL:
            return read_canvas_results_locked(session, conversation, task, values)
        return read_standard_results_locked(session, conversation, task, values)
    if name == "describe_creation_target":
        return _describe(session, conversation, task, values)
    if name == "read_selected_subjects":
        return _read_selected(session, conversation, task, values)
    if name in {"memory_catalog", "search_memory", "read_memory", "read_conversation_history"}:
        scope = checked_scope(
            session, conversation.owner_user_id, conversation.id, task.memory_project_ids
        )
        if name == "memory_catalog":
            return memory_catalog_locked(session, scope, offset=values.offset, limit=values.limit)
        if name == "search_memory":
            return search_memory_locked(session, scope, values.query, limit=values.limit)
        run = session.get(AgentRun, operation.run_id)
        checkpoint = deepcopy(run.checkpoint)
        remaining = MEMORY_READ_LIMIT - checkpoint.get("memory_read_characters", 0)
        if remaining <= 0:
            raise WorkflowError(
                "agent_memory_read_budget_exhausted", "本次任务已达到历史资料读取上限", 409
            )
        budget = min(values.character_limit, remaining)
        if name == "read_memory":
            if len(set(values.ids)) != len(values.ids):
                raise WorkflowError("invalid_tool_arguments", "记忆 ID 不能重复", 422)
            result = read_memories_locked(
                session,
                scope,
                values.ids,
                versions=values.versions,
                offset=values.offset,
                character_limit=budget,
            )
        else:
            result = _read_history(session, conversation, task, values, budget)
        checkpoint["memory_read_characters"] = checkpoint.get("memory_read_characters", 0) + sum(
            len(item["content"]) for item in result["items"]
        )
        run.checkpoint = checkpoint
        return result
    receipt = session.scalar(
        select(AgentOperation).where(
            AgentOperation.id == values.operation_id,
            AgentOperation.task_id == task.id,
        )
    )
    if receipt is None:
        raise NotFound("本任务中没有此操作回执")
    return {
        "receipt": read_operation(receipt).model_dump(mode="json"),
        "undo_receipts": [
            {
                "id": str(item.id),
                "selected_keys": item.selected_keys,
                "result_refs": item.result_refs,
            }
            for item in session.scalars(
                select(AgentBatchUndo)
                .where(AgentBatchUndo.operation_id == receipt.id)
                .order_by(AgentBatchUndo.id)
            )
        ],
    }
