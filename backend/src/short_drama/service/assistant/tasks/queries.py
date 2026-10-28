from sqlalchemy import func, select

from short_drama.agent.task.state import (
    read_operations,
    read_task,
)
from short_drama.core.exceptions import NotFound
from short_drama.dao.assistant.execution.run import AgentRunDAO
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.conversations.models import AgentEvent, AgentMessage
from short_drama.domain.assistant.tasks.task import (
    AgentOperation,
    AgentTaskInput,
)
from short_drama.schemas.assistant.tasks.task import (
    OperationDetailRead,
    OperationPage,
    TaskRuntimeState,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.execution.run import read_runs
from short_drama.service.assistant.tasks.base import TaskServiceBase


class TaskQueryOperations(TaskServiceBase):
    def task_runtime_state(self, identifier, *, before_task_id=None, limit=20):
        self.dao.validate_pagination(0, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            dao = AgentTaskDAO(self.session)
            ids = dao.page_ids(
                conversation.id,
                before_id=parse_identifier(before_task_id) if before_task_id else None,
                limit=limit + 1,
            )
            task_ids = ids[:limit]
            selected = dao.projections(conversation.id, task_ids) if task_ids else []
            operations = dao.operation_projections(task_ids, limit=201) if task_ids else []
            active = {row.task_id: row for row in dao.operation_projections(task_ids, active=True)}
            runs = (
                sorted(
                    AgentRunDAO(self.session).projections(conversation.id, task_ids=task_ids),
                    key=lambda run: run.id,
                )
                if task_ids
                else []
            )
            messages = self.session.scalars(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation.id)
                .order_by(AgentMessage.seq.desc())
                .limit(50)
            ).all()
            cursor = conversation.next_event_seq - 1
            first_delta = self.session.scalar(
                select(func.min(AgentEvent.seq)).where(
                    AgentEvent.conversation_id == conversation.id,
                    AgentEvent.run_id.in_(
                        [run.id for run in runs if run.status in {"queued", "running"}]
                    ),
                    AgentEvent.event_type == "assistant.delta",
                    AgentEvent.seq <= cursor,
                )
            )
            inputs = (
                self.session.execute(
                    select(
                        AgentTaskInput.task_id,
                        AgentTaskInput.message_id,
                        AgentTaskInput.input_revision,
                        AgentTaskInput.applied_plan_revision,
                    )
                    .where(AgentTaskInput.task_id.in_(task_ids))
                    .order_by(AgentTaskInput.task_id, AgentTaskInput.input_revision)
                ).all()
                if task_ids
                else []
            )
            return TaskRuntimeState(
                conversation_id=conversation.id,
                snapshot_cursor=cursor,
                resume_cursor=first_delta - 1 if first_delta is not None else cursor,
                messages={
                    "items": [self._message_read(message) for message in reversed(messages)],
                    "total": self.session.scalar(
                        select(func.count())
                        .select_from(AgentMessage)
                        .where(AgentMessage.conversation_id == conversation.id)
                    ),
                    "offset": 0,
                    "limit": 50,
                },
                runs=read_runs(self.session, runs),
                tasks=[
                    read_task(self.session, task, active_operation=active.get(task.id))
                    for task in selected
                ],
                operations=read_operations(self.session, operations[:200]),
                inputs=[
                    {
                        "task_id": str(item.task_id),
                        "message_id": str(item.message_id),
                        "input_revision": str(item.input_revision),
                        "applied_plan_revision": (
                            str(item.applied_plan_revision) if item.applied_plan_revision else None
                        ),
                    }
                    for item in inputs
                ],
                has_more_tasks=len(ids) > limit,
                has_more_operations=len(operations) > 200,
            )

    def operation_page(self, identifier, *, after_id=None, limit=50):
        self.dao.validate_pagination(0, limit)
        with self._transaction(read_only=True):
            task = AgentTaskDAO(self.session).task(parse_identifier(identifier))
            if task is None:
                raise NotFound("任务不存在")
            conversation = self._conversation(task.conversation_id)
            rows = AgentTaskDAO(self.session).operation_projections(
                [task.id],
                after_id=parse_identifier(after_id) if after_id else None,
                limit=limit + 1,
            )
            return OperationPage(
                items=read_operations(self.session, rows[:limit]),
                has_more=len(rows) > limit,
                next_after_id=rows[limit - 1].id if len(rows) > limit else None,
                snapshot_cursor=conversation.next_event_seq - 1,
            )

    def operation_detail(self, identifier):
        with self._transaction(read_only=True):
            row = self.session.get(AgentOperation, parse_identifier(identifier))
            if row is None:
                raise NotFound("操作不存在")
            task = AgentTaskDAO(self.session).task(row.task_id)
            self._conversation(task.conversation_id)
            return OperationDetailRead(
                **read_operations(self.session, [row])[0].model_dump(), result=row.result
            )

    def script_proposal(self, operation_id):
        from short_drama.service.assistant.operations.script import preview

        return preview(self, operation_id)

    def apply_script_proposal(self, operation_id, payload):
        from short_drama.service.assistant.operations.script import apply

        return apply(self, operation_id, payload)

    def revise_discussion(self, identifier, message_id, payload, idempotency_key):
        from short_drama.service.assistant.conversations.history import revise_discussion

        return revise_discussion(self, identifier, message_id, payload, idempotency_key)

    def discussion_eligibility(self, identifier, message_id):
        from short_drama.service.assistant.conversations.history import eligibility

        return eligibility(self, identifier, message_id)

    def discussion_revisions(self, identifier, message_id, offset=0, limit=30):
        from short_drama.service.assistant.conversations.history import revisions

        return revisions(self, identifier, message_id, offset, limit)

    def undo_preview(self, identifier):
        from short_drama.service.assistant.undo.undo import preview

        return preview(self, identifier)

    def undo_batch(self, identifier, payload, idempotency_key):
        from short_drama.service.assistant.undo.undo import apply

        return apply(self, identifier, payload, idempotency_key)
