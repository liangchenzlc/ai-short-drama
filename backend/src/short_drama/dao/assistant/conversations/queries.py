from sqlalchemy import func, select

from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun


def latest_summaries(session, conversation_ids):
    """分页后批量读取摘要，列表条数增加时不增加 SQL 次数。"""
    if not conversation_ids:
        return {}, {}
    latest_runs = (
        select(
            AgentRun.conversation_id,
            AgentRun.status,
            func.row_number()
            .over(
                partition_by=AgentRun.conversation_id,
                order_by=(AgentRun.created_at.desc(), AgentRun.id.desc()),
            )
            .label("rank"),
        )
        .where(AgentRun.conversation_id.in_(conversation_ids))
        .subquery()
    )
    statuses = dict(
        session.execute(
            select(latest_runs.c.conversation_id, latest_runs.c.status).where(
                latest_runs.c.rank == 1
            )
        ).all()
    )
    latest_messages = (
        select(
            AgentMessage.conversation_id,
            func.substring(AgentMessage.content, 1, 200).label("preview"),
            func.row_number()
            .over(partition_by=AgentMessage.conversation_id, order_by=AgentMessage.seq.desc())
            .label("rank"),
        )
        .where(AgentMessage.conversation_id.in_(conversation_ids))
        .subquery()
    )
    previews = dict(
        session.execute(
            select(latest_messages.c.conversation_id, latest_messages.c.preview).where(
                latest_messages.c.rank == 1
            )
        ).all()
    )
    return statuses, previews
