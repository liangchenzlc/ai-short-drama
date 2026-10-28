"""Bounded projections of complete tool groups, with separate exact Skill retention."""

import json
import logging
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from short_drama.agent.gateway.protocol import AgentGatewayError
from short_drama.agent.resources.resolver import image_block
from short_drama.agent.skill.contracts import activations
from short_drama.agent.skill.progressive import ACTIVE_SKILL_TOKENS
from short_drama.dao.agent_transcript_dao import AgentTranscriptDAO
from short_drama.utils.json_digest import digest

logger = logging.getLogger(__name__)
RECENT_TOOL_TOKENS = 12000
PROGRESS_TOKENS = 2000
MAX_RECENT_GROUPS = 64


def _tokens(value):
    return (len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) + 2) // 3


def _result(dao, task_id, value):
    if isinstance(value, dict) and value.get("codec") == "agent.operation-ref":
        canonical = dao.operation_result(task_id, int(value["operation_id"]))
        if canonical is None or canonical.get("codec") == "agent.operation-ref":
            raise AgentGatewayError("invalid_agent_receipt")
        return canonical
    return value


def groups_locked(session, run, task, *, turn_ids=None, extra_turn_ids=()):
    dao = AgentTranscriptDAO(session)
    floor = (run.checkpoint or {}).get("history_floor_turn_id", 0)
    active = {
        int(item["body_operation_id"])
        for item in activations(task)
        if item["active"] and item.get("body_operation_id")
    }
    resumed = (run.checkpoint or {}).get("resume_history", False)
    turns = dao.settled_turns(
        run.id,
        after_id=floor,
        task_id=task.id,
        pinned_operations=active,
        include_task_history=resumed,
        turn_ids=turn_ids,
        extra_turn_ids=extra_turn_ids,
        limit=MAX_RECENT_GROUPS,
    )
    operations = {
        (row.turn_id, row.provider_call_id): row
        for row in dao.operations(task.id, [row[0] for row in turns])
    }
    groups = []
    for turn_id, source_run_id, normalized in turns:
        if not normalized:
            continue
        calls = (
            normalized["output"].get("calls", [])
            if normalized["output_kind"] == "tool_requests"
            else []
        )
        if any((turn_id, call["tool_call_id"]) not in operations for call in calls):
            continue
        results = []
        for call in calls:
            operation = operations[turn_id, call["tool_call_id"]]
            if operation.status not in {"succeeded", "failed", "conflict"}:
                break
            results.append(
                {
                    "operation_id": str(operation.id),
                    "tool_call_id": call["tool_call_id"],
                    "name": call["tool_name"],
                    "result": _result(dao, task.id, operation.result) or {"error": operation.error},
                }
            )
        else:
            # A source group beyond the current history floor may only retain
            # the active method pair. Other results may have been withdrawn.
            pinned_only = turn_id <= floor or source_run_id != run.id and not resumed
            if pinned_only:
                results = [item for item in results if int(item["operation_id"]) in active]
                call_ids = {item["tool_call_id"] for item in results}
                calls = [item for item in calls if item["tool_call_id"] in call_ids]
            groups.append(
                {
                    "turn_id": str(turn_id),
                    "content": (normalized.get("narration", "") if calls else normalized["output"])
                    if not pinned_only
                    else "",
                    "calls": calls,
                    "results": results,
                }
            )
    return groups


def select_groups(
    groups: list[dict], active_operation_ids: set[str], *, recent_tokens: int | None = None
):
    recent_tokens = RECENT_TOOL_TOKENS if recent_tokens is None else recent_tokens
    recent, used = [], 0
    for group in reversed(groups):
        cost = _tokens(group)
        if recent and (used + cost > recent_tokens or len(recent) >= MAX_RECENT_GROUPS):
            break
        if cost > recent_tokens and not recent:
            # Never silently discard the just-completed tool result. The total
            # provider context guard can reject an oversized complete group.
            logger.info("agent_context_latest_group_over_quota", extra={"estimated_tokens": cost})
        recent.append(group)
        used += cost
    recent_ids = {group["turn_id"] for group in recent}
    pinned = []
    for group in groups:
        if group["turn_id"] in recent_ids:
            continue
        results = [
            item for item in group["results"] if item["operation_id"] in active_operation_ids
        ]
        if results:
            call_ids = {item["tool_call_id"] for item in results}
            pinned.append(
                {
                    **group,
                    "content": "",
                    "calls": [item for item in group["calls"] if item["tool_call_id"] in call_ids],
                    "results": results,
                }
            )
    selected = sorted([*pinned, *recent], key=lambda group: int(group["turn_id"]))
    retained = {
        item["operation_id"]
        for group in selected
        for item in group["results"]
        if item["operation_id"] in active_operation_ids
    }
    if retained != active_operation_ids:
        raise AgentGatewayError("agent_skill_context_missing")
    return selected, {
        "dropped_groups": len(groups) - len({item["turn_id"] for item in selected}),
        "retained_skills": len(retained),
        "recent_tokens": used,
        "skill_quota_tokens": ACTIVE_SKILL_TOKENS,
    }


def _excerpt(text: str) -> dict:
    return {
        "text": text if len(text) <= 256 else text[:191] + "…" + text[-64:],
        "truncated": len(text) > 256,
    }


def _read_evidence(item):
    result = item["result"]
    if not isinstance(result, dict) or result.get("error"):
        return
    values = (
        [result]
        if isinstance(result.get("content"), str)
        else [*result.get("results", []), *result.get("items", []), *result.get("subjects", [])]
    )
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("content"), str):
            continue
        yield {
            "operation_id": item["operation_id"],
            "tool_name": item.get("name"),
            **({"source_operation_id": result["operation_id"]} if "operation_id" in result else {}),
            **({"next": result["next"]} if "next" in result else {}),
            **{
                key: value[key]
                for key in (
                    "id",
                    "subject_key",
                    "kind",
                    "row_version",
                    "range",
                    "activation_id",
                    "ref_id",
                    "path",
                    "offset",
                    "next_offset",
                    "source_changed",
                )
                if key in value
            },
            "excerpt": _excerpt(value["content"]),
        }


def compact_progress(groups: list[dict], selected: list[dict]) -> dict:
    """Keep bounded, verbatim evidence from eligible groups outside the recent window.

    This is a projection of already-read sources, not new memory or a synthesized
    claim. Only groups supplied by the current scope/floor checks are eligible.
    """
    retained = {item["operation_id"] for group in selected for item in group["results"]}
    narrations = {group["turn_id"]: group["content"] for group in selected}
    notes, omitted, seen = {}, 0, set()
    # Preserve observed tool evidence before spending quota on old narration.
    for group in reversed(groups):
        for item in group["results"]:
            if item["operation_id"] in retained:
                continue
            for read in _read_evidence(item):
                identity = digest(
                    {key: value for key, value in read.items() if key != "operation_id"}
                )
                if identity in seen:
                    continue
                note = notes.get(group["turn_id"], {"turn_id": group["turn_id"], "reads": []})
                candidate = {**note, "reads": [*note["reads"], read]}
                updated = {**notes, group["turn_id"]: candidate}
                if _tokens(list(updated.values())) > PROGRESS_TOKENS:
                    omitted += 1
                    continue
                notes, seen = updated, seen | {identity}
    for group in reversed(groups):
        narration = group["content"]
        if not narration or narration == narrations.get(group["turn_id"]):
            continue
        note = notes.get(group["turn_id"], {"turn_id": group["turn_id"], "reads": []})
        updated = {**notes, group["turn_id"]: {**note, "narration": _excerpt(narration)}}
        if _tokens(list(updated.values())) > PROGRESS_TOKENS:
            omitted += 1
            continue
        notes = updated
    ordered = [notes[group["turn_id"]] for group in groups if group["turn_id"] in notes]
    return {
        "items": ordered,
        "omitted_notes": omitted,
        "estimated_tokens": _tokens(ordered),
        "quota_tokens": PROGRESS_TOKENS,
    }


def freeze_transcript(session, run, task, *, recent_tokens=None):
    active = {
        item["body_operation_id"]
        for item in activations(task)
        if item["active"] and item.get("body_operation_id")
    }
    eligible = groups_locked(
        session, run, task, extra_turn_ids=run.checkpoint.get("transcript_tail", [])
    )
    quota = RECENT_TOOL_TOKENS if recent_tokens is None else min(RECENT_TOOL_TOKENS, recent_tokens)
    groups, metrics = select_groups(eligible, active, recent_tokens=quota)
    progress = compact_progress(eligible, groups)
    prior = task.effective_state.get("tool_progress", {})
    floor = run.checkpoint.get("history_floor_turn_id", 0)
    retained_turns = {group["turn_id"] for group in groups if group["content"]}
    notes = {
        item["turn_id"]: item
        for item in [*prior.get("items", []), *progress["items"]]
        if int(item["turn_id"]) > floor and item["turn_id"] not in retained_turns
    }
    selected_notes, used = [], 0
    for identifier in sorted(notes, key=int, reverse=True):
        cost = _tokens(notes[identifier])
        if used + cost <= PROGRESS_TOKENS:
            selected_notes.append(notes[identifier])
            used += cost
    progress.update(
        items=list(reversed(selected_notes)),
        estimated_tokens=used,
        omitted_notes=progress["omitted_notes"] + len(notes) - len(selected_notes),
    )
    task.effective_state = {**task.effective_state, "tool_progress": progress}
    metrics["progress_tokens"] = progress["estimated_tokens"]
    metrics["omitted_progress_notes"] = progress["omitted_notes"]
    # References and a digest freeze the exact projection, not another copy of
    # every tool body. Immutable Operation.result is the source of truth.
    manifest = [
        {
            "turn_id": group["turn_id"],
            "tool_call_ids": [item["tool_call_id"] for item in group["calls"]],
            "include_content": bool(group["content"]),
            "digest": digest(group),
        }
        for group in groups
    ]
    checkpoint = deepcopy(run.checkpoint)
    checkpoint["skill_body_operations"] = sorted(active)
    checkpoint["context_metrics"] = metrics
    checkpoint["transcript_tail"] = [int(item["turn_id"]) for item in manifest]
    run.checkpoint = checkpoint
    logger.info("agent_context_compacted", extra={"run_id": run.id, **metrics})
    return manifest, progress


def restore_transcript(session, run, task, manifest):
    by_id = {
        group["turn_id"]: group
        for group in groups_locked(
            session, run, task, turn_ids=[int(item["turn_id"]) for item in manifest]
        )
    }
    result = []
    for entry in manifest:
        group = deepcopy(by_id.get(entry["turn_id"]))
        if group is None:
            raise AgentGatewayError("invalid_agent_receipt")
        if not entry.get("include_content", True):
            group["content"] = ""
        call_ids = set(entry["tool_call_ids"])
        if {item["tool_call_id"] for item in group["calls"]} != call_ids:
            group["content"] = ""
            group["calls"] = [item for item in group["calls"] if item["tool_call_id"] in call_ids]
            group["results"] = [
                item for item in group["results"] if item["tool_call_id"] in call_ids
            ]
        if digest(group) != entry["digest"]:
            raise AgentGatewayError("agent_receipt_changed")
        result.append(group)
    return result


def model_messages(
    instructions,
    frame,
    groups,
    hydrated,
    intents,
    protocol,
    *,
    continuation_prompt=None,
    resource_outcomes=(),
    context_layout_version=1,
):
    messages = [
        SystemMessage(content=instructions),
        HumanMessage(
            content=json.dumps(
                frame
                if context_layout_version == 1
                else {
                    "task_id": frame.get("task_id"),
                    "execution_target": frame.get("execution_target"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
    ]
    by_operation = hydrated
    failures = {
        item["tool_call_id"]: item["error"]
        for item in resource_outcomes
        if item["status"] == "failed"
    }
    for group in groups:
        messages.append(
            AIMessage(
                content=group["content"],
                tool_calls=[
                    {
                        "name": call["tool_name"],
                        "args": json.loads(call["args"])
                        if isinstance(call["args"], str)
                        else call["args"],
                        "id": call["tool_call_id"],
                    }
                    for call in group["calls"]
                ],
            )
        )
        visual_delivery = []
        for item in group["results"]:
            value = deepcopy(item["result"])
            # A prior image read remains a reference. Only this invocation's
            # newly requested resources are added as visual content.
            value.pop("hydration_intent", None)
            if item["tool_call_id"] in failures:
                value["error"] = failures[item["tool_call_id"]]
                value["pixels_delivered"] = False
            content = json.dumps(value, ensure_ascii=False, sort_keys=True)
            resources = by_operation.get(item["tool_call_id"], [])
            if not isinstance(resources, list):
                resources = [resources]
            blocks = []
            for resource in resources:
                caption = resource.reference.metadata.get("caption", "")
                if caption:
                    blocks.append({"type": "text", "text": caption})
                blocks.append(image_block(resource, protocol))
            if blocks and protocol in {"claude_messages.v1", "openai_responses.v1"}:
                content = [{"type": "text", "text": content}, *blocks]
            elif blocks:
                visual_delivery += [
                    {"type": "text", "text": f"工具 {item['tool_call_id']} 请求的媒体："},
                    *blocks,
                ]
            messages.append(
                ToolMessage(content=content, tool_call_id=item["tool_call_id"], name=item["name"])
            )
        if visual_delivery:
            messages.append(HumanMessage(content=visual_delivery))
    if context_layout_version >= 2:
        messages.append(HumanMessage(content=json.dumps(frame, ensure_ascii=False, sort_keys=True)))
    if continuation_prompt:
        messages.append(HumanMessage(content=continuation_prompt))
    return messages


def tool_result_references(groups):
    references = {}
    for group in groups:
        for item in group["results"]:
            value = deepcopy(item["result"])
            value.pop("hydration_intent", None)
            content = json.dumps(value, ensure_ascii=False, sort_keys=True)
            references[content] = {
                "operation_id": item["operation_id"],
                "checksum": digest(value),
                "estimated_tokens": _tokens(value),
            }
    return references
