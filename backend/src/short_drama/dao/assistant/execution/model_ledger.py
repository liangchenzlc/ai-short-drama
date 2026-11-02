"""Per-scope model receipt queries under the caller's parent Run lock."""

from sqlalchemy import and_, func, or_, select

from short_drama.domain.assistant.execution.models import AgentTurn


class AgentModelLedgerDAO:
    def __init__(self, session, run_id):
        self.session, self.run_id = session, run_id

    def unresolved(self, scope_id):
        return self.session.scalar(
            select(AgentTurn.id)
            .where(
                AgentTurn.run_id == self.run_id,
                or_(
                    AgentTurn.status == "unknown",
                    and_(AgentTurn.status == "sent", AgentTurn.scope_id == scope_id),
                ),
            )
            .limit(1)
        )

    def next_number(self, scope_id):
        return (
            self.session.scalar(
                select(func.max(AgentTurn.turn_no)).where(
                    AgentTurn.run_id == self.run_id,
                    AgentTurn.scope_id == scope_id,
                )
            )
            or 0
        ) + 1

    def call(self, model_call_id):
        return self.session.scalar(
            select(AgentTurn)
            .where(
                AgentTurn.run_id == self.run_id,
                AgentTurn.usage["model_call_id"].as_string() == model_call_id,
            )
            .with_for_update()
        )
