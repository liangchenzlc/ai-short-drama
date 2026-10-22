"""Deletion tombstones prevent online reads and new work while retaining business receipts."""

from sqlalchemy import select

from short_drama.agent.run_events import append_event
from short_drama.domain.agent_context import AgentAttachment
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.service.base import utcnow


def finalize_deletion_locked(session, conversation):
    from short_drama.agent.session_purge import enqueue_purge_locked

    if conversation.status != "deleting":
        return False
    tasks = session.scalars(
        select(AgentTask).where(AgentTask.conversation_id == conversation.id).with_for_update()
    ).all()
    if any(task.status in {"queued", "running", "waiting_media"} for task in tasks):
        return False
    if (
        tasks
        and session.scalar(
            select(AgentOperation.id)
            .where(
                AgentOperation.task_id.in_([task.id for task in tasks]),
                AgentOperation.status.in_(("running", "waiting_media", "reconciling")),
            )
            .limit(1)
        )
        is not None
    ):
        return False
    for task in tasks:
        task.context_snapshot = None
        task.selected_skills = []
        task.effective_state = {}
    attachments = session.scalars(
        select(AgentAttachment)
        .where(
            AgentAttachment.conversation_id == conversation.id,
            AgentAttachment.deleted_at.is_(None),
        )
        .with_for_update()
    ).all()
    for attachment in attachments:
        attachment.deleted_at = utcnow()
    conversation.status = "deleted"
    conversation.row_version += 1
    conversation.updated_at = utcnow()
    append_event(session, conversation, "conversation.deleted", {"id": str(conversation.id)})
    enqueue_purge_locked(session, conversation)
    return True
