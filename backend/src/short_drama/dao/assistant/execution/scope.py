"""Scope queries inherit the caller's Run lock and never commit."""

from sqlalchemy import select

from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope


class AgentExecutionScopeDAO:
    def __init__(self, session, *, owner_id, run_id):
        self.session = session
        self.owner_id, self.run_id = owner_id, run_id

    def get(self, *, scope_id=None, scope_key=None):
        model = AgentExecutionScope
        query = select(model).where(
            model.owner_user_id == self.owner_id, model.run_id == self.run_id
        )
        if scope_id is not None:
            query = query.where(model.id == scope_id)
        if scope_key is not None:
            query = query.where(model.scope_key == scope_key)
        return self.session.scalar(query.with_for_update())

    def workspaces(self):
        model = AgentExecutionScope
        return self.session.scalars(
            select(model)
            .where(
                model.owner_user_id == self.owner_id,
                model.run_id == self.run_id,
                model.workspace_volume.is_not(None),
                model.sandbox_status != "closed",
            )
            .with_for_update()
        ).all()


def terminal_workspace_run_ids(session, *, statuses, limit):
    return session.scalars(
        select(AgentExecutionScope.run_id)
        .join(AgentRun, AgentRun.id == AgentExecutionScope.run_id)
        .where(
            AgentRun.status.in_(statuses),
            AgentRun.lease_token.is_(None),
            AgentExecutionScope.workspace_volume.is_not(None),
            AgentExecutionScope.sandbox_status != "closed",
        )
        .distinct()
        .limit(limit)
    ).all()
