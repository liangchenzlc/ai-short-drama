"""The single projection boundary from real execution facts to product events."""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from typing import TYPE_CHECKING

from short_drama.agent.core.access import LeaseLost, lock_run
from short_drama.agent.core.events import append_event
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.runtime.delta_buffer import _DeltaBuffer
from short_drama.agent.runtime.trace_content import (
    display_refs,
    display_safe,
    display_text,
    reasoning_texts,
    utf8_preview,
)
from short_drama.agent.tools.presentation import visibility
from short_drama.core.time import utcnow
from short_drama.dao.assistant.execution.model_ledger import AgentModelLedgerDAO
from short_drama.dao.assistant.execution.trace import AssistantTraceDAO
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.schemas.assistant.operations.trace import TRACE_PREVIEW_BYTES, TraceBlock
from short_drama.utils.snowflake import next_id

if TYPE_CHECKING:
    from short_drama.agent.runtime.context import RuntimeContext
    from short_drama.agent.runtime.lease import ExecutionClaim

TERMINAL_TRACE = frozenset({"succeeded", "failed", "interrupted", "unknown", "cancelled"})
ARCHIVE_MAX_BYTES = 4 * 1024**2


def channel(text: str) -> dict:
    return {
        "preview": utf8_preview(text),
        "offset": 0,
        "total_bytes": len(text.encode("utf-8")),
        "truncated": len(text.encode("utf-8")) > TRACE_PREVIEW_BYTES,
    }


def save_block_locked(session, conversation, run, block, *, bindings=()):
    dao = AssistantTraceDAO(session)
    prior = dao.latest(conversation.id, run.id, block["block_id"], conversation.next_event_seq - 1)
    previous = prior.payload if prior else {}
    if previous.get("status") in TERMINAL_TRACE and block["status"] not in TERMINAL_TRACE:
        return previous
    block = deepcopy(block)
    block["block_version"] = int(previous.get("block_version", 0)) + 1
    block["started_seq"] = previous.get("started_seq", conversation.next_event_seq)
    saved_bindings = deepcopy(block.get("_resources", previous.get("_resources", [])))
    for binding in bindings:
        if not any(
            all(item.get(key) == value for key, value in binding.items()) for item in saved_bindings
        ):
            saved_bindings.append({"resource_id": str(next_id()), **deepcopy(binding)})
    block["resources"] = [
        {
            "resource_id": item["resource_id"],
            "name": display_safe(item["name"]),
            "kind": item["kind"],
            "byte_size": item.get("byte_size"),
            "total_bytes": item.get("total_bytes", item.get("byte_size")),
            "truncated": item.get("truncated", False),
            "available": item.get("available", True),
        }
        for item in saved_bindings
    ]
    public = TraceBlock.model_validate(
        {key: value for key, value in block.items() if not key.startswith("_")}
    ).model_dump(mode="json")
    append_event(
        session,
        conversation,
        "trace.block",
        {**public, "_resources": saved_bindings},
        run_id=run.id,
    )
    session.flush()
    return {**public, "_resources": saved_bindings}


def operation_block_locked(session, conversation, run, operation, *, spec=None, state=None):
    result = operation.result or {}
    if "_display" not in result:
        display, bindings = spec.presentation(
            spec=spec,
            session=session,
            task=state["task"],
            run=run,
            operation=operation,
            state=state["state"],
        )
        result = {
            **result,
            "_display": display.model_dump(mode="json"),
            "_trace_bindings": bindings,
        }
        operation.result = result
    display = result["_display"]
    scope = session.get(AgentExecutionScope, operation.scope_id)
    prior = AssistantTraceDAO(session).latest(
        conversation.id, run.id, operation.id, conversation.next_event_seq - 1
    )
    channels = deepcopy(prior.payload["channels"]) if prior else {}
    channels["input"] = channel(display_text(operation.arguments))
    if operation.finished_at:
        channels["output"] = channel(
            display_text(
                {
                    key: value
                    for key, value in result.items()
                    if not key.startswith("_") and key != "delegation_inputs"
                }
            )
        )
    now = utcnow()
    kind = (
        "subtask"
        if operation.tool_name == "task"
        else "skill"
        if "skill" in operation.tool_name
        else (
            "sandbox"
            if operation.tool_name
            in {"execute", "ls", "read_file", "write_file", "edit_file", "glob", "grep"}
            else "wait"
            if operation.tool_name in {"runtime__ask_user", "runtime__wait_generation"}
            else "tool"
        )
    )
    return save_block_locked(
        session,
        conversation,
        run,
        {
            **display,
            "block_id": str(operation.id),
            "run_id": str(run.id),
            "execution_scope_id": str(operation.scope_id),
            "parent_call_id": str(scope.parent_tool_call_id) if scope.parent_tool_call_id else None,
            "tool_call_id": str(operation.tool_call_id),
            "operation_id": str(operation.id),
            "subtask_id": str(operation.tool_call_id) if kind == "subtask" else None,
            "tool_name": operation.tool_name,
            "kind": kind,
            "source": spec.source if spec is not None else prior.payload["source"],
            "visibility": visibility(operation.tool_name),
            "status": operation.status,
            "started_at": operation.created_at,
            "updated_at": now,
            "finished_at": operation.finished_at,
            "duration_ms": int(
                (operation.finished_at - operation.created_at).total_seconds() * 1000
            )
            if operation.finished_at
            else None,
            "error": display_safe(operation.error),
            "channels": channels,
            "result_refs": display_refs(operation.result_refs),
        },
        bindings=result.get("_trace_bindings", []),
    )


def run_state_locked(session, conversation, run):
    from short_drama.dao.assistant.execution.scope import AgentExecutionScopeDAO

    prior = AssistantTraceDAO(session).latest(
        conversation.id, run.id, run.id, conversation.next_event_seq - 1
    )
    if run.status not in {"waiting_input", "waiting_generation"} and prior is None:
        return
    if prior is not None and prior.payload["status"] == run.status:
        return
    scope = AgentExecutionScopeDAO(session, owner_id=run.initiated_by, run_id=run.id).get(
        scope_key="root"
    )
    if scope is None:
        return
    title = {
        "waiting_input": "等待用户输入",
        "waiting_generation": "等待生成结果",
        "running": "恢复执行",
        "succeeded": "执行完成",
    }.get(run.status, "执行已结束")
    return save_block_locked(
        session,
        conversation,
        run,
        {
            "block_id": str(run.id),
            "run_id": str(run.id),
            "execution_scope_id": str(scope.id),
            "kind": "wait",
            "source": "runtime",
            "action": title,
            "title": title,
            "target": {"kind": "run", "id": str(run.id), "display_name": "本次执行"},
            "status": run.status,
            "started_at": run.started_at or run.created_at,
            "updated_at": utcnow(),
            "finished_at": run.finished_at,
            "error": display_safe(run.error),
        },
    )


def finish_trace_locked(session, conversation, run):
    cursor, before = conversation.next_event_seq - 1, None
    while True:
        page = AssistantTraceDAO(session).page(
            conversation.id, run.id, cursor, before_seq=before, limit=100
        )
        for item in page.items:
            if item.status in TERMINAL_TRACE or item.block_id == run.id:
                continue
            if item.operation_id:
                operation = session.get(AgentOperation, item.operation_id)
                operation_block_locked(session, conversation, run, operation)
            else:
                block = item.model_dump(mode="json")
                block.update(status="interrupted", finished_at=utcnow(), updated_at=utcnow())
                save_block_locked(session, conversation, run, block)
        if not page.has_more:
            break
        before = page.next_before_seq
    run_state_locked(session, conversation, run)


class AgentEventAdapter:
    def __init__(self, context: RuntimeContext, *, storage=None) -> None:
        self.context, self.storage = context, storage
        self.model_buffers, self.model_content, self.completed_calls = {}, {}, set()

    async def accept(
        self, framework_event: dict | tuple, claim: ExecutionClaim | None = None
    ) -> None:
        if isinstance(framework_event, dict) and framework_event.get("type") == "model.content":
            await asyncio.to_thread(self.model_content_event, framework_event)
        elif isinstance(framework_event, tuple) and len(framework_event) == 3:
            _namespace, mode, value = framework_event
            if mode == "messages":
                message, _metadata = value
                audit = message.response_metadata.get("protected_usage", {})
                call_id = audit.get("model_call_id")
                if call_id and call_id not in self.completed_calls:
                    for text in reasoning_texts(message):
                        await self.accept(
                            {
                                "type": "model.content",
                                **audit,
                                "channel": "reasoning",
                                "delta": text,
                            },
                            claim,
                        )

    def _bound(self, session):
        rows = lock_run(session, self.context.run_id)
        if (
            rows is None
            or not owns_execution(rows[2], self.context.fence)
            or rows[1].status == "deleted"
        ):
            raise LeaseLost()
        return rows[1], rows[2]

    def sandbox_locked(self, session, conversation, run, operation, kind, payload):
        prior = AssistantTraceDAO(session).latest(
            conversation.id, run.id, operation.id, conversation.next_event_seq - 1
        )
        if prior is None:
            return
        block = deepcopy(prior.payload)
        if kind == "sandbox.log":
            name, text = payload["stream"], display_safe(payload["content"])
            current = block["channels"].setdefault(name, channel(""))
            offset = current["total_bytes"]
            for start in range(0, len(text), 2048):
                piece = text[start : start + 2048]
                append_event(
                    session,
                    conversation,
                    "trace.delta",
                    {
                        "block_id": block["block_id"],
                        "block_version": str(int(block["block_version"]) + 1),
                        "execution_scope_id": block["execution_scope_id"],
                        "parent_call_id": block.get("parent_call_id"),
                        "tool_call_id": block["tool_call_id"],
                        "source": block["source"],
                        "channel": name,
                        "offset": offset,
                        "delta": piece,
                        "created_at": utcnow().isoformat() + "Z",
                    },
                    run_id=run.id,
                )
                offset += len(piece.encode("utf-8"))
                if not current["truncated"]:
                    current["preview"] = utf8_preview(current["preview"] + piece)
                current["total_bytes"] = offset
                current["truncated"] = offset > len(current["preview"].encode("utf-8"))
                block["updated_at"] = utcnow()
                block = save_block_locked(session, conversation, run, block)
                current = block["channels"][name]
            return
        if kind == "sandbox.log.archived":
            reference = payload["resource_ref"]
            current = block["channels"].setdefault(payload.get("stream", "log"), channel(""))
            current.update(total_bytes=payload["total_bytes"], truncated=True)
            binding = {
                "kind": "archive",
                "name": payload.get("stream", "log"),
                "locator": reference["uri"],
                "checksum": reference["checksum"],
                "total_bytes": payload["total_bytes"],
                "truncated": payload["archive_truncated"],
                "byte_size": reference["metadata"]["byte_size"],
            }
            save_block_locked(session, conversation, run, block, bindings=[binding])
        else:
            block["updated_at"] = utcnow()
            save_block_locked(session, conversation, run, block)

    def model_content_event(self, event: dict) -> None:
        call_id, name = event["model_call_id"], event["channel"]
        if name == "end":
            for key in [key for key in self.model_buffers if key[0] == call_id]:
                self.model_buffers.pop(key).add("", final=True)
            self._finish_model(call_id, event.get("status", "succeeded"))
            return
        text = display_safe(event["delta"])
        if not text:
            return
        key = (call_id, name)
        first = key not in self.model_buffers
        buffer = self.model_buffers.setdefault(key, _DeltaBuffer(self, event))
        # 首段立即建立可刷新的块；后续复用既有 200ms／8KiB flush，避免逐 token 写库。
        buffer.add(text, final=first)

    def save_delta(self, event: dict, text: str, offset: int) -> str:
        call_id, name = event["model_call_id"], event["channel"]
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session)
            turn = AgentModelLedgerDAO(session, run.id).call(call_id)
            if turn is None or turn.usage["source"] != event["source"]:
                raise LeaseLost()
            scope = session.get(AgentExecutionScope, turn.scope_id)
            for start in range(0, len(text), 2048):
                piece = text[start : start + 2048]
                if (
                    name == "text"
                    and event["source"] == "assistant"
                    and turn.scope_id == self.context.root_scope_id
                ):
                    append_event(
                        session,
                        conversation,
                        "assistant.delta",
                        {
                            "run_id": str(run.id),
                            "turn_id": str(turn.id),
                            "delta": piece,
                            "offset": offset,
                        },
                        run_id=run.id,
                    )
                elif name == "reasoning":
                    self._model_block(session, conversation, run, turn, scope, piece, offset)
                offset += len(piece.encode("utf-8"))
            if name == "reasoning":
                accumulated = self.model_content.setdefault(call_id, bytearray())
                accumulated.extend(
                    text.encode("utf-8")[: max(0, ARCHIVE_MAX_BYTES - len(accumulated))]
                )
        return "ok"

    def _model_block(self, session, conversation, run, turn, scope, text, offset):
        dao = AssistantTraceDAO(session)
        prior = dao.latest(conversation.id, run.id, turn.id, conversation.next_event_seq - 1)
        if prior is None:
            block = save_block_locked(
                session,
                conversation,
                run,
                {
                    "block_id": str(turn.id),
                    "run_id": str(run.id),
                    "model_call_id": str(turn.id),
                    "execution_scope_id": str(turn.scope_id),
                    "parent_call_id": str(scope.parent_tool_call_id)
                    if scope.parent_tool_call_id
                    else None,
                    "kind": "reasoning",
                    "source": {"tool_selection": "selector", "summarization": "summary"}.get(
                        turn.usage["source"], "assistant"
                    ),
                    "action": "模型思考",
                    "target": {
                        "kind": "model",
                        "id": str(run.model_config_id),
                        "display_name": run.config_snapshot["model_key"],
                    },
                    "title": "模型提供的思考",
                    "status": "running",
                    "started_at": turn.started_at,
                    "updated_at": utcnow(),
                    "channels": {"reasoning": channel("")},
                },
            )
        else:
            block = deepcopy(prior.payload)
        if block["status"] in TERMINAL_TRACE:
            return
        current = block["channels"]["reasoning"]
        if not current["truncated"]:
            current["preview"] = utf8_preview(current["preview"] + text)
        current["total_bytes"] = offset + len(text.encode("utf-8"))
        current["truncated"] = current["total_bytes"] > len(current["preview"].encode("utf-8"))
        append_event(
            session,
            conversation,
            "trace.delta",
            {
                "block_id": block["block_id"],
                "block_version": str(int(block["block_version"]) + 1),
                "execution_scope_id": block["execution_scope_id"],
                "parent_call_id": block.get("parent_call_id"),
                "source": block["source"],
                "channel": "reasoning",
                "offset": offset,
                "delta": text,
                "created_at": utcnow().isoformat() + "Z",
            },
            run_id=run.id,
        )
        block["updated_at"] = utcnow()
        save_block_locked(session, conversation, run, block)
        session.flush()

    def _finish_model(self, call_id: str, status: str) -> None:
        self.completed_calls.add(call_id)
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session)
            turn = AgentModelLedgerDAO(session, run.id).call(call_id)
            prior = (
                AssistantTraceDAO(session).latest(
                    conversation.id, run.id, turn.id, conversation.next_event_seq - 1
                )
                if turn
                else None
            )
            if prior is None:
                return
            block = deepcopy(prior.payload)
            block.update(
                status=status,
                finished_at=utcnow(),
                updated_at=utcnow(),
                duration_ms=int((utcnow() - turn.started_at).total_seconds() * 1000),
            )
            save_block_locked(session, conversation, run, block)
            identifier = turn.id
        content = bytes(self.model_content.pop(call_id, b""))
        if len(content) > TRACE_PREVIEW_BYTES:
            self.archive(
                identifier,
                "reasoning",
                content,
                total_bytes=block["channels"]["reasoning"]["total_bytes"],
            )

    def operation_content(self, operation_id: int, *, result=None) -> None:
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session)
            operation = session.get(AgentOperation, operation_id)
            values = {"input": operation.arguments} if result is None else {"output": result}
            texts = {name: display_text(value) for name, value in values.items()}
        for name, text in texts.items():
            if len(text.encode("utf-8")) > TRACE_PREVIEW_BYTES:
                self.archive(
                    operation_id, name, text.encode("utf-8"), total_bytes=len(text.encode("utf-8"))
                )

    def archive(self, block_id: int, name: str, data: bytes, *, total_bytes: int) -> None:
        from short_drama.service.media.files.storage import StorageService
        from short_drama.storage.minio import MinioStorage

        client = None if self.storage else MinioStorage(self.context.settings)
        storage = self.storage or StorageService(client, self.context.settings)
        try:
            data = data[:ARCHIVE_MAX_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
            invocation = f"operation:{block_id}"
            locator = storage.agent_resource_location(
                data, invocation_id=invocation, content_type="text/plain"
            ).locator
            binding = {
                "kind": "archive",
                "name": name,
                "locator": locator,
                "total_bytes": total_bytes,
                "truncated": total_bytes > len(data),
                "byte_size": len(data),
                "checksum": hashlib.sha256(data).hexdigest(),
                "available": False,
            }
            with self.context.factory.begin() as session:
                conversation, run = self._bound(session)
                prior = AssistantTraceDAO(session).latest(
                    conversation.id, run.id, block_id, conversation.next_event_seq - 1
                )
                save_block_locked(session, conversation, run, prior.payload, bindings=[binding])
            storage.freeze_agent_resource(data, invocation_id=invocation, content_type="text/plain")
            # 丢租约时保留 pending 引用交 purge；同哈希对象可能已被恢复 Worker 复用。
            with self.context.factory.begin() as session:
                conversation, run = self._bound(session)
                prior = AssistantTraceDAO(session).latest(
                    conversation.id, run.id, block_id, conversation.next_event_seq - 1
                )
                block = deepcopy(prior.payload)
                for item in block["_resources"]:
                    if item.get("locator") == locator:
                        item["available"] = True
                save_block_locked(session, conversation, run, block)
        finally:
            if client:
                client.close()

    def recover(self) -> None:
        """A replacement Worker never appends regenerated reasoning to an old call."""
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session)
            cursor, before = conversation.next_event_seq - 1, None
            while True:
                page = AssistantTraceDAO(session).page(
                    conversation.id, run.id, cursor, before_seq=before, limit=100
                )
                for block in page.items:
                    if block.kind == "reasoning" and block.status == "running":
                        values = block.model_dump(mode="json")
                        values.update(
                            status="interrupted", finished_at=utcnow(), updated_at=utcnow()
                        )
                        save_block_locked(session, conversation, run, values)
                if not page.has_more:
                    break
                before = page.next_before_seq
