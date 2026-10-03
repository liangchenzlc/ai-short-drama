"""Agent limits and locked state transitions shared by API, tools and workers."""

import re
from copy import deepcopy
from datetime import datetime

from short_drama.domain.agent import AgentEvent
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running", "waiting_review", "waiting_generation"})


def budget_limits(mode="discuss", *, images=0, videos=0):
    workflow = mode == "workflow"
    return {
        "decision_calls": 16 if workflow else 8,
        "tool_calls": 32 if workflow else 16,
        "output_tokens_per_call": 8192,
        "output_tokens": 65536 if workflow else 32768,
        "active_seconds": 600 if workflow else 300,
        "images": images,
        "videos": videos,
    }


def initial_usage():
    return {
        "decision_calls": 0,
        "tool_calls": 0,
        "output_tokens": 0,
        "reserved_output_tokens": 0,
        "active_seconds": 0.0,
        "images": 0,
        "videos": 0,
    }


def append_event(session, conversation, event_type, payload, *, run_id=None):
    """Caller holds Project -> Conversation/Run; payload is display-safe data."""
    now = utcnow()
    row = AgentEvent(
        id=next_id(),
        conversation_id=conversation.id,
        run_id=run_id,
        seq=conversation.next_event_seq,
        event_type=event_type,
        payload=deepcopy(payload),
        created_at=now,
    )
    conversation.next_event_seq += 1
    conversation.updated_at = now
    session.add(row)
    return row


def charge_active(run, now=None):
    now = now or utcnow()
    checkpoint = deepcopy(run.checkpoint or {})
    runtime = checkpoint.setdefault("runtime", {})
    started = runtime.pop("active_since", None)
    if started is not None:
        try:
            elapsed = max(0.0, (now - datetime.fromisoformat(started)).total_seconds())
        except (TypeError, ValueError):
            elapsed = 0.0
        usage = {**initial_usage(), **(run.usage or {})}
        usage["active_seconds"] += elapsed
        run.usage = usage
    run.checkpoint = checkpoint


def mark_scheduled(run, *, phase="model", when=None):
    """Schedule only an explicitly resumed active Run; never create a new Run."""
    if run.status in TERMINAL or run.cancel_requested:
        return False
    now = utcnow()
    charge_active(run, now)
    run.status = "queued" if run.status == "queued" else "running"
    run.phase = phase
    run.next_run_at = when or now
    run.message_status = "pending"
    run.message_version += 1
    run.lease_token = run.lease_until = None
    run.updated_at = now
    run.row_version += 1
    return True


def safe_error(code):
    return {
        "code": code
        if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code)
        else "agent_failed"
    }


def finish_locked(session, conversation, run, status, error=None):
    if status not in TERMINAL:
        raise ValueError("Invalid Agent terminal state")
    if run.status in TERMINAL:
        return False
    now = utcnow()
    charge_active(run, now)
    run.status = status
    run.error = safe_error(error.get("code")) if error else None
    run.finished_at = now
    run.next_run_at = None
    run.message_status = "idle"
    run.message_version += 1
    run.lease_token = run.lease_until = None
    run.updated_at = now
    run.row_version += 1
    append_event(
        session,
        conversation,
        "run.finished",
        {
            "run_id": str(run.id),
            "status": status,
            "error": run.error,
        },
        run_id=run.id,
    )
    return True


def wait_locked(session, conversation, run, status="waiting_review", payload=None):
    if status not in {"waiting_review", "waiting_generation"}:
        raise ValueError("Invalid Agent waiting state")
    if run.status in TERMINAL:
        return False
    now = utcnow()
    charge_active(run, now)
    run.status, run.phase = status, "wait"
    run.next_run_at = None
    run.message_status = "idle"
    run.message_version += 1
    run.lease_token = run.lease_until = None
    run.updated_at = now
    run.row_version += 1
    append_event(
        session,
        conversation,
        "run.waiting",
        {
            "run_id": str(run.id),
            "status": status,
            **(payload or {}),
        },
        run_id=run.id,
    )
    return True
