"""Durable completion review and progress supervision on existing task receipts."""

import json
from copy import deepcopy

from pydantic import ValidationError
from sqlalchemy import select

from short_drama.agent.core.state import finish_locked, mark_scheduled
from short_drama.agent.gateway.protocol import AgentGatewayError
from short_drama.agent.task.contract import (
    READ_TOOLS,
    WRITE_TOOLS,
    CompletionAssessment,
    CompletionReview,
    evaluate_review,
    review_tool,
)
from short_drama.agent.task.policy import policy
from short_drama.agent.tools.digest import stable
from short_drama.ai.prompts.loader import load_prompt
from short_drama.domain.agent import AgentRun, AgentTurn
from short_drama.domain.agent_task import AgentOperation

SUPERVISION_VERSION = 2
DIAGNOSE_AFTER = 6

# Tools that neither produce evidence nor count as wasted turns.
NEUTRAL_TOOLS = frozenset(
    {
        "set_task_requirements",
        "confirm_requirements",
        "enable_tool_group",
        "get_generation_policy",
        "search_skills",
        "release_skill",
        "memory_catalog",
        "search_memory",
    }
)

DEFAULT_PROGRESS = {"window": 24, "zero_windows": 2}


def evidence_kind(tool_name: str, result: dict) -> str:
    """Classify a settled operation for the sliding window, never for authorization."""
    if result.get("error") or result.get("already_applied") or result.get("already_loaded"):
        return "none"
    if tool_name in WRITE_TOOLS:
        return "write"
    if tool_name == "record_task_observation":
        return "observation" if result.get("recorded") else "none"
    if tool_name == "load_skill":
        return "skill"
    if tool_name in READ_TOOLS:
        return "read"
    return "none"


def _is_new_evidence(row, earlier: set[str]) -> bool:
    if row.evidence_kind in {None, "none"}:
        return False
    # Write repeats and re-reads of unchanged content share one digest, so only
    # the first appearance inside the window counts as progress.
    return bool(row.result_digest) and row.result_digest not in earlier


def candidate_text(session, task, candidate: dict) -> str:
    turn = session.get(AgentTurn, int(candidate["turn_id"]))
    source_run = session.get(AgentRun, turn.run_id) if turn else None
    if source_run is None or source_run.task_id != task.id:
        raise AgentGatewayError("invalid_agent_receipt")
    normalized = (turn.response or {}).get("normalized", {})
    if normalized.get("output_kind") != "text":
        raise AgentGatewayError("invalid_agent_receipt")
    return normalized["output"]


def prepare_review(session, task, run, values: dict, settings=None) -> dict:
    candidate = run.checkpoint.get("completion_candidate")
    if not candidate:
        return values
    if (
        candidate["input_revision"] != str(task.applied_input_revision)
        or candidate.get("memory_epoch", 0) != run.checkpoint.get("memory_epoch", 0)
        or int(candidate["turn_id"]) <= run.checkpoint.get("history_floor_turn_id", 0)
    ):
        run.checkpoint = {
            key: value for key, value in run.checkpoint.items() if key != "completion_candidate"
        }
        return values
    frame = deepcopy(values["context_frame"])
    frame["candidate_answer"] = candidate_text(session, task, candidate)
    frame["review_schema"] = "submit_completion_review"
    base = {
        **values,
        "decision_purpose": "completion_review",
        "context_frame": frame,
        "instructions": load_prompt("assistant/verification.md"),
        "tools": [review_tool()],
        "stream": False,
        "max_output_tokens": 4096,
        "max_tool_calls": 1,
        "hydration_intents": [],
        "continuation_prompt": "检查候选答复和真实执行证据，仅调用 submit_completion_review。",
    }
    if policy(task, "verification") != "evidence.v3":
        # Pre-upgrade tasks keep the original review input: same bounded history.
        return {**base, "history_manifest": values.get("history_manifest", [])}
    from short_drama.agent.harness.evidence import build
    from short_drama.agent.task.requirements import load_task_requirements
    from short_drama.service.agent_model_service import reviewer_snapshot

    requirements = load_task_requirements(session, task)
    if not requirements:
        return base
    floor = run.checkpoint.get("history_floor_turn_id", 0)
    reviewed, evidence, gaps = build(
        session, task, requirements, task.effective_state.get("media_evidence", {}), floor
    )
    frame["requirements"] = reviewed
    frame["evidence"] = evidence
    frame["evidence_gaps"] = gaps
    snapshot, degraded = reviewer_snapshot(
        session,
        run.initiated_by,
        run.config_snapshot,
        getattr(settings, "agent_reviewer_mode", "auto"),
    )
    return {
        **base,
        "instructions": load_prompt("assistant/verification-evidence.md"),
        # The reviewer gets the evidence package, never the executor's transcript.
        "history_manifest": [],
        "reviewer_snapshot": None if degraded else snapshot,
        "reviewer_degraded": degraded,
    }


def process_completion(session, conversation, task, run, turn, claim, normalized: dict):
    """Return final normalized output, or None after scheduling missing work.

    Review is a separate protected model Turn. Its schema call is an internal
    structured response, never an executable business tool or public message.
    """
    values = claim.inputs.get("kwargs", {})
    if not values.get("supervision_version") or run.checkpoint.get("discussion_revision"):
        return normalized
    checkpoint = deepcopy(run.checkpoint)
    review = values.get("decision_purpose") == "completion_review"
    if not review and normalized["output_kind"] != "text":
        return normalized
    if not review and policy(task, "verification") == "evidence.v3":
        from short_drama.agent.task.requirements import load_task_requirements

        if not load_task_requirements(session, task):
            # Nothing was confirmed, so there is no authoritative requirement to
            # check. Publish discussions directly, but never let unconfirmed
            # writes through: the guard still decides that case.
            from short_drama.agent.task.requirements import guard_publish_locked

            guard = guard_publish_locked(session, task, run)
            if guard is None:
                return normalized
            turn.response = {**turn.response, "internal": True}
            checkpoint["completion_feedback"] = guard
            run.checkpoint = checkpoint
            mark_scheduled(run, phase="model")
            return None
    turn.response = {**turn.response, "internal": True}
    if not review:
        checkpoint["completion_candidate"] = {
            "turn_id": str(turn.id),
            "input_revision": str(task.applied_input_revision),
            "memory_epoch": checkpoint.get("memory_epoch", 0),
        }
    else:
        candidate = checkpoint.pop("completion_candidate", None)
        if not candidate or candidate.get("memory_epoch", 0) != checkpoint.get("memory_epoch", 0):
            feedback = {
                "approved": False,
                "missing": [{"reason": "验收来源已变化，请重新核对当前要求"}],
            }
        else:
            try:
                calls = (
                    normalized["output"].get("calls", [])
                    if normalized["output_kind"] == "tool_requests"
                    else []
                )
                if len(calls) != 1 or calls[0]["tool_name"] != "submit_completion_review":
                    raise ValueError("Review must return the declared schema")
                arguments = calls[0]["args"]
                report_type = (
                    CompletionAssessment if values["supervision_version"] >= 2 else CompletionReview
                )
                report = report_type.model_validate(
                    json.loads(arguments) if isinstance(arguments, str) else arguments
                )
                text = candidate_text(session, task, candidate)
                feedback = evaluate_review(session, conversation, task, run, report, text)
            except (ValueError, ValidationError):
                feedback = {
                    "approved": False,
                    "missing": [{"reason": "验收输出未通过结构校验，请按真实来源重新核对"}],
                }
            if feedback["approved"]:
                from short_drama.agent.task.requirements import guard_publish_locked

                guard = guard_publish_locked(session, task, run)
                if guard is None:
                    checkpoint.pop("completion_feedback", None)
                    checkpoint.pop("completion_stall", None)
                    run.checkpoint = checkpoint
                    return {**normalized, "output_kind": "text", "output": text, "narration": ""}
                feedback = guard
        checkpoint["completion_feedback"] = feedback
        missing = sorted(item.get("key", "schema") for item in feedback["missing"])
        prior = checkpoint.get("completion_stall", {})
        repeated = prior.get("count", 0) + 1 if prior.get("missing") == missing else 1
        checkpoint["completion_stall"] = {"missing": missing, "count": repeated}
        if repeated >= 6:
            run.checkpoint = checkpoint
            finish_locked(
                session, conversation, run, "failed", {"code": "agent_completion_stalled"}
            )
            return None
    run.checkpoint = checkpoint
    mark_scheduled(run, phase="model")
    return None


def record_media_evidence(task, values: dict, turn_id: int) -> None:
    state = deepcopy(task.effective_state)
    evidence = state.get("media_evidence", {})
    outcomes = state.get("resource_outcomes", {})
    for outcome in values.get("resource_outcomes", []):
        identifier = outcome.get("operation_id")
        if not identifier:
            continue
        outcomes[identifier] = {**outcome, "turn_id": str(turn_id)}
        if outcome["status"] == "ready":
            evidence[identifier] = {
                "turn_id": str(turn_id),
                "source_turn_id": outcome.get("source_turn_id") or str(turn_id),
                "resource": outcome["resource"],
            }
    state["media_evidence"] = dict(list(evidence.items())[-64:])
    state["resource_outcomes"] = dict(list(outcomes.items())[-64:])
    task.effective_state = state


def _stable(value):
    return stable(value)


def record_progress(session, conversation, task, run, operation, result: dict) -> None:
    """Sliding-window stall detection over recorded evidence, not repeated calls."""
    profile = policy(task, "progress") or DEFAULT_PROGRESS
    window = max(1, int(profile.get("window", DEFAULT_PROGRESS["window"])))
    limit = max(1, int(profile.get("zero_windows", DEFAULT_PROGRESS["zero_windows"])))
    state = dict(task.effective_state)
    previous = state.get("progress") or {}
    if str(operation.id) == str(previous.get("last_operation_id")):
        return
    waiting = operation.tool_name == "get_generation_status" and result.get("settled") is False
    # Idempotent replays of an already-settled call are infrastructure noise,
    # not a stalled agent, so they neither advance nor count as zero progress.
    replay = bool(result.get("already_applied") or result.get("already_loaded"))
    neutral = operation.tool_name in NEUTRAL_TOOLS or replay
    rows = list(
        reversed(
            session.execute(
                select(
                    AgentOperation.evidence_kind,
                    AgentOperation.result_digest,
                    AgentOperation.id,
                )
                .where(
                    AgentOperation.task_id == task.id,
                    AgentOperation.input_revision == task.applied_input_revision,
                    AgentOperation.status.in_(["succeeded", "failed", "conflict"]),
                    AgentOperation.tool_name.not_in(NEUTRAL_TOOLS),
                )
                .order_by(AgentOperation.id.desc())
                .limit(window * 2)
            ).all()
        )
    )
    head, tail = (rows[:-window], rows[-window:]) if len(rows) > window else ([], rows)
    earlier = {row.result_digest for row in head if row.result_digest}
    progressed = any(_is_new_evidence(row, earlier) for row in tail)
    zero = int(previous.get("zero_windows", 0))
    if waiting or neutral:
        pass
    elif progressed or len(rows) < window:
        zero = 0
    else:
        zero += 1
    task.effective_state = {
        **state,
        "progress": {
            "window_start_id": str(tail[0].id) if tail else None,
            "zero_windows": zero,
            "last_operation_id": str(operation.id),
            "feedback": (
                "连续操作未获得新事实或成果，请核对已有证据、修正参数或换用其他入口；"
                "不要重复同一调用。"
            )
            if zero >= DIAGNOSE_AFTER
            else None,
        },
    }
    if zero >= limit and not waiting and not neutral:
        finish_locked(
            session,
            conversation,
            run,
            "failed",
            {
                "code": "agent_progress_stalled",
                "message": "连续尝试仍未取得新进展，已有成果已保留；请补充缺少的资料或要求后继续。",
            },
        )
