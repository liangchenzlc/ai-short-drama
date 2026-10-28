from sqlalchemy import select

from short_drama.agent.core.access import model_unchanged
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.task.policy import new_policy_from_settings
from short_drama.agent.task.state import (
    close_unstarted_calls,
    new_attempt_locked,
    read_task,
    task_event,
)
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.tasks.task import (
    AgentTask,
)
from short_drama.schemas.assistant.tasks.task import (
    AssistantTaskMessage,
    TaskMessageAccepted,
)
from short_drama.service.ai_config.management.assistant_models import (
    AgentModelService,
    model_snapshot,
)
from short_drama.service.assistant.context.attachment import attachment_key
from short_drama.service.assistant.execution.run import read_run
from short_drama.service.assistant.tasks.base import TaskServiceBase
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class TaskMessageOperations(TaskServiceBase):
    def send_task_message(self, identifier, payload, idempotency_key):
        inputs = AssistantTaskMessage.model_validate(self._payload(AssistantTaskMessage, payload))
        key, request_hash = attachment_key(idempotency_key), digest(inputs.model_dump(mode="json"))
        dao = AgentTaskDAO(self.session)
        with self._transaction():
            conversation = self._writable(identifier)
            existing = self.session.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.idempotency_key == key,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.request_hash != request_hash or existing.task_id is None:
                    raise Conflict("此请求身份已经用于其他消息")
                task = dao.task(existing.task_id, lock=True)
                run = dao.latest_run(task.id)
                return self._accepted(conversation, task, run, existing)
            if inputs.task_id is not None:
                task = dao.task(inputs.task_id, conversation_id=conversation.id, lock=True)
                if task is None:
                    raise NotFound("任务不存在或不属于此对话")
                if task.status == "succeeded":
                    raise WorkflowError(
                        "assistant_task_finished", "此任务已完成，请发起新任务", 409
                    )
            else:
                task = None if inputs.new_task else dao.selected_task(conversation.id)
            inputs = self._project_inputs(conversation, task, inputs)
            previous = dao.latest_run(task.id) if task else None
            if task is None:
                model = AgentModelService(self.session, self.settings).select_model(
                    inputs.model_config_id
                )
                snapshot = model_snapshot(model)
                try:
                    from short_drama.agent.gateway.langchain import select_protocol

                    select_protocol(snapshot)
                except AgentGatewayError:
                    raise WorkflowError(
                        "unsupported_assistant_protocol", "此模型协议不支持创作助手", 422
                    ) from None
                now = utcnow()
                task = AgentTask(
                    id=next_id(),
                    conversation_id=conversation.id,
                    goal=inputs.content,
                    status="queued",
                    phase="at_boundary",
                    row_version=1,
                    plan_revision=1,
                    input_revision=0,
                    applied_input_revision=0,
                    control_seq=0,
                    applied_control_seq=0,
                    control_state="none",
                    execution_target=None,
                    memory_project_ids=[],
                    context_snapshot=None,
                    selected_skills=[],
                    effective_state={
                        "engine": ENGINE_VERSION,
                        "agent_policy": new_policy_from_settings(self.settings),
                    },
                    media_limits=inputs.media_limits.model_dump(),
                    media_usage={},
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(task)
                self.session.flush()
            else:
                model, snapshot = None, previous.config_snapshot
                if previous.checkpoint.get("discussion_revision"):
                    raise WorkflowError(
                        "assistant_discussion_readonly",
                        "历史重答只处理原讨论，请使用独立新任务发送新要求",
                        409,
                    )
                if (
                    inputs.model_config_id is not None
                    and inputs.model_config_id != previous.model_config_id
                ):
                    raise WorkflowError(
                        "assistant_model_frozen", "当前任务使用已固定的模型，请新建任务切换", 409
                    )
            continuing = (
                previous is not None
                and task.effective_state.get("engine") == ENGINE_VERSION
                and task.status in {"stopped", "paused_error", "paused_budget"}
            )
            if previous is not None and task.control_state == "stop_pending":
                raise WorkflowError(
                    "assistant_task_stopping", "正在停止当前操作，请等待结算后发送补充要求", 409
                )
            if continuing:
                if previous.status not in {"succeeded", "failed", "cancelled"}:
                    raise WorkflowError(
                        "assistant_task_settling", "当前回复尚未结算，请稍后继续", 409
                    )
                if not model_unchanged(self.session, previous):
                    raise WorkflowError(
                        "assistant_model_changed",
                        "当前任务的模型配置已被修改，请新建任务后继续。已有成果仍然保留。",
                        409,
                    )
                if dao.active_operation(task.id) is not None:
                    raise Conflict("当前操作尚未结算，请先核对原任务状态")
            message = self._input_locked(conversation, task, inputs, key, request_hash, snapshot)
            if task.effective_state.get("engine") == ENGINE_VERSION:
                from short_drama.agent.task.requirements import seed_requirements_locked

                seed_requirements_locked(self.session, task, message)
            if continuing:
                close_unstarted_calls(self.session, previous)
                run = new_attempt_locked(
                    self.session,
                    conversation,
                    task,
                    message,
                    previous.model_config_id,
                    snapshot,
                    previous,
                )
                task_event(self.session, conversation, task, "task.resumed", run=run)
            else:
                run = previous or new_attempt_locked(
                    self.session, conversation, task, message, model.id, snapshot
                )
            self.session.flush()
            return self._accepted(conversation, task, run, message)

    def _accepted(self, conversation, task, run, message):
        return TaskMessageAccepted(
            message=self._message_read(message),
            task=read_task(self.session, task),
            run=read_run(self.session, run),
            cursor=conversation.next_event_seq - 1,
        )
