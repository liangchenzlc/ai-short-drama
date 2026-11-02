"""Waits remain Operation facts; no separate scheduler or question table."""

from sqlalchemy import select

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

    def for_run(self, run_id):
        return self.session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == run_id, AgentOperation.wait_state.is_not(None))
            .with_for_update()
        ).all()

    def candidates(self, limit):
        return self.session.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.status.in_(("waiting_input", "waiting_generation", "running")),
                AgentRun.id.in_(
                    select(AgentOperation.run_id).where(
                        AgentOperation.wait_state.is_not(None),
                        AgentOperation.wait_state["consumed"].as_boolean() == False,  # noqa: E712
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
