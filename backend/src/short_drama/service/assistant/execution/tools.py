"""Durable model/tool receipts; graph control remains entirely with LangChain."""

import json
from copy import deepcopy

from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import RemoveMessage, ToolMessage, message_to_dict, messages_from_dict
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command
from sqlalchemy import select

from short_drama.agent.context.delegation import delegated_inputs_locked
from short_drama.agent.context.delegation_results import business_outputs_locked
from short_drama.agent.context.subjects import subject_memory_keys
from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
from short_drama.agent.core.events import append_event, initial_usage
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.core.items import allocate_item_seq
from short_drama.agent.runtime.context import model_compaction
from short_drama.agent.runtime.events import operation_block_locked
from short_drama.agent.runtime.trace_content import display_safe
from short_drama.agent.task.queries import task_for_run
from short_drama.agent.task.sources import READ_TOOLS, WRITE_TOOLS, result_source_ids
from short_drama.agent.task.state import apply_inputs_locked, task_event
from short_drama.agent.tools.contracts import TOOL_VERSION
from short_drama.agent.tools.presentation import label, visibility
from short_drama.core.exceptions import WorkflowError
from short_drama.core.time import utcnow
from short_drama.dao.assistant.execution.model_ledger import AgentModelLedgerDAO
from short_drama.dao.assistant.execution.run import AgentRunDAO
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def enforce_media_limit_locked(session, task, operation, values):
    from short_drama.dao.assistant.tasks.task import AgentTaskDAO
    from short_drama.schemas.assistant.operations.media import MEDIA_USAGE_KEYS

    if operation.business_invocation_id is not None:
        return
    submitted = dict.fromkeys(MEDIA_USAGE_KEYS.values(), 0)
    seen = set()
    for receipt_id, result, arguments in AgentTaskDAO(session).media_admission_receipts(
        task.id, operation.id
    ):
        if receipt_id in seen:
            continue
        seen.add(receipt_id)
        for request, accepted in zip(arguments["requests"], result["items"], strict=True):
            kind = request.get("media_kind") or {
                "shot_video": "video",
                "character_voice": "audio",
                "dialogue_audio": "audio",
            }.get(request["target_kind"], "image")
            submitted[MEDIA_USAGE_KEYS[kind]] += accepted["count"]
    for request in values.requests:
        submitted[MEDIA_USAGE_KEYS[request.media_kind]] += request.count
    if any(
        task.media_limits[kind] is not None and count > task.media_limits[kind]
        for kind, count in submitted.items()
    ):
        raise WorkflowError("assistant_media_limit", "本任务媒体生成数量已达到上限", 409)


class AgentToolExecutionService:
    def __init__(self, context):
        self.context = context

    def _bound(self, session, *, admission=True):
        rows = lock_run(session, self.context.run_id)
        if rows is None:
            raise LeaseLost()
        project, conversation, run = rows
        if not run.lease_token or not owns_execution(run, self.context.fence):
            raise LeaseLost()
        task = task_for_run(session, run)
        if admission and (
            run.cancel_requested
            or task.control_state == "stop_pending"
            or not may_decide(session, project, conversation, run)
        ):
            raise LeaseLost()
        return conversation, task, run

    def model_input(self, state, execution_identity):
        # 来源撤回可能恢复相同消息；回执只用于同一公开图任务的崩溃恢复。
        signature = digest(
            {
                "execution_identity": execution_identity,
                "messages": [message_to_dict(item) for item in state["messages"]],
            }
        )
        with self.context.factory.begin() as session:
            conversation, task, run = self._bound(session)
            # ContextMiddleware 的更新已写检查点，产品水位现在才可前移。
            apply_inputs_locked(
                session,
                conversation,
                task,
                run,
                through_revision=state.get("applied_input_revision", 0),
            )
            turn = AgentRunDAO(session).model_result(run.id, self.context.scope_id, signature)
            if turn is None:
                return signature, None
            message = messages_from_dict([turn.response["graph_message"]])[0]
            compacted = turn.response.get("graph_compaction")
            if compacted:
                return signature, ExtendedModelResponse(
                    model_response=ModelResponse(result=[message]),
                    command=Command(
                        update={
                            "messages": [
                                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                                *messages_from_dict(compacted),
                                message,
                            ]
                        }
                    ),
                )
            return signature, message

    def pending_input(self, state):
        if self.context.scope_id != self.context.root_scope_id:
            return False
        with self.context.factory.begin() as session:
            _conversation, task, _run = self._bound(session)
            return task.input_revision > state.get("applied_input_revision", 0)

    def model_result(self, signature, message):
        with self.context.factory.begin() as session:
            conversation, _task, run = self._bound(session)
            call_id = message.response_metadata.get("protected_usage", {}).get("model_call_id")
            turn = AgentModelLedgerDAO(session, run.id).call(call_id)
            if (
                turn is None
                or turn.scope_id != self.context.scope_id
                or turn.usage["source"] != "assistant"
            ):
                raise WorkflowError("agent_response_not_durable", "模型回执尚未持久保存", 500)
            turn.response = {
                **(turn.response or {}),
                "graph_input_hash": signature,
                "graph_message": message_to_dict(message),
                **(
                    {"graph_compaction": [message_to_dict(item) for item in model_compaction.get()]}
                    if model_compaction.get()
                    else {}
                ),
                "applied": True,
            }
            if (
                self.context.scope_id == self.context.root_scope_id
                and message.text.strip()
                and message.tool_calls
            ):
                from short_drama.domain.assistant.conversations.models import AgentMessage

                narration = AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    task_id=run.task_id,
                    seq=conversation.next_message_seq,
                    item_seq=allocate_item_seq(conversation),
                    role="assistant",
                    kind="narration",
                    content=display_safe(message.text),
                    references=[],
                    artifacts=[],
                    created_at=utcnow(),
                )
                conversation.next_message_seq += 1
                session.add(narration)
                append_event(
                    session,
                    conversation,
                    "message.created",
                    {
                        "id": str(narration.id),
                        "run_id": str(run.id),
                        "seq": narration.seq,
                        "item_seq": narration.item_seq,
                        "kind": "narration",
                        "role": "assistant",
                        "content": narration.content,
                    },
                    run_id=run.id,
                )
            now = utcnow()
            for index, call in enumerate(message.tool_calls, 1):
                if call["name"] not in self.context.permitted_tool_names:
                    raise WorkflowError("unknown_tool", "模型请求了当前范围之外的工具", 422)
                session.add(
                    AgentToolCall(
                        id=next_id(),
                        run_id=run.id,
                        scope_id=self.context.scope_id,
                        turn_id=turn.id,
                        call_index=index,
                        provider_call_id=call["id"],
                        tool_name=call["name"],
                        arguments={"parsed": call["args"]},
                        arguments_hash=digest(call["args"]),
                        idempotency_key=digest([run.id, self.context.scope_id, call["id"]]),
                        status="prepared",
                        created_at=now,
                        updated_at=now,
                    )
                )
            usage = {**initial_usage(), **run.usage}
            usage["tool_calls"] += len(message.tool_calls)
            run.usage, run.phase = usage, "tools" if message.tool_calls else "model"
            append_event(
                session,
                conversation,
                "decision.completed",
                {
                    "run_id": str(run.id),
                    "scope_id": str(self.context.scope_id),
                    "turn_id": str(turn.id),
                    "tool_count": len(message.tool_calls),
                },
                run_id=run.id,
            )

    def start_tool(self, call, state):
        with self.context.factory.begin() as session:
            conversation, task, run = self._bound(session)
            if call["name"] not in state.get("permitted_tool_names", []):
                raise WorkflowError("assistant_tool_scope", "工具不属于当前执行范围", 403)
            stored = AgentRunDAO(session).tool_call(run.id, self.context.scope_id, call["id"])
            if (
                stored is None
                or stored.tool_name != call["name"]
                or stored.arguments["parsed"] != call["args"]
            ):
                raise WorkflowError("assistant_tool_call_scope", "工具调用尚未由模型边界登记", 409)
            operation = session.scalar(
                select(AgentOperation)
                .where(AgentOperation.tool_call_id == stored.id)
                .with_for_update()
            )
            if operation is not None:
                operation.execution_epoch = run.message_version
                needs_interrupt = (operation.wait_state or {}).get("requires_interrupt") and not (
                    operation.wait_state or {}
                ).get("consumed")
                return operation.id, self._replay(
                    stored.result
                ) if operation.finished_at and not needs_interrupt else None
            operation = AgentOperation(
                id=next_id(),
                task_id=task.id,
                run_id=run.id,
                scope_id=self.context.scope_id,
                tool_call_id=stored.id,
                tool_name=stored.tool_name,
                item_seq=allocate_item_seq(conversation),
                tool_version=TOOL_VERSION,
                plan_revision=task.plan_revision,
                input_revision=state.get("applied_input_revision", task.applied_input_revision),
                payload_hash=digest(call["args"]),
                arguments=deepcopy(call["args"]),
                status="running",
                phase="tool_running",
                row_version=1,
                execution_epoch=run.message_version,
                result_refs=[],
                changes=[],
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(operation)
            if stored.tool_name in READ_TOOLS:
                delegated = delegated_inputs_locked(session, run.id, self.context.scope_id)
                snapshot = (
                    delegated.get("context_snapshot")
                    if delegated is not None
                    else task.context_snapshot
                )
                stored.result = {
                    "source_context": {
                        "memory_epoch": state.get("memory_epoch", 0),
                        "subject_keys": sorted(subject_memory_keys(snapshot)),
                    }
                }
            stored.status, stored.updated_at = "running", utcnow()
            task.phase, task.row_version = "tool_running", task.row_version + 1
            session.flush()
            operation_block_locked(
                session,
                conversation,
                run,
                operation,
                spec=self.context.tool_lookup(stored.tool_name),
                state={"task": task, "state": state},
            )
            task_event(
                session,
                conversation,
                task,
                "step.started",
                run=run,
                call_id=str(stored.id),
                operation_id=str(operation.id),
                tool_name=stored.tool_name,
                scope_id=str(operation.scope_id),
                item_seq=operation.item_seq,
                visibility=visibility(stored.tool_name),
                label=label(stored.tool_name),
                phase=operation.phase,
                trace_version=str(operation.row_version),
            )
            return operation.id, None

    def finish_tool(self, operation_id, output):
        message = (
            next(item for item in output.update["messages"] if isinstance(item, ToolMessage))
            if isinstance(output, Command)
            else output
        )
        try:
            result = json.loads(message.content)
        except (ValueError, TypeError):
            result = {"content": message.content}
        if not isinstance(result, dict):
            result = {"value": result}
        with self.context.factory.begin() as session:
            conversation, task, run = self._bound(session, admission=False)
            operation = session.get(AgentOperation, operation_id)
            stored = session.get(AgentToolCall, operation.tool_call_id)
            if operation.tool_name == "task" and message.status != "error":
                child = session.scalar(
                    select(AgentExecutionScope).where(
                        AgentExecutionScope.run_id == run.id,
                        AgentExecutionScope.parent_tool_call_id == stored.id,
                    )
                )
                if child is not None:
                    business = business_outputs_locked(session, run.id, child.id)
                    references = [
                        reference
                        for refs in session.scalars(
                            select(AgentOperation.result_refs).where(
                                AgentOperation.run_id == run.id,
                                AgentOperation.scope_id == child.id,
                                AgentOperation.status == "succeeded",
                            )
                        )
                        for reference in refs
                        if reference.get("source") in {"sandbox_artifact", "sandbox_log"}
                    ]
                    references += [
                        reference for saved in business for reference in saved.result_refs
                    ]
                    result = {
                        "content": message.content,
                        "scope_id": str(child.id),
                        "result_refs": references,
                        "business_receipts": [
                            {"operation_id": str(saved.id), "tool_name": saved.tool_name}
                            for saved in business
                        ],
                    }
                    message.content = json.dumps(result, ensure_ascii=False)
                    message.name = "task"
            if operation.wait_state:
                message.additional_kwargs = {
                    **message.additional_kwargs,
                    "wait_operation_id": str(operation.id),
                }
            if operation.tool_name in READ_TOOLS | WRITE_TOOLS:
                message.content = json.dumps(
                    {**result, "receipt_operation_id": str(operation.id)}, ensure_ascii=False
                )
            operation.status = (
                "failed" if message.status == "error" or result.get("error") else "succeeded"
            )
            operation.error = result.get("error")
            operation.result = {
                **{
                    key: value
                    for key, value in (operation.result or {}).items()
                    if key.startswith("_display") or key == "_trace_bindings"
                },
                **(
                    {"delegation_inputs": operation.result["delegation_inputs"]}
                    if operation.tool_name == "task"
                    and (operation.result or {}).get("delegation_inputs")
                    else {}
                ),
                **result,
                "receipt_operation_id": str(operation.id),
            }
            operation.result_refs = result.get("result_refs", operation.result_refs)
            operation.phase = "terminal"
            operation.finished_at = operation.updated_at = utcnow()
            operation.row_version += 1
            stored.status, stored.error, stored.updated_at = (
                operation.status,
                operation.error,
                utcnow(),
            )
            source_context = (stored.result or {}).get("source_context")
            if operation.tool_name == "runtime__record_task_observation" and not operation.error:
                source = session.get(AgentOperation, int(operation.arguments["operation_id"]))
                source_context = session.get(AgentToolCall, source.tool_call_id).result[
                    "source_context"
                ]
            if source_context is not None:
                source_context = {
                    **source_context,
                    "source_message_ids": sorted(
                        set(
                            source_context.get("source_message_ids", [])
                            + result_source_ids(result)
                            + message.additional_kwargs.get("source_message_ids", [])
                        )
                    ),
                    "skill_activation_ids": sorted(
                        set(
                            source_context.get("skill_activation_ids", [])
                            + message.additional_kwargs.get("skill_activation_ids", [])
                            + ([result["activation_id"]] if result.get("activation_id") else [])
                        )
                    ),
                }
                message.additional_kwargs = {
                    **message.additional_kwargs,
                    **{
                        key: source_context[key]
                        for key in ("subject_keys", "source_message_ids", "skill_activation_ids")
                    },
                }
            stored.result = {
                "codec": "agent.graph-tool-result",
                **({"source_context": source_context} if source_context is not None else {}),
                "message": message_to_dict(message),
                "update": {
                    key: deepcopy(value)
                    for key, value in output.update.items()
                    if key != "messages"
                }
                if isinstance(output, Command)
                else None,
            }
            task.phase, task.row_version = "deciding", task.row_version + 1
            operation_block_locked(
                session,
                conversation,
                run,
                operation,
                spec=self.context.tool_lookup(stored.tool_name),
            )
            task_event(
                session,
                conversation,
                task,
                "step.finished",
                run=run,
                call_id=str(stored.id),
                operation_id=str(operation.id),
                tool_name=stored.tool_name,
                scope_id=str(operation.scope_id),
                item_seq=operation.item_seq,
                visibility=visibility(stored.tool_name),
                label=label(stored.tool_name),
                phase="terminal",
                outcome=operation.status,
                trace_version=str(operation.row_version),
                result_refs=operation.result_refs,
                error=operation.error,
            )
        return output

    @staticmethod
    def _replay(saved):
        message = messages_from_dict([saved["message"]])[0]
        if saved["update"] is not None:
            return Command(update={**saved["update"], "messages": [message]})
        return message
