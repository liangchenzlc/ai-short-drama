from sqlalchemy import select

from short_drama.agent.memory.sources import restricted_messages
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.memory.memory import AgentMemoryDAO
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.memory.memory import AgentMemoryItem
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from short_drama.schemas.assistant.memory.memory import MemoryCreate
from short_drama.service.assistant.memory.memory_store import (
    apply_memory_command_locked,
    blocked_extraction,
    checked_scope,
    create_memory_locked,
    require_memory_enabled,
    source_rows,
)


def execute_memory_locked(session, conversation, task, operation, values, settings):
    generation = require_memory_enabled(session, conversation.owner_user_id)
    scope = checked_scope(session, conversation.owner_user_id)
    identifiers = set(values.source_message_ids)
    messages = session.scalars(
        select(AgentMessage)
        .join(
            AgentTaskInput,
            AgentTaskInput.message_id == AgentMessage.id,
        )
        .where(
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision <= operation.input_revision,
            AgentTaskInput.applied_plan_revision.is_not(None),
            AgentMessage.role == "user",
            AgentMessage.conversation_id == conversation.id,
            AgentMessage.id.in_(identifiers),
            AgentMessage.memory_capture_generation == generation,
        )
    ).all()
    if identifiers != {message.id for message in messages}:
        raise WorkflowError("assistant_memory_sources", "记忆来源须为本任务已应用用户消息", 403)
    sources = source_rows(session, conversation.owner_user_id, conversation.id, list(identifiers))
    restricted, _tasks = restricted_messages(session, conversation.id)
    if identifiers.intersection(restricted):
        raise Conflict("已遗忘来源不能重新进入记忆")
    previous = session.scalar(
        select(AgentOperation)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name == operation.tool_name,
            AgentOperation.payload_hash == operation.payload_hash,
            AgentOperation.status == "succeeded",
        )
        .limit(1)
    )
    if previous:
        operation.result_refs = previous.result_refs
        return {**previous.result, "already_applied": True, "operation_id": str(previous.id)}
    action = operation.tool_name.removeprefix("runtime__").removesuffix("_memory")
    before = None
    if action == "remember":
        create = MemoryCreate(
            kind=values.kind,
            memory_key=values.memory_key,
            title=values.title,
            content=values.content,
        )
        if blocked_extraction(
            session,
            conversation.owner_user_id,
            create.kind,
            create.memory_key,
            sources,
        ):
            raise Conflict("已遗忘记忆须用新的明确来源恢复")
        row = create_memory_locked(session, conversation.owner_user_id, create, sources=sources)
    else:
        row = session.scalar(
            AgentMemoryDAO(session)
            .rows(scope, active=False)
            .where(AgentMemoryItem.id == values.memory_id)
            .with_for_update()
        )
        if row is None:
            raise NotFound("记忆不存在或超出当前任务范围")
        if row.content_version != values.content_version:
            raise Conflict("记忆版本已变化，请读取当前版本")
        if action == "restore" and any(
            message.created_at <= row.updated_at for message in messages
        ):
            raise Conflict("恢复记忆需要遗忘之后的新用户输入")
        if action == "update" and not values.content:
            raise WorkflowError("invalid_tool_arguments", "更新记忆需要正文", 422)
        before = {
            "id": str(row.id),
            "content_version": str(row.content_version),
            "state": row.state,
        }
        apply_memory_command_locked(session, row, action, values.content, sources)
    session.flush()
    reference = {
        "kind": "memory",
        "id": str(row.id),
        "name": row.title,
        "content_version": str(row.content_version),
        "state": row.state,
    }
    operation.result_refs = [reference]
    operation.changes = [
        {
            "kind": "memory",
            "id": str(row.id),
            "before": before,
            "after": reference,
            "source_message_ids": [str(item) for item in identifiers],
        }
    ]
    return {"memory": reference, "already_applied": False}
