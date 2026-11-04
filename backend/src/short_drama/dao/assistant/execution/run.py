"""Explicit public Run projections; execution reads retain the full ORM record."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn


@dataclass(frozen=True)
class RunProjection:
    id: int
    task_id: int | None
    conversation_id: int
    status: str
    phase: str
    row_version: int
    model_config_id: int
    error: dict | None
    usage: dict
    budget: dict
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    checkpoint: dict
    config_snapshot: dict


class AgentRunDAO:
    def __init__(self, session: Session):
        self.session = session

    def execution_active(self, claim):
        from short_drama.domain.assistant.conversations.models import AgentConversation
        from short_drama.domain.assistant.tasks.task import AgentTask

        return (
            self.session.scalar(
                select(AgentRun.id)
                .join(AgentConversation, AgentConversation.id == AgentRun.conversation_id)
                .join(AgentTask, AgentTask.id == AgentRun.task_id)
                .where(
                    AgentRun.id == claim.run_id,
                    AgentRun.message_version == claim.version,
                    AgentRun.lease_token == claim.token,
                    AgentRun.cancel_requested == 0,
                    AgentRun.status == "running",
                    AgentConversation.status == "active",
                    AgentTask.control_state.not_in(("stop_pending", "delete_pending")),
                )
            )
            is not None
        )

    def stopping(self, run_ids):
        from short_drama.domain.assistant.conversations.models import AgentConversation
        from short_drama.domain.assistant.tasks.task import AgentTask

        return set(
            self.session.scalars(
                select(AgentRun.id)
                .join(AgentConversation, AgentConversation.id == AgentRun.conversation_id)
                .outerjoin(AgentTask, AgentTask.id == AgentRun.task_id)
                .where(
                    AgentRun.id.in_(run_ids),
                    AgentRun.status == "running",
                    (AgentRun.cancel_requested == 1)
                    | (AgentConversation.status == "deleting")
                    | AgentTask.control_state.in_(("stop_pending", "delete_pending")),
                )
            )
        )

    def latest_model(self, run_id, scope_id):
        return self.session.scalar(
            select(AgentTurn)
            .where(
                AgentTurn.run_id == run_id,
                AgentTurn.scope_id == scope_id,
                AgentTurn.usage["source"].as_string() == "assistant",
            )
            .order_by(AgentTurn.turn_no.desc())
            .limit(1)
        )

    def model_result(self, run_id, scope_id, signature):
        return self.session.scalar(
            select(AgentTurn)
            .where(
                AgentTurn.run_id == run_id,
                AgentTurn.scope_id == scope_id,
                AgentTurn.response["graph_input_hash"].as_string() == signature,
            )
            .order_by(AgentTurn.turn_no.desc())
            .limit(1)
        )

    def tool_call(self, run_id, scope_id, provider_call_id):
        return self.session.scalar(
            select(AgentToolCall)
            .where(
                AgentToolCall.run_id == run_id,
                AgentToolCall.scope_id == scope_id,
                AgentToolCall.provider_call_id == provider_call_id,
            )
            .with_for_update()
        )

    def projections(self, conversation_id: int, *, task_ids=None, run_ids=None, active=False):
        query = select(
            AgentRun.id,
            AgentRun.task_id,
            AgentRun.conversation_id,
            AgentRun.status,
            AgentRun.phase,
            AgentRun.row_version,
            AgentRun.model_config_id,
            AgentRun.error,
            AgentRun.usage,
            AgentRun.budget,
            AgentRun.created_at,
            AgentRun.updated_at,
            AgentRun.finished_at,
            AgentRun.checkpoint["authorization"]["mode"].as_string().label("mode"),
            AgentRun.checkpoint["awaiting_artifacts"].label("awaiting_artifacts"),
            AgentRun.checkpoint["model_connections"].label("model_connections"),
            AgentRun.checkpoint["recovery_states"].label("recovery_states"),
            AgentRun.config_snapshot["name"].as_string().label("model_name"),
        ).where(AgentRun.conversation_id == conversation_id)
        if task_ids is not None:
            query = query.where(AgentRun.task_id.in_(task_ids))
        if run_ids is not None:
            query = query.where(AgentRun.id.in_(run_ids))
        if active:
            query = query.where(
                AgentRun.status.in_(("queued", "running", "waiting_generation", "waiting_input"))
            )
        # JSON extraction can still make a MySQL filesort carry the source JSON.
        # Only IDs may be sorted/paginated in SQL; sort public rows after materialization.
        result = []
        for row in self.session.execute(query):
            values = dict(row._mapping)
            checkpoint = {
                "authorization": {"mode": values.pop("mode") or "discuss"},
                "awaiting_artifacts": values.pop("awaiting_artifacts") or [],
                "model_connections": values.pop("model_connections") or {},
                "recovery_states": values.pop("recovery_states") or {},
            }
            snapshot = {"name": values.pop("model_name") or "文本模型"}
            result.append(RunProjection(**values, checkpoint=checkpoint, config_snapshot=snapshot))
        return result

    def page_ids(self, conversation_id: int, offset: int, limit: int) -> list[int]:
        return list(
            self.session.scalars(
                select(AgentRun.id)
                .where(AgentRun.conversation_id == conversation_id)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    def queue(self, conversation_ids: set[int]):
        from short_drama.domain.assistant.tasks.task import AgentTask

        return self.session.execute(
            select(
                AgentRun.id,
                AgentRun.conversation_id,
                AgentRun.status,
                AgentRun.task_id,
                func.substr(AgentTask.goal, 1, 120).label("goal"),
            )
            .outerjoin(AgentTask, AgentTask.id == AgentRun.task_id)
            .where(
                AgentRun.conversation_id.in_(conversation_ids),
                AgentRun.status.in_(("queued", "running", "waiting_generation", "waiting_input")),
            )
        ).all()
