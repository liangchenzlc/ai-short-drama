"""Task-local attachment snapshots follow the same input boundary as source selections."""

from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain.agent_context import AgentAttachment
from short_drama.domain.agent_task import AgentTaskInput


def task_attachments(session, task, *, include_pending=False, restricted_ids=()):
    query = select(AgentTaskInput).where(AgentTaskInput.task_id == task.id)
    if not include_pending:
        query = query.where(AgentTaskInput.input_revision <= task.applied_input_revision)
    selected = {}
    for item in session.scalars(query.order_by(AgentTaskInput.input_revision)).all():
        payload = item.payload
        if "retained_attachment_ids" in payload:
            selected = {
                key: value
                for key, value in selected.items()
                if key in payload["retained_attachment_ids"]
            }
        if item.message_id not in restricted_ids:
            selected.update(
                (value["id"], value)
                for value in payload.get("attachments", [])
                if value["id"].isdecimal()
            )
    return selected


def attachment_keys(attachments):
    return {f"attachment:{identifier}" for identifier in attachments}


def historical_attachment_keys(session, task_ids):
    keys = {identifier: set() for identifier in task_ids}
    if task_ids:
        for task_id, payload in session.execute(
            select(AgentTaskInput.task_id, AgentTaskInput.payload).where(
                AgentTaskInput.task_id.in_(task_ids)
            )
        ).all():
            keys[task_id].update(
                attachment_keys(
                    value["id"]
                    for value in payload.get("attachments", [])
                    if value["id"].isdecimal()
                )
            )
    return keys


def available_attachment_ids(session, conversation, attachments):
    if not attachments:
        return set()
    return {
        str(identifier)
        for identifier in session.scalars(
            select(AgentAttachment.id)
            .where(
                AgentAttachment.id.in_([int(value) for value in attachments]),
                AgentAttachment.conversation_id == conversation.id,
                AgentAttachment.owner_user_id == conversation.owner_user_id,
                AgentAttachment.deleted_at.is_(None),
            )
            .with_for_update()
        ).all()
    }


def validate_attachment_budget(attachments, source_media):
    if len(attachments) + len(source_media) > 16:
        raise WorkflowError("agent_context_too_large", "任务附件与引用媒体合计不能超过 16 项", 422)
    if (
        sum(len((item.get("text_content") or "").encode("utf-8")) for item in attachments.values())
        > 131072
    ):
        raise WorkflowError("agent_context_too_large", "任务文本资料合计不能超过 128 KiB", 422)
