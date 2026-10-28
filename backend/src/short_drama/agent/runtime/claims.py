import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime

from pydantic_ai.tools import ToolDefinition
from sqlalchemy import select

from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged
from short_drama.agent.core.events import (
    VARIABLE_HARNESS_METERS,
    budget_limits,
    initial_usage,
)
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.service.base import utcnow


def preceding_execution(session, run):
    """Conversation lock serializes execution; review waits allow supplementary chat."""
    blockers = AgentRun.status.in_(("running", "waiting_generation"))
    if run.status == "queued":
        blockers = blockers | ((AgentRun.status == "queued") & (AgentRun.id < run.id))
    return session.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.conversation_id == run.conversation_id,
            AgentRun.id != run.id,
            blockers,
        )
        .order_by(AgentRun.id)
        .limit(1)
        .with_for_update()
    )


def _cancelled_code(session, run):
    """A user stop is not an access revocation; keep the two notices distinct."""
    if run.task_id is not None:
        from short_drama.domain.assistant.tasks.task import AgentTask

        status = session.scalar(select(AgentTask.status).where(AgentTask.id == run.task_id))
        if status == "stopped":
            return "agent_stopped"
    return "access_revoked"


def checked_budget(run):
    authorization = (run.checkpoint or {}).get("authorization") or {}
    assistant = (run.checkpoint or {}).get("purpose") == "assistant_chat"
    mode = (
        "harness"
        if (run.checkpoint or {}).get("engine") == ENGINE_VERSION
        else (
            "assistant"
            if assistant
            else "workflow"
            if authorization.get("approved_plan")
            else "discuss"
        )
    )
    maximum = budget_limits(mode)
    budget = run.budget or {}
    if set(budget) != set(maximum):
        raise AgentGatewayError("invalid_agent_budget")
    for key, value in budget.items():
        if mode == "harness" and key in VARIABLE_HARNESS_METERS:
            if value is None and key in {"tool_calls", "active_seconds"}:
                continue
            if type(value) is not int or value <= 0:
                raise AgentGatewayError("invalid_agent_budget")
            continue
        if (
            type(value) is not int
            or value < 0
            or (
                key
                not in ({"images", "videos", "tool_calls"} if assistant else {"images", "videos"})
                and (value == 0 or value > maximum[key])
            )
            or (assistant and key in {"images", "videos", "tool_calls"} and value != 0)
        ):
            raise AgentGatewayError("invalid_agent_budget")
    return dict(budget)


def budget_limit(budget, key):
    """Infinity is arithmetic-only; durable budgets represent unlimited as null."""
    return math.inf if budget[key] is None else budget[key]


def active_seconds(run, now=None):
    usage = {**initial_usage(), **(run.usage or {})}
    started = ((run.checkpoint or {}).get("runtime") or {}).get("active_since")
    if started:
        try:
            usage["active_seconds"] += max(
                0.0, ((now or utcnow()) - datetime.fromisoformat(started)).total_seconds()
            )
        except (TypeError, ValueError):
            raise AgentGatewayError("invalid_agent_checkpoint") from None
    return usage["active_seconds"]


def latest_turn(session, run_id):
    return AgentTranscriptDAO(session).latest_turn(run_id)


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def freeze_input(values, run):
    values = dict(values)
    values["tools"] = [
        deepcopy(tool) if isinstance(tool, dict) else asdict(tool)
        for tool in values.get("tools", ())
    ]
    if (
        (run.checkpoint or {}).get("purpose") == "assistant_chat"
        or (run.checkpoint or {}).get("discussion_revision")
    ) and (
        values["tools"] or values.get("deferred_results") or values.get("max_tool_calls", 0) != 0
    ):
        raise AgentGatewayError("assistant_tools_forbidden")
    values.setdefault("stream", True)
    budget = checked_budget(run)
    usage = {**initial_usage(), **(run.usage or {})}
    available = (
        budget_limit(budget, "output_tokens")
        - usage["output_tokens"]
        - usage["reserved_output_tokens"]
    )
    if (
        available <= 0
        or usage["decision_calls"] >= budget_limit(budget, "decision_calls")
        or active_seconds(run) >= budget_limit(budget, "active_seconds")
    ):
        raise AgentGatewayError("agent_budget_exhausted")
    values["max_output_tokens"] = min(
        values.get("max_output_tokens", 8192), budget["output_tokens_per_call"], available
    )
    values["max_tool_calls"] = min(
        values.get("max_tool_calls", 16), budget_limit(budget, "tool_calls") - usage["tool_calls"]
    )
    values["timeout_seconds"] = min(
        values.get("timeout_seconds", 120),
        120,
        max(1, budget_limit(budget, "active_seconds") - active_seconds(run)),
    )
    context_budget = None
    if (run.checkpoint or {}).get("purpose") == "global_assistant":
        from short_drama.agent.context.budget import segment_budget

        context_budget = segment_budget(values)
    return {
        "codec": "agent.segment-input",
        "version": 1,
        "kwargs": _json_copy(values),
        "task_input_revision": (run.checkpoint or {}).get("task_input_revision"),
        "memory_epoch": (run.checkpoint or {}).get("memory_epoch", 0),
        "task_compaction_id": (run.checkpoint or {}).get("task_compaction_id"),
        "generation_policy_version": (run.checkpoint or {}).get("generation_policy_version"),
        "subject_access_keys": (run.checkpoint or {}).get("available_subject_keys"),
        "attachment_access_ids": (run.checkpoint or {}).get("available_attachment_ids"),
        "result_access_keys": (run.checkpoint or {}).get("available_result_keys"),
        "image_content_access_keys": (run.checkpoint or {}).get("available_image_content_keys"),
        **({"context_budget": context_budget} if context_budget is not None else {}),
    }


def thaw_input(envelope):
    if envelope.get("codec") != "agent.segment-input" or envelope.get("version") != 1:
        raise AgentGatewayError("invalid_agent_checkpoint")
    values = deepcopy(envelope["kwargs"])
    if not values.get("harness_version"):
        values["tools"] = [ToolDefinition(**tool) for tool in values.get("tools", ())]
    return values


@dataclass(frozen=True, repr=False)
class Claim:
    run_id: int
    version: int
    token: str
    phase: str
    turn_id: int | None
    snapshot: dict
    inputs: dict | None
    request: dict | None
    raw: dict | None
    task_id: int | None = None
