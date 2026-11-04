"""Per-scope model receipt queries under the caller's parent Run lock."""

from sqlalchemy import and_, func, or_, select

from short_drama.domain.assistant.execution.models import AgentTurn


class AgentModelLedgerDAO:
    def __init__(self, session, run_id):
        self.session, self.run_id = session, run_id

    def has_uncertain_request(self):
        return (
            self.session.scalar(
                select(AgentTurn.id)
                .where(
                    AgentTurn.run_id == self.run_id,
                    AgentTurn.status.in_(("sent", "unknown")),
                    func.coalesce(AgentTurn.usage["source"].as_string(), "")
                    != "conversation_title",
                )
                .limit(1)
            )
            is not None
        )

    def unresolved(self, scope_id, *, source):
        title_source = (
            func.coalesce(AgentTurn.usage["source"].as_string(), "") == "conversation_title"
        )
        same_kind = title_source if source == "conversation_title" else ~title_source
        return self.session.scalar(
            select(AgentTurn.id)
            .where(
                AgentTurn.run_id == self.run_id,
                or_(
                    and_(AgentTurn.status == "unknown", ~title_source),
                    and_(AgentTurn.status == "sent", AgentTurn.scope_id == scope_id, same_kind),
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

    def graph_fragments(self, scope_id, call_ids):
        return list(
            self.session.scalars(
                select(AgentTurn).where(
                    AgentTurn.run_id == self.run_id,
                    AgentTurn.scope_id == scope_id,
                    AgentTurn.usage["source"].as_string() == "assistant",
                    AgentTurn.usage["model_call_id"].as_string().in_(call_ids),
                )
            )
        )
