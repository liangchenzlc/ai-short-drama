from sqlalchemy import select

from short_drama.agent.task.controls import apply_controls_locked
from short_drama.agent.task.state import (
    read_task,
    task_capabilities,
    task_event,
)
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.tasks.task import (
    AgentTaskControl,
)
from short_drama.schemas.assistant.tasks.task import (
    TaskControlAccepted,
    TaskControlRequest,
)
from short_drama.service.assistant.context.attachment import attachment_key
from short_drama.service.assistant.execution.run import read_run
from short_drama.service.assistant.tasks.base import TaskServiceBase
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class TaskControlOperations(TaskServiceBase):
    def control_task(self, identifier, payload, idempotency_key):
        values = TaskControlRequest.model_validate(self._payload(TaskControlRequest, payload))
        serialized = values.model_dump(mode="json")
        key, request_hash = attachment_key(idempotency_key), digest(serialized)
        dao = AgentTaskDAO(self.session)
        with self._transaction():
            conversation, task = self._task(identifier)
            existing = self.session.scalar(
                select(AgentTaskControl)
                .where(AgentTaskControl.task_id == task.id, AgentTaskControl.request_key == key)
                .with_for_update()
            )
            run = dao.latest_run(task.id)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise Conflict("此请求身份已经用于其他控制操作")
                return self._control_accepted(conversation, task, run, existing)
            if task.status == "succeeded":
                raise WorkflowError("assistant_task_finished", "任务已完成，不能停止", 409)
            capabilities = task_capabilities(task)
            if not capabilities["stop"] or not getattr(
                self.settings, "agent_stop_at_boundary", True
            ):
                raise WorkflowError("assistant_stop_unavailable", "当前任务暂不支持停止", 409)
            if run.lease_token is None:
                from short_drama.service.assistant.execution.interruptions import (
                    settle_interrupted_locked,
                )

                settle_interrupted_locked(self.session, conversation, run, "agent_cancelled")
            active = dao.active_operations(task.id)
            task.control_seq += 1
            task.row_version += 1
            control = AgentTaskControl(
                id=next_id(),
                task_id=task.id,
                seq=task.control_seq,
                kind=values.kind,
                request_key=key,
                request_hash=request_hash,
                expected_control_seq=None,
                source_message_id=None,
                requested_after_operation_id=active[-1].id if active else None,
                created_at=utcnow(),
            )
            self.session.add(control)
            task.control_state = "stop_pending"
            self.session.flush()
            task_event(
                self.session,
                conversation,
                task,
                "control.requested",
                run=run,
                control_id=str(control.id),
                control_seq=str(control.seq),
                kind=values.kind,
            )
            apply_controls_locked(
                self.session,
                conversation,
                task,
                run,
            )
            self.session.flush()
            return self._control_accepted(conversation, task, dao.latest_run(task.id), control)

    def _control_accepted(self, conversation, task, run, control):
        return TaskControlAccepted(
            control_id=control.id,
            task=read_task(self.session, task),
            run=read_run(self.session, run),
            cursor=conversation.next_event_seq - 1,
        )
