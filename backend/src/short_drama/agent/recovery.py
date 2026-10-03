"""Recover local decisions/tools and delivery only; never resend unknown model calls."""

from datetime import timedelta

from sqlalchemy import and_, or_, select

from short_drama.agent.runtime import latest_turn, lock_run, may_decide
from short_drama.agent.state import TERMINAL, finish_locked, mark_scheduled
from short_drama.domain.agent import AgentRun
from short_drama.service.base import utcnow


def recover(factory, settings, limit=100):
    if not settings.agent_enabled:
        return 0
    now = utcnow()
    count = 0
    with factory.begin() as session:
        ids = session.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.status.not_in(TERMINAL),
                or_(
                    and_(AgentRun.lease_until.is_not(None), AgentRun.lease_until <= now),
                    and_(
                        AgentRun.message_status == "published",
                        AgentRun.lease_token.is_(None),
                        AgentRun.updated_at
                        <= now - timedelta(seconds=settings.agent_lease_seconds),
                    ),
                ),
            )
            .order_by(AgentRun.updated_at, AgentRun.id)
            .limit(limit)
        ).all()
        for identifier in ids:
            rows = lock_run(session, identifier, skip_locked=True)
            if rows is None:
                continue
            project, conversation, run = rows
            if run.status in TERMINAL or (run.lease_until and run.lease_until > now):
                continue
            if run.cancel_requested or not may_decide(session, project, conversation, run):
                finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
            elif run.message_status in {"publishing", "published"}:
                # A confirmed/lost message may arrive later; version fences duplicates.
                mark_scheduled(run, phase=run.phase)
            else:
                turn = latest_turn(session, run.id)
                if run.phase == "tools":
                    mark_scheduled(run, phase="tools")
                elif turn is None or turn.status == "prepared" or (turn.response or {}).get("raw"):
                    mark_scheduled(run, phase="model")
                else:
                    if turn.status == "sent":
                        turn.status, turn.error = "unknown", {"code": "agent_acceptance_unknown"}
                        turn.updated_at = turn.finished_at = now
                    finish_locked(
                        session, conversation, run, "failed", {"code": "agent_acceptance_unknown"}
                    )
            count += 1
    return count
