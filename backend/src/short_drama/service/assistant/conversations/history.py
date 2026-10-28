"""Discussion-only revisions serialized with every task and tool claim in the conversation."""

from copy import deepcopy

from sqlalchemy import func, or_, select

from short_drama.agent.core.events import ACTIVE
from short_drama.agent.task.state import new_attempt_locked, task_event
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.conversations.history import AgentDiscussionRevision
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.memory.memory import (
    AgentConversationSummary,
    AgentMemoryItem,
    AgentMemoryJob,
    AgentMemorySource,
)
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.schemas.assistant.conversations.history import (
    DiscussionEligibility,
    DiscussionRevisionRead,
    DiscussionRevisionRequest,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.service.assistant.context.attachment import attachment_key
from short_drama.service.base import Page, utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def message_locked(service, conversation, identifier):
    row = service.session.scalar(
        select(AgentMessage)
        .where(
            AgentMessage.id == parse_identifier(identifier),
            AgentMessage.conversation_id == conversation.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("消息不存在或不属于此对话")
    return row


def discussion_locked(service, conversation, message):
    session = service.session
    if message.role != "user" or message.task_id is None:
        return None, None, ("assistant_history_readonly", "只能修订全局会话中的用户讨论消息")
    task = AgentTaskDAO(session).task(message.task_id, lock=True)
    # A later call may have consumed a raw message, a summary, or a discussion conclusion.
    # Without explicit negative provenance, retain all earlier execution evidence.
    dependency = session.scalar(
        select(AgentToolCall.id)
        .join(AgentRun, AgentRun.id == AgentToolCall.run_id)
        .where(
            AgentRun.conversation_id == conversation.id,
            or_(AgentRun.task_id == task.id, AgentToolCall.updated_at >= message.created_at),
            or_(
                AgentToolCall.id.in_(select(AgentOperation.tool_call_id)),
                AgentToolCall.status.not_in(("prepared", "ready", "cancelled")),
            ),
        )
        .limit(1)
    )
    task_messages = select(AgentMessage.id).where(AgentMessage.task_id == task.id)
    memory_dependency = session.scalar(
        select(AgentMemorySource.id)
        .join(AgentMemoryItem, AgentMemoryItem.id == AgentMemorySource.memory_id)
        .where(
            AgentMemorySource.message_id.in_(task_messages),
            AgentMemoryItem.state == "active",
        )
        .limit(1)
    )
    if dependency is not None or memory_dependency is not None:
        return (
            task,
            None,
            ("assistant_history_dependency", "此讨论已有工具或已保存记忆依赖，请发送新消息纠正"),
        )
    if (
        session.scalar(
            select(AgentRun.id)
            .where(AgentRun.conversation_id == conversation.id, AgentRun.status.in_(ACTIVE))
            .limit(1)
        )
        is not None
    ):
        return task, None, ("assistant_history_busy", "请等待会话中的任务结束后再修订讨论")
    if task.input_revision != task.applied_input_revision or task.control_state != "none":
        return task, None, ("assistant_history_pending", "此任务还有待处理输入或控制，请先核对")
    reply = session.scalar(
        select(AgentMessage)
        .where(
            AgentMessage.task_id == task.id,
            AgentMessage.role == "assistant",
            AgentMessage.seq > message.seq,
        )
        .order_by(AgentMessage.seq)
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reply is None:
        return (
            task,
            None,
            ("assistant_history_no_reply", "这条讨论尚无完整回复，请通过任务继续处理"),
        )
    return task, reply, None


def eligibility(service, identifier, message_id):
    with service._transaction():
        conversation = service._writable(identifier)
        message = message_locked(service, conversation, message_id)
        _task, _reply, error = discussion_locked(service, conversation, message)
        return DiscussionEligibility(
            allowed=error is None,
            code=error[0] if error else None,
            reason=error[1] if error else None,
            row_version=message.row_version,
            current_content=message.content,
        )


def message_snapshot(message):
    return {
        "row_version": str(message.row_version),
        "content": message.content,
        "references": deepcopy(message.references),
        "artifacts": deepcopy(message.artifacts),
    }


def invalidate_discussion_locked(session, conversation):
    conversation.active_summary_id = None
    conversation.summary_covers_until_seq = 0
    conversation.summary_generation += 1
    for summary in session.scalars(
        select(AgentConversationSummary)
        .where(
            AgentConversationSummary.conversation_id == conversation.id,
            AgentConversationSummary.status.in_(("active", "ready")),
        )
        .with_for_update()
    ).all():
        summary.status = "invalidated"
    for job in session.scalars(
        select(AgentMemoryJob)
        .where(
            AgentMemoryJob.conversation_id == conversation.id,
            AgentMemoryJob.kind.in_(("summary", "extract")),
            AgentMemoryJob.status.in_(("pending", "running")),
        )
        .with_for_update()
    ).all():
        job.status, job.message_status = "stale", "idle"
        job.finished_at = utcnow()
        job.lease_token = job.lease_until = None
        job.publish_token = job.publish_until = None


def revise_discussion(service, identifier, message_id, payload, idempotency_key):
    values = DiscussionRevisionRequest.model_validate(
        service._payload(DiscussionRevisionRequest, payload)
    )
    key = attachment_key(idempotency_key)
    request_hash = digest({"message_id": str(message_id), **values.model_dump(mode="json")})
    session = service.session
    with service._transaction():
        conversation = service._writable(identifier)
        source = message_locked(service, conversation, message_id)
        prior = session.scalar(
            select(AgentDiscussionRevision)
            .where(
                AgentDiscussionRevision.conversation_id == conversation.id,
                AgentDiscussionRevision.request_key == key,
            )
            .with_for_update()
        )
        if prior is not None:
            if prior.request_hash != request_hash:
                raise Conflict("此请求身份已用于其他历史修订")
            task = AgentTaskDAO(session).task(source.task_id, lock=True)
            return service._accepted(
                conversation, task, session.get(AgentRun, prior.run_id), source
            )
        if source.row_version != values.row_version:
            raise WorkflowError(
                "assistant_history_changed", "消息版本已变化，请保留草稿并重新核对", 409
            )
        task, reply, error = discussion_locked(service, conversation, source)
        if error:
            raise WorkflowError(*error, 409)
        if max(source.row_version, reply.row_version, task.plan_revision) >= UINT64_MAX:
            raise Conflict("消息或任务版本已达上限，不能修订")
        before_source, before_reply = message_snapshot(source), message_snapshot(reply)
        previous = AgentTaskDAO(session).latest_run(task.id)
        if values.kind == "edit":
            source.content, source.row_version = values.content, source.row_version + 1
            first = session.scalar(
                select(AgentMessage.id)
                .where(AgentMessage.task_id == task.id, AgentMessage.role == "user")
                .order_by(AgentMessage.seq)
                .limit(1)
            )
            if source.id == first:
                task.goal = source.content
        task.plan_revision += 1
        task.row_version += 1
        task.updated_at = utcnow()
        # The account guard permits only this validated message's content update, in this flush.
        session.info["assistant_discussion_edit"] = source.id
        try:
            session.flush()
        finally:
            session.info.pop("assistant_discussion_edit", None)
        invalidate_discussion_locked(session, conversation)
        revision_id = next_id()
        run = new_attempt_locked(
            session,
            conversation,
            task,
            source,
            previous.model_config_id,
            previous.config_snapshot,
            trigger_revision=revision_id,
        )
        revision = AgentDiscussionRevision(
            id=revision_id,
            conversation_id=conversation.id,
            source_message_id=source.id,
            source_version=source.row_version,
            reply_message_id=reply.id,
            run_id=run.id,
            kind=values.kind,
            request_key=key,
            request_hash=request_hash,
            source_snapshot=before_source,
            reply_snapshot=before_reply,
            created_at=utcnow(),
        )
        session.add(revision)
        run.checkpoint = {
            **run.checkpoint,
            "discussion_revision": {
                "id": str(revision.id),
                "source_message_id": str(source.id),
                "source_version": str(source.row_version),
                "reply_message_id": str(reply.id),
                "reply_version": str(reply.row_version),
                "before_reply_seq": reply.seq,
            },
        }
        task_event(
            session,
            conversation,
            task,
            "message.revised",
            run=run,
            message_id=str(source.id),
            row_version=str(source.row_version),
        )
        session.flush()
        return service._accepted(conversation, task, run, source)


def revisions(service, identifier, message_id, offset=0, limit=30):
    with service._transaction():
        conversation = service._conversation(identifier)
        message = message_locked(service, conversation, message_id)
        query = select(AgentDiscussionRevision).where(
            AgentDiscussionRevision.conversation_id == conversation.id,
            or_(
                AgentDiscussionRevision.source_message_id == message.id,
                AgentDiscussionRevision.reply_message_id == message.id,
            ),
        )
        total = service.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = service.session.scalars(
            query.order_by(AgentDiscussionRevision.id.desc()).offset(offset).limit(limit)
        ).all()
        items = []
        for row in rows:
            snapshot = row.source_snapshot if message.role == "user" else row.reply_snapshot
            items.append(
                DiscussionRevisionRead(
                    id=row.id,
                    kind=row.kind,
                    row_version=snapshot["row_version"],
                    content=snapshot["content"],
                    source_message_id=row.source_message_id,
                    source_version=row.source_version,
                    run_id=row.run_id,
                    created_at=row.created_at,
                )
            )
        return Page(items=items, total=total, offset=offset, limit=limit)
