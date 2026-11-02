"""Bounded observations backed by eligible task operations, never long-term memory."""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from short_drama.agent.context.delegation import scope_snapshot_locked
from short_drama.agent.context.operation_receipts import scoped_operation_locked
from short_drama.agent.context.subjects import eligible_snapshot, subject_memory_keys
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.audit import pixels_delivered
from short_drama.agent.task.sources import READ_TOOLS
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.schemas.base import Identifier, InputModel, nonblank
from short_drama.service.assistant.context.subject import selection_metadata
from short_drama.utils.json_digest import digest

NOTE_TOKENS = 3000


def eligible_scope_keys(session, owner_id, snapshot, restricted):
    subjects = eligible_snapshot(snapshot, restricted)
    available = {
        item["subject_key"]
        for item in selection_metadata(session, owner_id, subjects)
        if item["availability"] != "unavailable"
    }
    return subject_memory_keys(
        {
            "subjects": [
                item
                for item in (subjects or {}).get("subjects", [])
                if item["subject_key"] in available
            ]
        }
    )


class RecordObservation(InputModel):
    operation_id: Identifier
    kind: Literal["observation", "inference"] = "observation"
    source_quote: Annotated[
        str,
        Field(
            max_length=1000,
            description="从来源 content 复制一段连续原文短句，保留字符和换行；不能拼接或改写。"
            "已交付像素的观察可留空。",
        ),
    ] = ""
    note: Annotated[str, Field(min_length=1, max_length=1000), AfterValidator(nonblank)]


def _contents(value):
    if isinstance(value, dict):
        if isinstance(value.get("content"), str):
            yield value["content"]
        for key in ("items", "results", "subjects"):
            for item in value.get(key, []):
                yield from _contents(item)


def projection(session, task, run, scope_id, state, restricted, scope_keys) -> dict:
    saved = task.effective_state.get("working_set", {})
    identifiers = {
        int(item[key])
        for item in saved.get("items", [])
        for key in ("operation_id", "record_operation_id")
    }
    operations = {row.id: row for row in AgentTranscriptDAO(session).evidence(task.id, identifiers)}
    items = []
    for item in saved.get("items", []):
        source = operations.get(int(item["operation_id"]))
        recorder = operations.get(int(item["record_operation_id"]))
        if (
            recorder is not None
            and recorder.run_id == run.id
            and recorder.scope_id == scope_id
            and recorder.status == "succeeded"
            and _eligible(source, run, restricted, scope_keys, state)
            and digest(source.result) == item["source_checksum"]
        ):
            items.append(item)
    return {"items": items, "omitted": saved.get("omitted", 0), "quota_tokens": NOTE_TOKENS}


def _eligible(source, run, restricted, scope_keys, state=None):
    if source is None or source.status != "succeeded" or source.tool_name not in READ_TOOLS:
        return False
    context = (source.tool_result or {}).get("source_context")
    return bool(
        context is not None
        and context["memory_epoch"] == run.checkpoint.get("memory_epoch", 0)
        and set(context["subject_keys"]).issubset(scope_keys)
        and not {str(value) for value in restricted}.intersection(context["source_message_ids"])
        and (
            state is None
            or set(context["skill_activation_ids"]).issubset(
                item["id"] for item in state.get("skill_activations", []) if item.get("active")
            )
        )
    )


def record_locked(session, conversation, task, operation, values: RecordObservation, settings):
    from short_drama.domain.assistant.execution.models import AgentRun

    run = session.get(AgentRun, operation.run_id)
    scoped_source = scoped_operation_locked(session, task, operation, int(values.operation_id))
    rows = AgentTranscriptDAO(session).evidence(task.id, [int(values.operation_id)])
    source = rows[0] if rows else None
    restricted, _ = restricted_messages(session, conversation.id)
    scope_keys = eligible_scope_keys(
        session, conversation.owner_user_id, scope_snapshot_locked(session, task), restricted
    )
    if not _eligible(source, run, restricted, scope_keys):
        raise WorkflowError("invalid_tool_arguments", "工作笔记来源不是当前可用的成功操作", 422)
    visual = pixels_delivered(session, scoped_source)
    if not visual and (
        not values.source_quote.strip()
        or not any(values.source_quote in text for text in _contents(source.result))
    ):
        raise WorkflowError(
            "invalid_tool_arguments",
            "source_quote 必须是连续原文，不能拼接或改写；从 source_excerpt 复制短句后重试",
            422,
            details={
                "source_operation_id": str(source.id),
                "source_excerpt": next(_contents(source.result), "")[:500],
            },
        )
    note = {
        "operation_id": str(source.id),
        "record_operation_id": str(operation.id),
        "scope_id": str(operation.scope_id),
        "turn_id": str(source.turn_id),
        "source_checksum": digest(source.result),
        "kind": values.kind,
        "source_quote": values.source_quote,
        "note": values.note,
        "visual": visual,
    }
    state = deepcopy(task.effective_state.get("working_set", {"items": [], "omitted": 0}))
    identity = digest({key: value for key, value in note.items() if key != "record_operation_id"})
    if any(
        digest({key: value for key, value in item.items() if key != "record_operation_id"})
        == identity
        for item in state["items"]
    ):
        return {"recorded": False, "already_applied": True}
    state["items"].append(note)
    while (
        len(state["items"]) > 32
        or sum((len(str(item).encode()) + 2) // 3 for item in state["items"]) > NOTE_TOKENS
    ):
        state["items"].pop(0)
        state["omitted"] += 1
    task.effective_state = {**task.effective_state, "working_set": state}
    return {"recorded": True, "source_operation_id": str(source.id), "note": values.note}
