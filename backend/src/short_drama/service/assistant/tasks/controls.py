from sqlalchemy import select

from short_drama.agent.core.access import model_unchanged
from short_drama.agent.core.items import KIND_REPLY, allocate_item_seq
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.task.controls import apply_controls_locked
from short_drama.agent.task.state import (
    read_task,
    task_capabilities,
    task_event,
)
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.tasks.task import RESUMABLE_TASKS, AgentTaskDAO
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.tasks.task import (
    AgentTaskControl,
    AgentTaskInput,
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
        serialized = values.model_dump(
            mode="json", exclude={"message_id"} if values.message_id is None else set()
        )
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
            if task.effective_state.get("engine") == ENGINE_VERSION:
                capabilities = task_capabilities(task)
                if values.kind == "stop":
                    enabled = capabilities["stop"] and getattr(
                        self.settings, "agent_stop_at_boundary", True
                    )
                    if not enabled:
                        raise WorkflowError(
                            "assistant_stop_unavailable", "当前任务暂不支持停止", 409
                        )
                elif values.kind == "redirect":
                    raise WorkflowError(
                        "assistant_redirect_unavailable",
                        "当前任务不支持改向；请直接发送补充要求，将在下一段生效",
                        409,
                    )
                else:
                    raise WorkflowError(
                        "assistant_resume_unavailable",
                        "暂停或停止后请直接发送消息继续执行",
                        409,
                    )
            # 恢复会复制当前 run 的模型快照；配置已变更时提前拒绝，避免生成必然失败的 run。
            if (
                values.kind == "resume"
                and run is not None
                and not model_unchanged(self.session, run)
            ):
                raise WorkflowError(
                    "assistant_model_changed",
                    "当前任务的模型配置已被修改，请新建任务后继续。已有输入和候选仍然保留。",
                    409,
                )
            if run.checkpoint.get("discussion_revision") and (
                values.kind == "redirect" or values.content is not None
            ):
                raise WorkflowError(
                    "assistant_discussion_readonly", "历史重答不能改向执行，请发送独立新任务", 409
                )
            if task.status == "succeeded":
                raise WorkflowError("assistant_task_finished", "任务已经完成", 409)
            if values.kind == "resume":
                if values.expected_control_seq != task.control_seq:
                    # 已在回执查重之后确认版本过期；客户端可清理本次恢复记录，不能越过新停止。
                    raise WorkflowError(
                        "assistant_control_changed",
                        "停止或暂停状态已变化，请核对当前任务后继续",
                        409,
                    )
                if task.status not in RESUMABLE_TASKS and task.control_state != "stop_pending":
                    raise WorkflowError("assistant_task_not_paused", "任务尚未停止或暂停", 409)
            source = None
            if values.message_id is not None:
                source = dao.input_message(task.id, values.message_id)
                if source is None:
                    raise NotFound("消息不存在或不属于此任务")
            if source is None and (values.content is not None or values.kind == "resume"):
                source = AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    task_id=task.id,
                    seq=conversation.next_message_seq,
                    item_seq=allocate_item_seq(conversation),
                    role="user",
                    kind=KIND_REPLY,
                    content=values.content or "继续任务",
                    references=[],
                    artifacts=[],
                    created_at=utcnow(),
                )
                conversation.next_message_seq += 1
                self.session.add(source)
                self.session.flush()
                if values.content is not None:
                    task.input_revision += 1
                    self.session.add(
                        AgentTaskInput(
                            id=next_id(),
                            task_id=task.id,
                            message_id=source.id,
                            input_revision=task.input_revision,
                            payload={},
                            created_at=utcnow(),
                        )
                    )
            active = dao.active_operation(task.id)
            task.control_seq += 1
            task.row_version += 1
            control = AgentTaskControl(
                id=next_id(),
                task_id=task.id,
                seq=task.control_seq,
                kind=values.kind,
                request_key=key,
                request_hash=request_hash,
                expected_control_seq=values.expected_control_seq,
                source_message_id=source.id if source else None,
                requested_after_operation_id=active.id if active else None,
                created_at=utcnow(),
            )
            self.session.add(control)
            if values.kind == "stop":
                task.control_state = "stop_pending"
            elif values.kind == "redirect" and task.control_state != "stop_pending":
                task.control_state = "redirect_pending"
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
            apply_controls_locked(self.session, conversation, task, run)
            self.session.flush()
            return self._control_accepted(conversation, task, dao.latest_run(task.id), control)

    def _control_accepted(self, conversation, task, run, control):
        return TaskControlAccepted(
            control_id=control.id,
            task=read_task(self.session, task),
            run=read_run(self.session, run),
            cursor=conversation.next_event_seq - 1,
        )
