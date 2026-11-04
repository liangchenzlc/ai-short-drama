import math
from datetime import datetime

from sqlalchemy import select

from short_drama.agent.core import ENGINE_VERSION
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
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.service.base import utcnow


def preceding_execution(session, run):
    """Conversation lock serializes execution; review waits allow supplementary chat."""
    blockers = AgentRun.status.in_(("running", "waiting_generation", "waiting_input"))
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
            if value is None:
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
