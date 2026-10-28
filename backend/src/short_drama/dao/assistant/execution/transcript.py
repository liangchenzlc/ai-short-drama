"""Read settled model/tool groups without HTTP snapshots or raw provider bodies."""

from sqlalchemy import select

from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput


class AgentTranscriptDAO:
    def __init__(self, session):
        self.session = session

    def latest_turn(self, run_id):
        identifier = self.session.scalar(
            select(AgentTurn.id)
            .where(AgentTurn.run_id == run_id)
            .order_by(AgentTurn.turn_no.desc())
            .limit(1)
        )
        return self.session.get(AgentTurn, identifier) if identifier is not None else None

    def input_messages(self, task_id, revision, identifiers):
        return self.session.execute(
            select(AgentMessage.id, AgentMessage.content, AgentTaskInput.input_revision)
            .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
            .where(
                AgentTaskInput.task_id == task_id,
                AgentTaskInput.input_revision <= revision,
                AgentTaskInput.applied_plan_revision.is_not(None),
                AgentMessage.role == "user",
                AgentMessage.id.in_(identifiers),
            )
        ).all()

    def task_input_messages(self, task_id, revision, limit=32):
        """All applied user inputs, newest first; used for authoritative quotes."""
        return self.session.execute(
            select(AgentMessage.id, AgentMessage.content, AgentTaskInput.input_revision)
            .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
            .where(
                AgentTaskInput.task_id == task_id,
                AgentTaskInput.input_revision <= revision,
                AgentTaskInput.applied_plan_revision.is_not(None),
                AgentMessage.role == "user",
            )
            .order_by(AgentTaskInput.input_revision.desc())
            .limit(limit)
        ).all()

    def evidence(self, task_id, identifiers):
        return self.session.execute(
            select(
                AgentOperation.id,
                AgentOperation.status,
                AgentOperation.tool_name,
                AgentOperation.result_refs,
                AgentOperation.result,
                AgentOperation.changes,
                AgentToolCall.turn_id,
            )
            .join(AgentToolCall, AgentToolCall.id == AgentOperation.tool_call_id)
            .where(AgentOperation.task_id == task_id, AgentOperation.id.in_(identifiers))
        ).all()

    def settled_turns(
        self,
        run_id,
        *,
        after_id=0,
        task_id=None,
        pinned_operations=(),
        include_task_history=False,
        turn_ids=None,
        extra_turn_ids=(),
        limit=64,
    ):
        pinned_turns = (
            select(AgentToolCall.turn_id)
            .join(AgentOperation, AgentOperation.tool_call_id == AgentToolCall.id)
            .where(AgentOperation.task_id == task_id, AgentOperation.id.in_(pinned_operations))
        )
        scope = AgentTurn.run_id == run_id
        if include_task_history:
            scope = AgentTurn.run_id.in_(
                select(AgentRun.id).where(AgentRun.task_id == task_id, AgentRun.id <= run_id)
            )
        eligible = (
            (scope & (AgentTurn.id > after_id)) | AgentTurn.id.in_(pinned_turns),
            AgentTurn.status == "succeeded",
        )
        if turn_ids is not None:
            ids = self.session.scalars(
                select(AgentTurn.id).where(*eligible, AgentTurn.id.in_(turn_ids))
            ).all()
        else:
            recent = self.session.scalars(
                select(AgentTurn.id)
                .where(*eligible, scope, AgentTurn.id > after_id)
                .order_by(AgentTurn.id.desc())
                .limit(limit)
            ).all()
            extra = self.session.scalars(
                select(AgentTurn.id).where(
                    *eligible, AgentTurn.id.in_(extra_turn_ids) | AgentTurn.id.in_(pinned_turns)
                )
            ).all()
            ids = [*recent, *extra]
        ids = sorted(set(ids))
        if not ids:
            return []
        rows = self.session.execute(
            select(
                AgentTurn.id,
                AgentTurn.run_id,
                AgentTurn.response["normalized"].label("normalized"),
                AgentTurn.response["applied"].as_boolean().label("applied"),
                AgentTurn.response["internal"].as_boolean().label("internal"),
            ).where(AgentTurn.id.in_(ids))
        ).all()
        by_id = {
            row.id: (row.run_id, row.normalized)
            for row in rows
            if row.applied is True and row.internal is not True
        }
        return [(identifier, *by_id[identifier]) for identifier in ids if identifier in by_id]

    def operations(self, task_id, turn_ids):
        return list(
            self.session.execute(
                select(
                    AgentToolCall.turn_id,
                    AgentToolCall.provider_call_id,
                    AgentOperation.id,
                    AgentOperation.tool_name,
                    AgentOperation.status,
                    AgentOperation.result,
                    AgentOperation.error,
                )
                .join(AgentOperation, AgentOperation.tool_call_id == AgentToolCall.id)
                .where(
                    AgentToolCall.turn_id.in_(turn_ids),
                    AgentOperation.task_id == task_id,
                )
            ).all()
        )

    def operation_result(self, task_id, operation_id):
        return self.session.scalar(
            select(AgentOperation.result).where(
                AgentOperation.task_id == task_id,
                AgentOperation.id == operation_id,
            )
        )
