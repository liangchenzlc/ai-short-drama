"""Agent limits and ordered event append primitives without lifecycle dispatch."""

from copy import deepcopy

from short_drama.core.time import utcnow
from short_drama.domain.assistant.conversations.models import AgentEvent
from short_drama.utils.snowflake import next_id

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})

VARIABLE_HARNESS_METERS = frozenset(
    {"decision_calls", "tool_calls", "output_tokens", "active_seconds"}
)

ACTIVE = frozenset({"queued", "running", "waiting_generation", "waiting_input"})


def budget_limits(mode="discuss", *, images=0, videos=0):
    if mode == "harness":
        return {
            "decision_calls": None,
            "tool_calls": None,
            "output_tokens_per_call": 8192,
            "output_tokens": None,
            "active_seconds": None,
            "images": images,
            "videos": videos,
        }
    if mode == "assistant":
        return {
            "decision_calls": 1,
            "tool_calls": 0,
            "output_tokens_per_call": 8192,
            "output_tokens": 8192,
            "active_seconds": 300,
            "images": 0,
            "videos": 0,
        }
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
        "cached_tokens": 0,
        "active_seconds": 0.0,
        "images": 0,
        "videos": 0,
    }


def append_event(session, conversation, event_type, payload, *, run_id=None):
    """Caller holds Project -> Conversation/Run; private trace bindings stay server-side."""
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


def public_event_payload(event_type, payload):
    from short_drama.agent.runtime.trace_content import display_refs, display_safe, utf8_preview

    public = display_safe(payload)
    if "result_refs" in public:
        public["result_refs"] = display_refs(public["result_refs"])
    if event_type == "sandbox.log":
        public["content"] = utf8_preview(public["content"])
    return public
