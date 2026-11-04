"""Waits remain Operation facts; no separate scheduler or question table."""

from sqlalchemy import String, and_, cast, func, or_, select

from short_drama.domain import AsyncTask
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation


class AgentWaitingDAO:
    def __init__(self, session):
        self.session = session

    def operation(self, run_id, scope_id, tool_call_id):
        return self.session.scalar(
            select(AgentOperation)
            .join(AgentToolCall, AgentToolCall.id == AgentOperation.tool_call_id)
            .where(
                AgentOperation.run_id == run_id,
                AgentOperation.scope_id == scope_id,
                AgentToolCall.provider_call_id == tool_call_id,
            )
            .with_for_update()
        )

    def pending_projections(self, run_ids):
        return self.session.execute(
            select(AgentOperation.run_id, AgentOperation.wait_state, AgentOperation.created_at)
            .join(AgentRun, AgentRun.id == AgentOperation.run_id)
            .where(
                AgentOperation.run_id.in_(run_ids),
                AgentRun.status.in_(("running", "waiting_input", "waiting_generation")),
                AgentRun.cancel_requested == 0,
                AgentOperation.error.is_(None),
                AgentOperation.wait_state.is_not(None),
                AgentOperation.wait_state["consumed"].as_boolean() == False,  # noqa: E712
                AgentOperation.wait_state["ready"].as_boolean() == False,  # noqa: E712
            )
        ).all()

    def for_run(self, run_id):
        return self.session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == run_id, AgentOperation.wait_state.is_not(None))
            .with_for_update()
        ).all()

    def candidates(self, limit):
        unfinished_media = (
            select(AsyncTask.id)
            .where(
                func.json_contains(
                    AgentOperation.wait_state["task_ids"],
                    func.json_quote(cast(AsyncTask.id, String)),
                )
                == 1,
                AsyncTask.status.not_in(("succeeded", "failed", "cancelled")),
            )
            .correlate(AgentOperation)
            .exists()
        )
        return self.session.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.status.in_(("waiting_input", "waiting_generation", "running")),
                AgentRun.message_status == "idle",
                AgentRun.cancel_requested == 0,
                AgentRun.lease_token.is_(None),
                AgentRun.id.in_(
                    select(AgentOperation.run_id).where(
                        AgentOperation.wait_state.is_not(None),
                        AgentOperation.wait_state["consumed"].as_boolean() == False,  # noqa: E712
                        AgentOperation.wait_state["interrupt_id"].as_string().is_not(None),
                        AgentOperation.error.is_(None),
                        or_(
                            AgentOperation.wait_state["ready"].as_boolean() == True,  # noqa: E712
                            and_(
                                AgentOperation.wait_state["kind"].as_string() == "generation",
                                ~unfinished_media,
                            ),
                        ),
                    )
                ),
            )
            .order_by(AgentRun.updated_at, AgentRun.id)
            .limit(limit)
        ).all()

    def questions(self, task_ids):
        return self.session.execute(
            select(
                AgentOperation,
                AgentRun.status,
                AgentRun.cancel_requested,
                AgentExecutionScope.parent_tool_call_id,
            )
            .join(AgentRun, AgentRun.id == AgentOperation.run_id)
            .join(AgentExecutionScope, AgentExecutionScope.id == AgentOperation.scope_id)
            .where(
                AgentOperation.task_id.in_(task_ids),
                AgentOperation.wait_state["kind"].as_string() == "user_input",
            )
            .order_by(AgentOperation.id)
        ).all()
