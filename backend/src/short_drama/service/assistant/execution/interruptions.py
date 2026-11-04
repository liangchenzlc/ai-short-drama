"""Close interrupted orchestration receipts without cancelling admitted business work."""

import logging
from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.task.queries import task_for_run
from short_drama.agent.task.state import task_event
from short_drama.core.time import utcnow
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.conversations.models import AgentEvent, AgentMessage
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


def save_interrupted_text_locked(session, conversation, run):
    """Preserve only displayed root turns that have no durable product message yet."""
    from short_drama.agent.core.events import append_event
    from short_drama.agent.core.items import allocate_item_seq

    rows = session.execute(
        select(AgentEvent.seq, AgentEvent.event_type, AgentEvent.payload)
        .where(
            AgentEvent.run_id == run.id,
            AgentEvent.event_type.in_(("assistant.delta", "message.created", "message.updated")),
        )
        .order_by(AgentEvent.seq)
    ).all()
    represented = {
        part["turn_id"]
        for _, kind, payload in rows
        if kind != "assistant.delta"
        for part in payload.get("text_parts", [])
    }
    turns = {}
    for seq, kind, payload in rows:
        if kind == "assistant.delta" and payload["turn_id"] not in represented:
            turns.setdefault(payload["turn_id"], {}).setdefault(
                payload["offset"], (seq, payload["delta"])
            )
    for turn_id, chunks in turns.items():
        offset, parts = 0, []
        for start, (_seq, text) in sorted(chunks.items()):
            if start != offset:
                logger.error(
                    "agent_interrupted_text_gap", extra={"run_id": run.id, "turn_id": turn_id}
                )
                break
            parts.append(text)
            offset += len(text.encode("utf-8"))
        else:
            content = "".join(parts)
            message = AgentMessage(
                id=next_id(),
                conversation_id=conversation.id,
                task_id=run.task_id,
                seq=conversation.next_message_seq,
                item_seq=allocate_item_seq(conversation),
                role="assistant",
                kind="narration",
                content=content,
                references=[{"type": "interrupted_response", "turn_id": turn_id}],
                artifacts=[],
                created_at=utcnow(),
            )
            conversation.next_message_seq += 1
            session.add(message)
            append_event(
                session,
                conversation,
                "message.created",
                {
                    "id": str(message.id),
                    "run_id": str(run.id),
                    "seq": message.seq,
                    "item_seq": message.item_seq,
                    "role": "assistant",
                    "kind": "narration",
                    "content": content,
                    "interrupted": True,
                    "artifacts": [],
                    "text_parts": [
                        {
                            "turn_id": turn_id,
                            "event_seq": min(seq for seq, _ in chunks.values()),
                            "content": content,
                        }
                    ],
                },
                run_id=run.id,
            )


def settle_interrupted_locked(session, conversation, run, code):
    save_interrupted_text_locked(session, conversation, run)
    task = task_for_run(session, run)
    for operation in AgentTaskDAO(session).active_operations(task.id):
        if operation.run_id != run.id:
            continue
        receipt = (
            session.get(BusinessToolInvocation, operation.business_invocation_id)
            if operation.business_invocation_id
            else None
        )
        committed = receipt is not None and receipt.status == "succeeded"
        display = {
            key: value
            for key, value in (operation.result or {}).items()
            if key in {"_display", "_trace_bindings"}
        }
        operation.status = "succeeded" if committed else "failed"
        operation.error = (
            None if committed else {"code": "agent_cancelled" if operation.wait_state else code}
        )
        operation.result = (
            {
                **deepcopy(receipt.result),
                "business_invocation_id": str(receipt.id),
                "receipt_operation_id": str(operation.id),
                **display,
            }
            if committed
            else {**(operation.result or {}), "error": operation.error}
        )
        if committed:
            operation.result_refs, operation.changes = (
                deepcopy(receipt.result_refs),
                deepcopy(receipt.changes),
            )
        operation.phase = "terminal"
        operation.finished_at = operation.updated_at = utcnow()
        operation.row_version += 1
        call = session.get(AgentToolCall, operation.tool_call_id)
        if call is not None:
            call.status, call.error, call.updated_at = (
                "cancelled" if operation.wait_state and not committed else operation.status,
                operation.error,
                utcnow(),
            )
        if operation.wait_state:
            from short_drama.agent.core.events import append_event

            append_event(
                session,
                conversation,
                "question.cancelled"
                if operation.wait_state["kind"] == "user_input"
                else "wait.cancelled",
                {"operation_id": str(operation.id), "scope_id": str(operation.scope_id)},
                run_id=run.id,
            )
        task_event(
            session,
            conversation,
            task,
            "step.finished",
            run=run,
            operation_id=str(operation.id),
            tool_name=operation.tool_name,
            item_seq=operation.item_seq,
            outcome=operation.status,
            trace_version=str(operation.row_version),
            error=operation.error,
        )
    session.flush()
