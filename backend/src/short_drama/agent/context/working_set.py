"""Bounded observations backed by eligible task operations, never long-term memory."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from short_drama.agent.task.contract import READ_TOOLS
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.agent_transcript_dao import AgentTranscriptDAO
from short_drama.schemas.base import Identifier, InputModel, nonblank
from short_drama.utils.json_digest import digest

NOTE_TOKENS = 3000


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


def projection(task, run) -> dict:
    state = task.effective_state.get("working_set", {})
    floor = (run.checkpoint or {}).get("history_floor_turn_id", 0)
    items = [item for item in state.get("items", []) if int(item["turn_id"]) > floor]
    return {"items": items, "omitted": state.get("omitted", 0), "quota_tokens": NOTE_TOKENS}


def record_locked(session, conversation, task, operation, values: RecordObservation, settings):
    from short_drama.domain.agent import AgentRun

    run = session.get(AgentRun, operation.run_id)
    rows = AgentTranscriptDAO(session).evidence(task.id, [int(values.operation_id)])
    source = rows[0] if rows else None
    floor = (run.checkpoint or {}).get("history_floor_turn_id", 0)
    if (
        source is None
        or source.status != "succeeded"
        or source.turn_id <= floor
        or (source.tool_name not in READ_TOOLS or (source.result or {}).get("error"))
    ):
        raise WorkflowError("invalid_tool_arguments", "工作笔记来源不是当前可用的成功操作", 422)
    visual = str(source.id) in task.effective_state.get("media_evidence", {})
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
        "turn_id": str(source.turn_id),
        "source_checksum": digest(source.result),
        "kind": values.kind,
        "source_quote": values.source_quote,
        "note": values.note,
        "visual": visual,
    }
    state = projection(task, run)
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
