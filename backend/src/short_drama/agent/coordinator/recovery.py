"""Recover expired deliveries; readiness services schedule persistent wait resumes."""

from datetime import timedelta

from sqlalchemy import and_, or_, select

from short_drama.agent.core.access import lock_run, may_decide
from short_drama.agent.core.events import TERMINAL
from short_drama.agent.core.state import finish_locked, mark_scheduled
from short_drama.agent.session.lifecycle import finalize_deletion_locked
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.service.assistant.execution.interruptions import settle_interrupted_locked
from short_drama.service.base import utcnow


def recover(factory, settings, limit=100):
    from short_drama.agent.sandbox.workspace import cleanup_terminal_runs

    cleanup_terminal_runs(factory, settings, limit)
    if not settings.agent_enabled:
        return 0
    now = utcnow()
    count = 0
    with factory.begin() as session:
        ids = session.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.status.in_({"queued", "running"}),
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
            if (
                run.status in TERMINAL
                or run.status == "waiting_generation"
                or (run.lease_until and run.lease_until > now)
            ):
                continue
            turns = session.scalars(
                select(AgentTurn).where(AgentTurn.run_id == run.id).with_for_update()
            ).all()
            unknown = False
            for turn in turns:
                if turn.status == "sent" or (
                    turn.status == "succeeded"
                    and turn.usage.get("source") == "assistant"
                    and not (turn.response or {}).get("graph_message")
                ):
                    # 模型发送和图确认之间无法承诺恰好一次；费用与受理事实单独保留。
                    turn.status, turn.error = (
                        "unknown",
                        {"code": "agent_acceptance_unknown", "accepted_unknown": True},
                    )
                    turn.updated_at = turn.finished_at = now
                unknown |= (
                    turn.status == "unknown" and turn.usage.get("source") != "conversation_title"
                )
            stopped = run.cancel_requested or not may_decide(session, project, conversation, run)
            if stopped or unknown:
                code = "agent_stopped" if stopped else "agent_acceptance_unknown"
                settle_interrupted_locked(session, conversation, run, code)
                if stopped:
                    from short_drama.agent.task.controls import apply_controls_locked
                    from short_drama.agent.task.queries import task_for_run

                    apply_controls_locked(
                        session,
                        conversation,
                        task_for_run(session, run),
                        run,
                    )
                finish_locked(
                    session, conversation, run, "cancelled" if stopped else "failed", {"code": code}
                )
                finalize_deletion_locked(session, conversation)
            else:
                # 故障恢复从框架 checkpoint 继续；普通工具结算不会经过此处投递。
                run.checkpoint = {
                    key: value
                    for key, value in run.checkpoint.items()
                    if key != "model_connections"
                }
                mark_scheduled(run, phase="model")
            count += 1
    from short_drama.service.assistant.execution.waiting import collect_ready_waits

    return count + collect_ready_waits(factory, settings, limit)
