"""Deterministic evidence assembly for the completion reviewer.

The reviewer must not inherit the executor's transcript. This module rebuilds
"what was actually produced" from settled operations, runs the checks that can be
decided without a model, and only then hands the remainder to the reviewer.
"""

from sqlalchemy import select

from short_drama.domain.agent import AgentToolCall
from short_drama.domain.agent_task import AgentOperation

MAX_OPERATIONS = 200
MAX_EXCERPTS = 4
MAX_EXCERPT_CHARS = 1200


def _write_tools():
    from short_drama.agent.task.contract import WRITE_TOOLS

    return WRITE_TOOLS


def _read_tools():
    from short_drama.agent.task.contract import READ_TOOLS

    return READ_TOOLS


def _refs(operation):
    return [ref for ref in operation.result_refs or [] if ref.get("id")]


def _excerpt(operation):
    result = operation.result or {}
    text = result.get("content")
    if not isinstance(text, str):
        items = result.get("results")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            text = items[0].get("content")
    if not isinstance(text, str):
        text = ""
    return text[:MAX_EXCERPT_CHARS]


def load_operations(session, task, floor):
    rows = session.execute(
        select(AgentOperation, AgentToolCall.turn_id)
        .join(AgentToolCall, AgentToolCall.id == AgentOperation.tool_call_id)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.status == "succeeded",
            AgentToolCall.turn_id > floor,
        )
        .order_by(AgentOperation.id.desc())
        .limit(MAX_OPERATIONS)
    ).all()
    return [(row[0], int(row[1])) for row in rows]


def _matches(operation, requirement, media):
    kind = requirement.kind
    if kind == "write":
        if operation.tool_name not in _write_tools():
            return False
        artifact = requirement.artifact_kind
        if artifact and not any(ref.get("kind") == artifact for ref in _refs(operation)):
            return False
        if requirement.asset_kind:
            kinds = {(change.get("after") or {}).get("kind") for change in operation.changes or []}
            if requirement.asset_kind not in kinds:
                return False
        return True
    if kind == "read":
        return operation.tool_name in _read_tools()
    if kind == "image":
        return str(operation.id) in media
    if kind == "skill":
        return operation.tool_name == "load_skill"
    if kind == "observation":
        return operation.tool_name == "record_task_observation"
    return False


def _count(requirement, matched):
    if requirement.kind != "write":
        return len({operation.id for operation, _turn in matched})
    refs = {
        (ref.get("kind"), str(ref.get("id")))
        for operation, _turn in matched
        for ref in _refs(operation)
        if ref.get("status") != "deleted"
    }
    return len(refs)


def build(session, task, requirements, media, floor):
    """Return (reviewed requirements, evidence, gaps) built from settled operations."""
    operations = load_operations(session, task, floor)
    reviewed, evidence, gaps = [], {}, []
    for requirement in requirements:
        if requirement.kind in {"answer", "contract"}:
            reviewed.append(_summary(requirement, {"status": "unverifiable", "count": 0}, None))
            gaps.append({"key": requirement.requirement_key, "reason": "需要语义核对"})
            continue
        matched = [
            (operation, turn)
            for operation, turn in operations
            if _matches(operation, requirement, media)
        ]
        count = _count(requirement, matched)
        passed = count >= requirement.minimum_count
        entries = []
        for operation, turn in matched[:MAX_EXCERPTS]:
            entries.append(
                {
                    "operation_id": str(operation.id),
                    "tool_name": operation.tool_name,
                    "turn_id": str(turn),
                    "result_refs": _refs(operation)[:8],
                    "excerpt": _excerpt(operation),
                }
            )
        if entries:
            evidence[requirement.requirement_key] = entries
        reviewed.append(
            _summary(
                requirement,
                {
                    "status": "pass" if passed else "fail",
                    "count": count,
                    "reason": None
                    if passed
                    else f"有效证据 {count}/{requirement.minimum_count}",
                },
                matched[0][0].id if matched else None,
            )
        )
        if not passed:
            gaps.append(
                {
                    "key": requirement.requirement_key,
                    "reason": f"有效证据 {count}/{requirement.minimum_count}",
                }
            )
    return reviewed, evidence, gaps


def _summary(requirement, d1, first_operation_id):
    return {
        "key": requirement.requirement_key,
        "description": requirement.description,
        "kind": requirement.kind,
        "minimum_count": requirement.minimum_count,
        "artifact_kind": requirement.artifact_kind,
        "asset_kind": requirement.asset_kind,
        "d1": {**d1, "operation_id": str(first_operation_id) if first_operation_id else None},
    }
