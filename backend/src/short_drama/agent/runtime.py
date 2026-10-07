"""Durable one-segment decisions. External model I/O never holds database locks."""

import asyncio
import contextlib
import hashlib
import json
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from pydantic_ai.tools import ToolDefinition
from sqlalchemy import select

from short_drama.agent.model_gateway import AgentGatewayError
from short_drama.agent.state import (
    TERMINAL,
    append_event,
    budget_limits,
    finish_locked,
    initial_usage,
    mark_scheduled,
    run_artifact_references,
    safe_error,
    wait_locked,
)
from short_drama.ai.model_identity import model_credential_identity
from short_drama.domain import AIModelConfig, Episode, Project
from short_drama.domain.agent import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)
from short_drama.domain.collaboration import ProjectMember, User
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id


class LeaseLost(Exception):
    pass


def lock_run(session, run_id, *, skip_locked=False):
    """All mutations, including publisher/recovery, lock Project before private rows."""
    parent = session.execute(
        select(AgentRun.conversation_id, AgentConversation.project_id)
        .join(AgentConversation, AgentConversation.id == AgentRun.conversation_id)
        .where(AgentRun.id == run_id)
    ).first()
    if parent is None:
        return None
    project = session.scalar(
        select(Project)
        .where(Project.id == parent.project_id)
        .with_for_update(skip_locked=skip_locked)
    )
    if project is None:
        return None
    conversation = session.scalar(
        select(AgentConversation)
        .where(AgentConversation.id == parent.conversation_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        return None
    run = session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    return (project, conversation, run) if run is not None else None


def may_decide(session, project, conversation, run):
    # Locking reads see current committed identity/membership after waiting for
    # the Project lock; ordinary MySQL REPEATABLE READ could use an older view.
    user = session.scalar(select(User).where(User.id == run.initiated_by).with_for_update())
    if (
        user is None
        or user.status != "active"
        or not user.email_verified_at
        or project.archived_at
        or conversation.status != "active"
        or conversation.owner_user_id != run.initiated_by
    ):
        return False
    if (
        project.owner_user_id != user.id
        and session.scalar(
            select(ProjectMember.id)
            .where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
                ProjectMember.status == "active",
            )
            .with_for_update()
        )
        is None
    ):
        return False
    authorization = (run.checkpoint or {}).get("authorization")
    if conversation.scope_version == 2:
        from short_drama.agent.assistant_chat import validate_assistant_chat

        try:
            validate_assistant_chat(conversation, run)
        except AgentGatewayError:
            return False
        return True
    if (run.checkpoint or {}).get("purpose") == "assistant_chat":
        return False
    episode = session.scalar(select(Episode).where(Episode.id == conversation.episode_id))
    return (
        episode is not None
        and episode.project_id == project.id
        and isinstance(authorization, dict)
        and authorization.get("mode") in {"auto", "discuss", "single", "workflow"}
    )


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


def model_unchanged(session, run):
    model = session.scalar(
        select(AIModelConfig).where(AIModelConfig.id == run.model_config_id).with_for_update()
    )
    snapshot = run.config_snapshot or {}
    return (
        model is not None
        and model.owner_user_id == run.initiated_by
        and model.enabled == 1
        and model.is_deleted == 0
        and model.service_type == "text"
        and model.row_version == snapshot.get("row_version")
        and model.model_key == snapshot.get("model_key")
        and model.base_url == snapshot.get("base_url")
        and model_credential_identity(model) == snapshot.get("credential_identity")
    )


def checked_budget(run):
    authorization = (run.checkpoint or {}).get("authorization") or {}
    assistant = (run.checkpoint or {}).get("purpose") == "assistant_chat"
    mode = (
        "assistant"
        if assistant
        else "workflow"
        if authorization.get("approved_plan")
        else "discuss"
    )
    maximum = budget_limits(mode)
    budget = run.budget or {}
    if set(budget) != set(maximum):
        raise AgentGatewayError("invalid_agent_budget")
    for key, value in budget.items():
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
    return budget


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
    return session.scalar(
        select(AgentTurn)
        .where(AgentTurn.run_id == run_id)
        .order_by(AgentTurn.turn_no.desc())
        .limit(1)
    )


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def freeze_input(values, run):
    values = dict(values)
    values["tools"] = [asdict(tool) for tool in values.get("tools", ())]
    if (run.checkpoint or {}).get("purpose") == "assistant_chat" and (
        values["tools"] or values.get("deferred_results") or values.get("max_tool_calls", 0) != 0
    ):
        raise AgentGatewayError("assistant_tools_forbidden")
    values.setdefault("stream", True)
    budget = checked_budget(run)
    usage = {**initial_usage(), **(run.usage or {})}
    available = budget["output_tokens"] - usage["output_tokens"] - usage["reserved_output_tokens"]
    if (
        available <= 0
        or usage["decision_calls"] >= budget["decision_calls"]
        or active_seconds(run) >= budget["active_seconds"]
    ):
        raise AgentGatewayError("agent_budget_exhausted")
    values["max_output_tokens"] = min(
        values.get("max_output_tokens", 8192), budget["output_tokens_per_call"], available
    )
    values["max_tool_calls"] = min(
        values.get("max_tool_calls", 16), budget["tool_calls"] - usage["tool_calls"]
    )
    values["timeout_seconds"] = min(
        values.get("timeout_seconds", 120),
        120,
        max(1, budget["active_seconds"] - active_seconds(run)),
    )
    return {"codec": "agent.segment-input", "version": 1, "kwargs": _json_copy(values)}


def thaw_input(envelope):
    if envelope.get("codec") != "agent.segment-input" or envelope.get("version") != 1:
        raise AgentGatewayError("invalid_agent_checkpoint")
    values = deepcopy(envelope["kwargs"])
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


class AgentRuntimeStore:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings

    def _owned(self, session, run_id, version, token):
        rows = lock_run(session, run_id)
        if rows is None:
            raise LeaseLost()
        project, conversation, run = rows
        if (
            run.status in TERMINAL
            or run.message_version != version
            or run.lease_token != token
            or run.lease_until is None
            or run.lease_until <= utcnow()
        ):
            raise LeaseLost()
        return project, conversation, run

    def claim_publish(self):
        if not self.settings.agent_enabled:
            return None
        now = utcnow()
        with self.factory.begin() as session:
            ids = session.scalars(
                select(AgentRun.id)
                .where(
                    AgentRun.status.in_(("queued", "running")),
                    AgentRun.message_status == "pending",
                    AgentRun.next_run_at <= now,
                )
                .order_by(AgentRun.next_run_at, AgentRun.id)
                .limit(50)
            ).all()
            for identifier in ids:
                rows = lock_run(session, identifier, skip_locked=True)
                if rows is None:
                    continue
                _project, _conversation, run = rows
                if (
                    run.message_status != "pending"
                    or run.status in TERMINAL
                    or not run.next_run_at
                    or run.next_run_at > now
                ):
                    continue
                if preceding_execution(session, run) is not None:
                    continue
                run.message_status = "publishing"
                run.lease_token, run.lease_until = (
                    uuid4().hex,
                    now + timedelta(seconds=self.settings.generation_publish_lease_seconds),
                )
                run.updated_at = now
                return {"run_id": run.id, "version": run.message_version, "token": run.lease_token}
        return None

    def publish_result(self, run_id, version, token, *, success):
        with self.factory.begin() as session:
            rows = lock_run(session, run_id)
            if rows is None:
                return False
            _project, _conversation, run = rows
            if (
                run.message_version != version
                or run.lease_token != token
                or run.message_status != "publishing"
            ):
                return False
            run.message_status = "published" if success else "pending"
            run.lease_token = run.lease_until = None
            run.updated_at = utcnow()
            if not success:
                run.next_run_at = run.updated_at + timedelta(seconds=2)
            return True

    def claim_execution(self, run_id, version, prepare_decision):
        if not self.settings.agent_enabled:
            return None
        now = utcnow()
        with self.factory.begin() as session:
            rows = lock_run(session, run_id)
            if rows is None:
                return None
            project, conversation, run = rows
            if (
                run.status not in {"queued", "running"}
                or run.message_version != version
                or run.message_status not in {"pending", "publishing", "published"}
                or (run.lease_token is not None and run.message_status != "publishing")
                or run.next_run_at is None
                or run.next_run_at > now
            ):
                return None
            from short_drama.agent.assistant_chat import is_assistant_chat, validate_assistant_chat

            if is_assistant_chat(conversation, run):
                try:
                    validate_assistant_chat(conversation, run)
                except AgentGatewayError as error:
                    finish_locked(session, conversation, run, "failed", {"code": error.code})
                    return None
            if run.cancel_requested or not may_decide(session, project, conversation, run):
                finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
                return None
            if preceding_execution(session, run) is not None:
                run.message_status = "pending"
                run.lease_token = run.lease_until = None
                return None
            from short_drama.core.exceptions import BusinessError
            from short_drama.service.agent_conversation_service import validate_conversation_subject

            try:
                validate_conversation_subject(session, conversation, lock=True)
            except BusinessError as error:
                finish_locked(session, conversation, run, "failed", {"code": error.code})
                return None
            run.status = "running"
            run.started_at = run.started_at or now
            run.updated_at = now
            run.message_status = "idle"
            run.lease_token, run.lease_until = (
                uuid4().hex,
                now + timedelta(seconds=self.settings.agent_lease_seconds),
            )
            checkpoint = deepcopy(run.checkpoint)
            checkpoint.setdefault("runtime", {})["active_since"] = now.isoformat()
            run.checkpoint = checkpoint
            turn = latest_turn(session, run.id)
            if run.phase == "tools":
                try:
                    if active_seconds(run) >= checked_budget(run)["active_seconds"]:
                        raise AgentGatewayError("agent_budget_exhausted")
                except AgentGatewayError as error:
                    finish_locked(session, conversation, run, "failed", {"code": error.code})
                    return None
                return Claim(run.id, version, run.lease_token, "tools", None, {}, None, None, None)
            if turn is None or (
                turn.status == "succeeded" and (turn.response or {}).get("applied") is True
            ):
                try:
                    inputs = freeze_input(prepare_decision(session, conversation, run), run)
                except AgentGatewayError as error:
                    finish_locked(session, conversation, run, "failed", {"code": error.code})
                    return None
                number = (turn.turn_no if turn else 0) + 1
                turn = AgentTurn(
                    id=next_id(),
                    run_id=run.id,
                    turn_no=number,
                    request_messages=[inputs],
                    status="prepared",
                    usage={},
                    created_at=now,
                    updated_at=now,
                )
                session.add(turn)
                session.flush()
            raw = (turn.response or {}).get("raw")
            if is_assistant_chat(conversation, run):
                inputs = turn.request_messages[0].get("kwargs", {})
                if (
                    inputs.get("tools")
                    or inputs.get("deferred_results")
                    or inputs.get("max_tool_calls") != 0
                ):
                    finish_locked(
                        session, conversation, run, "failed", {"code": "assistant_tools_forbidden"}
                    )
                    return None
            if turn.status != "prepared" and raw is None:
                turn.status, turn.error, turn.updated_at = (
                    "unknown",
                    {"code": "agent_acceptance_unknown"},
                    now,
                )
                turn.finished_at = now
                finish_locked(session, conversation, run, "failed", turn.error)
                return None
            return Claim(
                run.id,
                version,
                run.lease_token,
                "model",
                turn.id,
                deepcopy(run.config_snapshot),
                deepcopy(turn.request_messages[0]),
                deepcopy(turn.request_messages[1]) if len(turn.request_messages) > 1 else None,
                deepcopy(raw),
            )

    def before_send(self, claim, request):
        with self.factory.begin() as session:
            project, conversation, run = self._owned(
                session, claim.run_id, claim.version, claim.token
            )
            turn = session.get(AgentTurn, claim.turn_id)
            if (
                not self.settings.agent_enabled
                or run.cancel_requested
                or not may_decide(session, project, conversation, run)
                or not model_unchanged(session, run)
                or turn.status != "prepared"
            ):
                raise AgentGatewayError("agent_admission_denied")
            from short_drama.core.exceptions import BusinessError
            from short_drama.service.agent_conversation_service import validate_conversation_subject

            try:
                validate_conversation_subject(session, conversation, lock=True)
            except BusinessError as error:
                raise AgentGatewayError(error.code) from None
            budget = checked_budget(run)
            usage = {**initial_usage(), **(run.usage or {})}
            amount = claim.inputs["kwargs"]["max_output_tokens"]
            if (
                usage["decision_calls"] >= budget["decision_calls"]
                or usage["output_tokens"] + usage["reserved_output_tokens"] + amount
                > budget["output_tokens"]
                or active_seconds(run) >= budget["active_seconds"]
            ):
                raise AgentGatewayError("agent_budget_exhausted")
            usage["decision_calls"] += 1
            usage["reserved_output_tokens"] += amount
            run.usage, run.updated_at = usage, utcnow()
            turn.request_messages = [deepcopy(claim.inputs), _json_copy(request)]
            turn.status, turn.started_at, turn.updated_at = "sent", utcnow(), utcnow()
            turn.usage = {"reserved_output_tokens": amount, "settled": False}
            append_event(
                session,
                conversation,
                "decision.started",
                {"run_id": str(run.id), "turn_id": str(turn.id)},
                run_id=run.id,
            )

    def save_raw(self, claim, raw):
        # A late complete receipt is useful evidence even after stop/recovery.
        with self.factory.begin() as session:
            rows = lock_run(session, claim.run_id)
            if rows is None:
                raise LeaseLost()
            _project, _conversation, _run = rows
            turn = session.get(AgentTurn, claim.turn_id)
            if turn is None or turn.status == "prepared":
                raise LeaseLost()
            response = deepcopy(turn.response or {})
            if response.get("raw") is not None and response["raw"] != raw:
                raise AgentGatewayError("agent_response_conflict")
            response["raw"] = _json_copy(raw)
            turn.response, turn.updated_at = response, utcnow()

    def save_delta(self, claim, delta, offset):
        if not delta:
            return False
        with self.factory.begin() as session:
            try:
                _project, conversation, run = self._owned(
                    session, claim.run_id, claim.version, claim.token
                )
            except LeaseLost:
                return False
            if run.cancel_requested:
                return False
            append_event(
                session,
                conversation,
                "assistant.delta",
                {
                    "run_id": str(run.id),
                    "turn_id": str(claim.turn_id),
                    "delta": delta,
                    "offset": offset,
                },
                run_id=run.id,
            )
            return True

    def heartbeat(self, claim):
        with self.factory.begin() as session:
            try:
                _project, _conversation, run = self._owned(
                    session, claim.run_id, claim.version, claim.token
                )
            except LeaseLost:
                return False
            if not self.settings.agent_enabled or run.cancel_requested:
                return False
            run.lease_until = utcnow() + timedelta(seconds=self.settings.agent_lease_seconds)
            return True

    def save_result(self, claim, normalized):
        with self.factory.begin() as session:
            rows = lock_run(session, claim.run_id)
            if rows is None:
                raise LeaseLost()
            project, conversation, run = rows
            turn = session.get(AgentTurn, claim.turn_id)
            response = deepcopy(turn.response or {})
            if not response.get("raw"):
                raise AgentGatewayError("agent_response_not_durable", accepted_unknown=True)
            if response.get("applied") is True:
                return False
            normalized = response.get("normalized") or _json_copy(normalized)
            response["normalized"] = normalized
            response["applied"] = False
            turn.response, turn.status = response, "succeeded"
            turn.finished_at, turn.updated_at = utcnow(), utcnow()
            usage = {**initial_usage(), **(run.usage or {})}
            turn_usage = deepcopy(turn.usage or {})
            reported = normalized.get("usage") or {}
            tokens = reported.get("output_tokens")
            if (
                reported.get("output_tokens_reported") is True
                and type(tokens) is int
                and tokens >= 0
                and not turn_usage.get("settled")
            ):
                usage["reserved_output_tokens"] -= turn_usage.get("reserved_output_tokens", 0)
                usage["output_tokens"] += tokens
                turn_usage.update({"settled": True, "output_tokens": tokens})
            turn_usage["provider_usage"] = reported
            run.usage, turn.usage, run.updated_at = usage, turn_usage, utcnow()
            from short_drama.agent.assistant_chat import is_assistant_chat, validate_assistant_chat

            if is_assistant_chat(conversation, run):
                try:
                    validate_assistant_chat(conversation, run)
                    if normalized["output_kind"] != "text":
                        raise AgentGatewayError("assistant_tools_forbidden")
                except AgentGatewayError as error:
                    finish_locked(session, conversation, run, "failed", {"code": error.code})
                    return False
            active = (
                run.status not in TERMINAL
                and run.message_version == claim.version
                and run.lease_token == claim.token
                and run.lease_until
                and run.lease_until > utcnow()
            )
            if not active:
                return False
            if run.cancel_requested or not may_decide(session, project, conversation, run):
                finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
                return False
            budget = checked_budget(run)
            if usage["output_tokens"] + usage["reserved_output_tokens"] > budget["output_tokens"]:
                finish_locked(
                    session, conversation, run, "failed", {"code": "agent_budget_exhausted"}
                )
                return False
            if (
                normalized["output_kind"] == "tool_requests"
                and active_seconds(run) >= budget["active_seconds"]
            ):
                finish_locked(
                    session, conversation, run, "failed", {"code": "agent_budget_exhausted"}
                )
                return False
            checkpoint = deepcopy(run.checkpoint)
            checkpoint["history"] = normalized["history"]
            checkpoint["pending_results"] = {"calls": {}}
            run.checkpoint = checkpoint
            response["applied"] = True
            turn.response = response
            if normalized["output_kind"] == "text":
                authorization = checkpoint.get("authorization") or {}
                if authorization.get("mode") in {"single", "workflow"} and any(
                    step["id"] not in authorization.get("consumed_steps", [])
                    for step in authorization.get("steps", [])
                ):
                    finish_locked(
                        session,
                        conversation,
                        run,
                        "failed",
                        {
                            "code": "agent_plan_not_completed"
                            if authorization.get("mode") == "workflow"
                            else "agent_task_not_executed",
                        },
                    )
                    return False
                message = AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    seq=conversation.next_message_seq,
                    role="assistant",
                    content=normalized["output"],
                    references=[],
                    artifacts=run_artifact_references(session, conversation, run),
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
                        "role": "assistant",
                        "content": message.content,
                        "artifacts": message.artifacts,
                    },
                    run_id=run.id,
                )
                finish_locked(session, conversation, run, "succeeded")
                return False
            calls = normalized["output"].get("calls", [])
            if usage["tool_calls"] + len(calls) > budget["tool_calls"]:
                finish_locked(
                    session, conversation, run, "failed", {"code": "agent_tool_budget_exhausted"}
                )
                return False
            usage["tool_calls"] += len(calls)
            run.usage, run.phase = usage, "tools"
            for index, call in enumerate(calls, 1):
                raw_arguments = call["args"]
                try:
                    parsed = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else deepcopy(raw_arguments)
                    )
                    if not isinstance(parsed, dict):
                        parsed = None
                    else:
                        parsed = _json_copy(parsed)
                except (TypeError, ValueError):
                    parsed = None
                digest = hashlib.sha256(
                    json.dumps(
                        raw_arguments, ensure_ascii=False, sort_keys=True, allow_nan=False
                    ).encode()
                ).hexdigest()
                key = hashlib.sha256(
                    f"{run.id}:{turn.id}:{call['tool_call_id']}".encode()
                ).hexdigest()
                session.add(
                    AgentToolCall(
                        id=next_id(),
                        run_id=run.id,
                        turn_id=turn.id,
                        call_index=index,
                        provider_call_id=call["tool_call_id"],
                        tool_name=call["tool_name"],
                        arguments={"raw": raw_arguments, "parsed": parsed},
                        arguments_hash=digest,
                        idempotency_key=key,
                        status="prepared",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            append_event(
                session,
                conversation,
                "decision.completed",
                {"run_id": str(run.id), "turn_id": str(turn.id), "tool_count": len(calls)},
                run_id=run.id,
            )
            return True

    def fail(self, claim, error):
        with self.factory.begin() as session:
            rows = lock_run(session, claim.run_id)
            if rows is None:
                return
            _project, conversation, run = rows
            if (
                run.status in TERMINAL
                or run.message_version != claim.version
                or run.lease_token != claim.token
            ):
                return
            turn = session.get(AgentTurn, claim.turn_id)
            if turn is not None and turn.status != "succeeded":
                turn.status = (
                    "unknown" if turn.status == "sent" and error.accepted_unknown else "failed"
                )
                turn.error, turn.finished_at, turn.updated_at = (
                    safe_error(error.code),
                    utcnow(),
                    utcnow(),
                )
            finish_locked(
                session,
                conversation,
                run,
                "failed",
                {
                    **safe_error(error.code),
                    "accepted_unknown": error.accepted_unknown,
                },
            )


class _DeltaBuffer:
    """Batch gateway-sanitized display text; private protocol data never enters this path."""

    def __init__(self, store, claim):
        self.store, self.claim = store, claim
        self.pending, self.offset, self.last = "", 0, time.monotonic()
        self.disabled = False

    def add(self, delta, final=False):
        if self.disabled:
            return
        self.pending += delta
        if self.pending and (
            final or len(self.pending.encode()) >= 8192 or time.monotonic() - self.last >= 0.2
        ):
            self.flush()

    def flush(self):
        if self.pending and not self.disabled:
            if not self.store.save_delta(self.claim, self.pending, self.offset):
                self.disabled = True
            self.offset += len(self.pending)
            self.pending, self.last = "", time.monotonic()


class AgentRuntime:
    def __init__(self, factory, settings, gateway, prepare_decision=None, execute_tools=None):
        if prepare_decision is None or execute_tools is None:
            from short_drama.agent.tools import (
                execute_tools as executor,
            )
            from short_drama.agent.tools import (
                prepare_decision as prepare,
            )

            prepare_decision = prepare_decision or prepare
            execute_tools = execute_tools or executor
        self.factory, self.settings, self.gateway = factory, settings, gateway
        self.prepare_decision, self.execute_tools = prepare_decision, execute_tools
        self.store = AgentRuntimeStore(factory, settings)

    def execute_one(self, run_id, message_version):
        claim = self.store.claim_execution(int(run_id), int(message_version), self.prepare_decision)
        if claim is None:
            return
        if claim.phase == "tools":
            self.execute_tools(self.factory, self.settings, claim.run_id)
            return
        try:
            asyncio.run(self._decision(claim))
        except LeaseLost:
            return
        except AgentGatewayError as error:
            self.store.fail(claim, error)
        except Exception:
            # The wrapper/recovery owns crashes. Never expose private exception text.
            raise RuntimeError("Agent action interrupted; database recovery required") from None

    async def _decision(self, claim):
        from short_drama.agent.input_media import materialize_prompt
        from short_drama.agent.model_gateway import serialize_segment_result
        from short_drama.core.exceptions import BusinessError
        from short_drama.service.agent_model_service import model_credentials

        key = self.settings.encryption_key
        credential = model_credentials(claim.snapshot, key)
        values = thaw_input(claim.inputs)
        if isinstance(values.get("user_prompt"), dict):
            if claim.raw is not None:
                raise AgentGatewayError("invalid_agent_checkpoint")
            try:
                values["user_prompt"] = await asyncio.to_thread(
                    materialize_prompt, values["user_prompt"], self.settings
                )
            except BusinessError as error:
                raise AgentGatewayError(error.code) from None
            claim.inputs["kwargs"]["user_prompt"] = _json_copy(values["user_prompt"])
        if claim.raw is not None:
            result = await self.gateway.replay_segment(
                claim.snapshot,
                credential,
                request_payload=claim.request,
                raw_response=claim.raw,
                **values,
            )
        else:
            delta = _DeltaBuffer(self.store, claim)

            async def maintain():
                last_heartbeat = time.monotonic()
                while True:
                    await asyncio.sleep(0.2)
                    delta.flush()
                    if time.monotonic() - last_heartbeat >= max(
                        5, self.settings.agent_lease_seconds // 3
                    ):
                        if not self.store.heartbeat(claim):
                            delta.disabled = True
                            return
                        last_heartbeat = time.monotonic()

            async def on_request(payload):
                self.store.before_send(claim, payload)

            async def on_response(payload):
                self.store.save_raw(claim, payload)

            async def on_text_delta(text):
                delta.add(text)

            maintenance = asyncio.create_task(maintain())
            try:
                result = await self.gateway.run_segment(
                    claim.snapshot,
                    credential,
                    on_request=on_request,
                    on_response=on_response,
                    on_text_delta=on_text_delta if values.get("stream") else None,
                    **values,
                )
            finally:
                maintenance.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await maintenance
            delta.add("", final=True)
        normalized = serialize_segment_result(result)
        if self.store.save_result(claim, normalized):
            self.execute_tools(self.factory, self.settings, claim.run_id)


# The public locked helpers are re-exported for the API/tool coordinator.
__all__ = [
    "AgentRuntime",
    "AgentRuntimeStore",
    "LeaseLost",
    "append_event",
    "budget_limits",
    "finish_locked",
    "initial_usage",
    "lock_run",
    "mark_scheduled",
    "may_decide",
    "wait_locked",
]
