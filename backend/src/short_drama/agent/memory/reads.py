from copy import deepcopy

from short_drama.agent.runtime.read_tools import _read_history
from short_drama.agent.tools.contracts import MEMORY_READ_LIMIT
from short_drama.core.exceptions import WorkflowError
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.service.assistant.memory.memory_store import checked_scope, read_memories_locked


def _read(session, conversation, task, operation, values, *, history):
    run = session.get(AgentRun, operation.run_id)
    checkpoint = deepcopy(run.checkpoint)
    remaining = MEMORY_READ_LIMIT - checkpoint.get("memory_read_characters", 0)
    if remaining <= 0:
        raise WorkflowError(
            "agent_memory_read_budget_exhausted", "本次任务已达到历史资料读取上限", 409
        )
    budget = min(values.character_limit, remaining)
    if history:
        result = _read_history(session, conversation, task, values, budget)
    else:
        if len(set(values.ids)) != len(values.ids):
            raise WorkflowError("invalid_tool_arguments", "记忆 ID 不能重复", 422)
        scope = checked_scope(
            session, conversation.owner_user_id, conversation.id, task.memory_project_ids
        )
        result = read_memories_locked(
            session,
            scope,
            values.ids,
            versions=values.versions,
            offset=values.offset,
            character_limit=budget,
        )
    checkpoint["memory_read_characters"] = checkpoint.get("memory_read_characters", 0) + sum(
        len(item["content"]) for item in result["items"]
    )
    run.checkpoint = checkpoint
    return result


def read_memory_locked(session, conversation, task, operation, values):
    return _read(session, conversation, task, operation, values, history=False)


def read_history_locked(session, conversation, task, operation, values):
    return _read(session, conversation, task, operation, values, history=True)
