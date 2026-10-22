"""Bounded history sources, subject restrictions and source hashing."""

import json

from sqlalchemy import select

from short_drama.agent.attachment_context import historical_attachment_keys
from short_drama.agent.subject_context import subject_memory_keys
from short_drama.domain.agent import AgentConversation, AgentMessage
from short_drama.domain.agent_memory import (
    AgentMemoryForgetRule,
    AgentMemoryItem,
    AgentMemoryRevision,
    AgentMemorySource,
)
from short_drama.domain.agent_task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.utils.json_digest import digest

SOFT_HISTORY_BYTES = 16000
MAX_SOURCE_BYTES = 48000
HARD_CONTEXT_BYTES = 84000


def token_estimate(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) // 3 + 1


def selected_keys(task):
    return subject_memory_keys(task.context_snapshot)


def message_subject_keys(message, tasks, legacy_attachments=None):
    scopes = [ref for ref in message.references if ref.get("type") == "subject_scope"]
    if scopes:
        keys = {key for ref in scopes for key in ref.get("keys", [])}
        if not any(ref.get("includes_attachments") for ref in scopes):
            keys.update((legacy_attachments or {}).get(message.task_id, set()))
        return sorted(keys)
    if message.task_id not in tasks:
        return []
    task = tasks[message.task_id]
    return sorted(
        selected_keys(task)
        | set(task.effective_state.get("legacy_subject_keys", []))
        | (legacy_attachments or {}).get(message.task_id, set())
    )


def restricted_messages(session, conversation_id):
    conversation = session.get(AgentConversation, conversation_id)
    if conversation is None:
        return set(), set()
    sources = session.execute(
        select(AgentMemorySource.message_id, AgentMessage.task_id)
        .join(AgentMemoryItem, AgentMemoryItem.id == AgentMemorySource.memory_id)
        .join(
            AgentMemoryRevision,
            (AgentMemoryRevision.memory_id == AgentMemorySource.memory_id)
            & (AgentMemoryRevision.content_version == AgentMemorySource.content_version),
        )
        .outerjoin(AgentMessage, AgentMessage.id == AgentMemorySource.message_id)
        .where(
            AgentMemorySource.conversation_id == conversation_id,
            AgentMemoryItem.owner_user_id == conversation.owner_user_id,
            (AgentMemoryItem.state != "active")
            | (
                (AgentMemoryItem.origin == "explicit")
                & (AgentMemoryRevision.snapshot["content"].as_string() != AgentMemoryItem.content)
            ),
        )
        .with_for_update()
    ).all()
    return {row.message_id for row in sources}, {row.task_id for row in sources if row.task_id}


def message_sources(session, conversation_id, from_seq, to_seq, *, limit=500, message_ids=None):
    messages = session.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.seq >= from_seq,
            AgentMessage.seq <= to_seq,
            AgentMessage.id.in_(message_ids) if message_ids is not None else True,
        )
        .order_by(AgentMessage.seq)
        .limit(limit)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    task_ids = {row.task_id for row in messages if row.task_id}
    tasks = (
        {
            row.id: row
            for row in session.scalars(
                select(AgentTask)
                .where(AgentTask.id.in_(task_ids))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }
        if task_ids
        else {}
    )
    operations = (
        session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id.in_(task_ids), AgentOperation.status == "succeeded")
            .with_for_update()
        ).all()
        if task_ids
        else []
    )
    explicit_memory_messages = {
        str(operation.arguments.get("request_message_id"))
        for operation in operations
        if operation.tool_name
        in {"remember_memory", "update_memory", "forget_memory", "restore_memory"}
    }
    inputs = (
        dict(
            session.execute(
                select(AgentTaskInput.message_id, AgentTaskInput.applied_plan_revision)
                .where(AgentTaskInput.message_id.in_([row.id for row in messages]))
                .with_for_update()
            ).all()
        )
        if messages
        else {}
    )
    suppressed_ids, suppressed_tasks = restricted_messages(session, conversation_id)
    legacy_attachments = historical_attachment_keys(session, task_ids)
    owner = (
        select(AgentConversation.owner_user_id)
        .where(AgentConversation.id == conversation_id)
        .scalar_subquery()
    )
    rules = session.scalars(
        select(AgentMemoryForgetRule)
        .where(AgentMemoryForgetRule.owner_user_id == owner, AgentMemoryForgetRule.active == 1)
        .with_for_update()
    ).all()
    fingerprints = {value for rule in rules for value in rule.source_fingerprints}
    suppressed_ids.update(
        row.id
        for row in messages
        if digest({"role": row.role, "content": row.content}) in fingerprints
    )
    suppressed_tasks.update(
        row.task_id for row in messages if row.id in suppressed_ids and row.task_id
    )
    return [
        {
            "id": str(row.id),
            "seq": row.seq,
            "role": row.role,
            "row_version": str(row.row_version),
            "content": "这条历史来源已被纠正或遗忘，已停止自动注入。"
            if row.id in suppressed_ids
            or row.role == "assistant"
            and row.task_id in suppressed_tasks
            else row.content,
            "memory_restricted": row.id in suppressed_ids
            or row.role == "assistant"
            and row.task_id in suppressed_tasks,
            "task_id": str(row.task_id) if row.task_id else None,
            "task_status": tasks[row.task_id].status if row.task_id in tasks else None,
            "input_applied": row.id not in inputs or inputs[row.id] is not None,
            "explicit_memory_processed": str(row.id) in explicit_memory_messages,
            "hash": digest({"role": row.role, "content": row.content}),
            "subject_keys": message_subject_keys(row, tasks, legacy_attachments)
            if row.role == "assistant" and row.task_id in tasks
            else [],
            "completed_operation_ids": [
                str(op.id)
                for op in operations
                if op.task_id == row.task_id
                and op.finished_at is not None
                and op.finished_at <= row.created_at
            ],
        }
        for row in messages
    ]


def source_hash(messages):
    return digest(
        [
            {
                key: row.get(key)
                for key in (
                    "id",
                    "seq",
                    "hash",
                    "row_version",
                    "memory_restricted",
                    "input_applied",
                    "explicit_memory_processed",
                )
            }
            for row in messages
        ]
    )
