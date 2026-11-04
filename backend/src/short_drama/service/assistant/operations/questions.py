"""Authenticated answers are real user messages targeted to an existing wait."""

from dataclasses import dataclass

from short_drama.agent.core.access import lock_run, may_decide
from short_drama.agent.core.events import TERMINAL, append_event
from short_drama.agent.core.items import allocate_item_seq
from short_drama.agent.runtime.events import operation_block_locked
from short_drama.agent.task.queries import task_for_run
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_session_actor
from short_drama.core.time import utcnow
from short_drama.dao.assistant.execution.waiting import AgentWaitingDAO
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.schemas.assistant.operations.questions import (
    AskUserAnswer,
    AskUserRequest,
    PendingQuestion,
    QuestionAnswerReceipt,
    validate_answer,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.execution.waiting import bound_run, schedule_ready_locked
from short_drama.service.assistant.memory.memory_store import memory_capture_generation
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


@dataclass(frozen=True)
class QuestionWait:
    operation_id: int
    question_version: int


class QuestionService:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    def register(self, run_id, scope_id, tool_call_id, request: AskUserRequest, *, fence):
        with self.session.begin():
            conversation, _task, run = bound_run(self.session, run_id, fence)
            operation = AgentWaitingDAO(self.session).operation(run.id, scope_id, tool_call_id)
            if operation is None or operation.tool_name != "runtime__ask_user":
                raise NotFound("问题调用不存在")
            if operation.wait_state is None:
                operation.arguments = request.model_dump(mode="json")
                operation.wait_state = {
                    "kind": "user_input",
                    "scope_id": str(scope_id),
                    "tool_call_id": str(operation.tool_call_id),
                    "question_version": "1",
                    "requires_interrupt": True,
                    "ready": False,
                    "consumed": False,
                }
                operation.status, operation.phase = "waiting_input", "waiting_input"
                operation_block_locked(self.session, conversation, run, operation)
                self.session.get(AgentToolCall, operation.tool_call_id).status = "waiting_input"
                append_event(
                    self.session,
                    conversation,
                    "question.requested",
                    {
                        "operation_id": str(operation.id),
                        "scope_id": str(scope_id),
                        "question_version": "1",
                        "request": operation.arguments,
                    },
                    run_id=run.id,
                )
            return QuestionWait(operation.id, int(operation.wait_state["question_version"]))

    def read_answer(self, operation_id, *, fence):
        with self.session.begin():
            operation = self.session.get(AgentOperation, operation_id)
            if operation is None:
                raise NotFound("问题不存在")
            bound_run(self.session, operation.run_id, fence)
            state = operation.wait_state
            if not state or state["kind"] != "user_input" or not state.get("answer"):
                raise Conflict("问题尚未回答")
            return {
                **state["answer"],
                "answer_message_id": state["answer_message_id"],
                "source_message_ids": [state["answer_message_id"]],
            }

    def answer(self, operation_id, values, *, idempotency_key):
        answer = (
            values if isinstance(values, AskUserAnswer) else AskUserAnswer.model_validate(values)
        )
        with self.session.begin():
            actor = require_session_actor(self.session.info.get("actor"))
            operation = self.session.get(AgentOperation, parse_identifier(operation_id))
            if operation is None:
                raise NotFound("问题不存在")
            rows = lock_run(self.session, operation.run_id)
            if rows is None or rows[1].owner_user_id != actor.user_id:
                raise NotFound("问题不存在")
            project, conversation, run = rows
            self.session.refresh(operation, with_for_update=True)
            state = operation.wait_state
            if not state or state["kind"] != "user_input":
                raise NotFound("问题不存在")
            payload = answer.model_dump(mode="json")
            identity = digest([str(operation.id), idempotency_key])
            request_hash = digest(payload)
            # 已成功受理的请求先于版本、待答和 Run 终态检查，保留终态后的可靠重试。
            if state.get("answer_hash"):
                if state["answer_hash"] != request_hash:
                    raise Conflict("问题已有答案，不能覆盖")
                return QuestionAnswerReceipt.model_validate(state["answer_receipt"])
            task = task_for_run(self.session, run)
            if (
                run.status in TERMINAL
                or run.cancel_requested
                or task.control_state == "stop_pending"
                or operation.finished_at is not None
                or not may_decide(self.session, project, conversation, run)
            ):
                raise Conflict("问题已停止或解决")
            if str(answer.question_version) != state["question_version"]:
                raise Conflict("问题版本已变化")
            try:
                request = AskUserRequest.model_validate(operation.arguments)
                validate_answer(request, answer)
            except ValueError as error:
                raise WorkflowError("assistant_answer_invalid", str(error), 422) from None
            now = utcnow()
            message = AgentMessage(
                id=next_id(),
                conversation_id=conversation.id,
                task_id=task.id,
                seq=conversation.next_message_seq,
                item_seq=allocate_item_seq(conversation),
                role="user",
                kind="tool_answer",
                memory_capture_generation=memory_capture_generation(
                    self.session, conversation.owner_user_id
                ),
                content=answer_text(request, answer),
                references=[
                    {
                        "type": "question_answer",
                        "operation_id": str(operation.id),
                        "scope_id": str(operation.scope_id),
                        "answered_by": str(actor.user_id),
                    }
                ],
                artifacts=[],
                created_at=now,
            )
            conversation.next_message_seq += 1
            self.session.add(message)
            self.session.flush()
            append_event(
                self.session,
                conversation,
                "message.created",
                {
                    "id": str(message.id),
                    "kind": "tool_answer",
                    "role": "user",
                    "content": message.content,
                    "operation_id": str(operation.id),
                    "scope_id": str(operation.scope_id),
                    "seq": message.seq,
                    "item_seq": message.item_seq,
                },
                run_id=run.id,
            )
            append_event(
                self.session,
                conversation,
                "question.answered",
                {
                    "operation_id": str(operation.id),
                    "scope_id": str(operation.scope_id),
                    "answer_message_id": str(message.id),
                    "outcome": answer.outcome,
                },
                run_id=run.id,
            )
            append_event(
                self.session,
                conversation,
                "wait.ready",
                {
                    "operation_id": str(operation.id),
                    "scope_id": str(operation.scope_id),
                },
                run_id=run.id,
            )
            receipt = QuestionAnswerReceipt(
                operation_id=operation.id,
                answer_message_id=message.id,
                run_id=run.id,
                cursor=conversation.next_event_seq - 1,
            )
            operation.wait_state = {
                **state,
                "ready": True,
                "answer": payload,
                "answer_message_id": str(message.id),
                "answered_by": str(actor.user_id),
                "answered_at": now.isoformat(),
                "answer_hash": request_hash,
                "answer_request_identity": identity,
                "answer_receipt": receipt.model_dump(mode="json"),
            }
            schedule_ready_locked(self.session, conversation, task, run)
            return receipt


def answer_text(request: AskUserRequest, answer: AskUserAnswer) -> str:
    if answer.outcome == "skipped":
        return f"已跳过：{request.title}"
    lines = []
    for field in request.fields:
        value = answer.values.get(field.key)
        selected = [value] if isinstance(value, str) else value or []
        if selected:
            labels = {item.value: item.label for item in field.options}
            lines.append(f"{field.label}：{'、'.join(labels.get(item, item) for item in selected)}")
    if answer.additional_text:
        lines.append(answer.additional_text)
    return "\n".join(lines) or f"已回答：{request.title}"


def pending_questions(session, task_ids):
    result = []
    for operation, run_status, cancel_requested, parent_call in AgentWaitingDAO(session).questions(
        task_ids
    ):
        state = operation.wait_state
        cancelled = operation.error and operation.error.get("code") == "agent_cancelled"
        status = "cancelled" if cancelled else state.get("answer", {}).get("outcome", "pending")
        can_answer = status == "pending" and run_status not in TERMINAL and not cancel_requested
        result.append(
            PendingQuestion(
                operation_id=operation.id,
                run_id=operation.run_id,
                scope_id=operation.scope_id,
                parent_tool_call_id=parent_call,
                question_version=state["question_version"],
                request=operation.arguments if can_answer else None,
                status=status,
                can_answer=can_answer,
                answer_message_id=state.get("answer_message_id"),
                answered_at=state.get("answered_at"),
            )
        )
    return result
