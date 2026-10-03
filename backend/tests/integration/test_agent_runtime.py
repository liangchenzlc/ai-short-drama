"""Agent scheduling with real disposable MySQL and a model/broker that never uses HTTP."""

import base64
import json
import threading
import time
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic_ai.tools import ToolDefinition
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from short_drama.agent import model_gateway
from short_drama.agent import runtime as runtime_module
from short_drama.agent.publisher import AgentPublisher
from short_drama.agent.recovery import recover
from short_drama.agent.runtime import AgentRuntime, AgentRuntimeStore, lock_run
from short_drama.agent.state import budget_limits, finish_locked, initial_usage, mark_scheduled
from short_drama.domain import AIModelConfig
from short_drama.domain.agent import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration


@pytest.fixture
def runtime_setup(db_session, monkeypatch):
    from short_drama.service.ai_model_config_service import AIModelConfigService
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.project_service import ProjectService

    project = ProjectService(db_session).create({"name": "Runtime", "aspect": "16:9"})
    episode = EpisodeService(db_session).create(
        {"project_id": project.id, "title": "One", "position": 1, "aspect": "16:9"}
    )
    model = AIModelConfigService(db_session).create(
        {
            "service_type": "text",
            "name": "Local mock",
            "provider": "compatible",
            "model_key": "mock",
            "base_url": "https://model.invalid/v1",
        }
    )
    import hashlib

    with db_session.begin():
        conversation = AgentConversation(
            id=10,
            owner_user_id=1,
            project_id=project.id,
            episode_id=episode.id,
            title="Private",
            next_message_seq=2,
        )
        db_session.add(conversation)
        db_session.flush()
        db_session.add(
            AgentMessage(id=11, conversation_id=10, seq=1, role="user", content="Discuss")
        )
        db_session.flush()
        db_session.add(
            AgentRun(
                id=12,
                conversation_id=10,
                trigger_message_id=11,
                initiated_by=1,
                model_config_id=model.id,
                config_snapshot={
                    "service_type": "text",
                    "model_key": model.model_key,
                    "base_url": model.base_url,
                    "row_version": model.row_version,
                    "credential_identity": hashlib.sha256(b"").hexdigest(),
                    "credential_cipher": None,
                },
                checkpoint={
                    "schema_version": 1,
                    "authorization": {"mode": "discuss", "approved_plan": None},
                    "history": None,
                },
                budget=budget_limits(),
                usage=initial_usage(),
                next_run_at=utcnow(),
            )
        )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    settings = SimpleNamespace(
        agent_enabled=True,
        agent_lease_seconds=180,
        agent_poll_seconds=5,
        generation_publish_lease_seconds=15,
        encryption_key=None,
    )
    monkeypatch.setattr(
        model_gateway, "serialize_segment_result", lambda result: result, raising=False
    )
    return factory, settings


def prepare(session, conversation, run):
    return {
        "instructions": "Only discuss or read authorized context.",
        "user_prompt": "Discuss" if not run.checkpoint.get("history") else None,
        "history": run.checkpoint.get("history"),
        "tools": [ToolDefinition(name="read_context", parameters_json_schema={"type": "object"})],
        "deferred_results": run.checkpoint.get("pending_results"),
        "conversation_id": str(conversation.id),
        "max_output_tokens": 8192,
        "max_tool_calls": 16,
        "timeout_seconds": 120,
    }


def normalized(*, tools=False, reported=True, tokens=3):
    return {
        "output_kind": "tool_requests" if tools else "text",
        "output": {
            "calls": [
                {
                    "tool_call_id": "call1",
                    "tool_name": "read_context",
                    "args": '{ "optional": null }',
                },
                {"tool_call_id": "call2", "tool_name": "read_context", "args": "not-json"},
            ],
            "approvals": [],
        }
        if tools
        else "A safe reply.",
        "history": {"private": "protocol state"},
        "usage": {
            "output_tokens": tokens,
            "output_tokens_reported": reported,
            "usage_reported": reported,
        },
        "protocol": "openai_chat.v1",
        "requests": 1,
    }


class MockGateway:
    def __init__(self, result=None, crash=None, before_admission=None, after_send=None):
        self.result = result or normalized()
        self.crash, self.before_admission, self.after_send = crash, before_admission, after_send
        self.posts, self.replays = 0, 0

    async def run_segment(
        self, snapshot, credential, *, on_request, on_response, on_text_delta, **kwargs
    ):
        if self.before_admission:
            self.before_admission()
        await on_request(
            {
                "codec": "agent.request",
                "version": 1,
                "protocol": "openai_chat.v1",
                "url": snapshot["base_url"] + "/chat/completions",
                "body": {"model": snapshot["model_key"], "stream": kwargs["stream"]},
            }
        )
        self.posts += 1
        if self.after_send:
            self.after_send()
        if self.crash == "sent":
            raise RuntimeError("Simulated worker termination")
        await on_text_delta("A safe ")
        await on_response(
            {
                "codec": "agent.raw-response",
                "version": 1,
                "status_code": 200,
                "content_type": "application/json",
                "body_b64": base64.b64encode(json.dumps({"result": self.result}).encode()).decode(),
            }
        )
        if self.crash == "raw":
            raise RuntimeError("Simulated normalization interruption")
        await on_text_delta("reply.")
        return deepcopy(self.result)

    async def replay_segment(
        self, snapshot, credential, *, request_payload, raw_response, **kwargs
    ):
        self.replays += 1
        assert request_payload["codec"] == "agent.request"
        return json.loads(base64.b64decode(raw_response["body_b64"]))["result"]


def unused_tools(*args):
    raise AssertionError("A text response must not execute a tool")


def expire(factory):
    with factory.begin() as session:
        _project, _conversation, run = lock_run(session, 12)
        run.lease_until = utcnow() - timedelta(seconds=1)


def version(factory):
    with factory.begin() as session:
        return session.get(AgentRun, 12).message_version


def test_model_result_is_durable_once_and_duplicate_delivery_has_no_second_post(runtime_setup):
    factory, settings = runtime_setup
    gateway = MockGateway()
    runtime = AgentRuntime(factory, settings, gateway, prepare, unused_tools)
    runtime.execute_one(12, 1)
    runtime.execute_one(12, 1)
    with factory.begin() as session:
        run, turn = session.get(AgentRun, 12), session.scalar(select(AgentTurn))
        assert run.status == "succeeded" and gateway.posts == 1
        assert run.usage["decision_calls"] == 1 and run.usage["output_tokens"] == 3
        assert run.usage["reserved_output_tokens"] == 0
        assert turn.status == "succeeded" and turn.response["applied"] is True
        assert turn.request_messages[1]["codec"] == "agent.request"
        assert turn.response["raw"]["codec"] == "agent.raw-response"
        assert (
            session.scalars(select(AgentMessage).where(AgentMessage.role == "assistant")).one().seq
            == 2
        )
        seq = session.scalars(select(AgentEvent.seq).order_by(AgentEvent.seq)).all()
        assert seq == list(range(1, len(seq) + 1))


def test_intents_are_saved_and_reserved_before_tool_executor(runtime_setup):
    factory, settings = runtime_setup
    gateway = MockGateway(normalized(tools=True))
    observed = []

    def execute_tools(_factory, _settings, run_id):
        with factory.begin() as session:
            _project, _conversation, run = lock_run(session, run_id)
            turn = session.scalar(select(AgentTurn))
            assert turn.response["raw"] and turn.response["normalized"] and turn.response["applied"]
            tools = session.scalars(select(AgentToolCall).order_by(AgentToolCall.call_index)).all()
            assert run.usage["tool_calls"] == 2
            assert tools[0].arguments == {
                "raw": '{ "optional": null }',
                "parsed": {"optional": None},
            }
            assert tools[1].arguments == {"raw": "not-json", "parsed": None}
            assert len({tool.idempotency_key for tool in tools}) == 2
            observed.extend(tools)
            for tool in tools:
                tool.status, tool.result = "succeeded", {"output": {"read": True}}
            run.checkpoint = {
                **run.checkpoint,
                "pending_results": {
                    "calls": {tool.provider_call_id: tool.result["output"] for tool in tools}
                },
            }
            mark_scheduled(run)

    AgentRuntime(factory, settings, gateway, prepare, execute_tools).execute_one(12, 1)
    assert len(observed) == 2 and gateway.posts == 1
    gateway.result = normalized()
    AgentRuntime(factory, settings, gateway, prepare, unused_tools).execute_one(
        12, version(factory)
    )
    with factory.begin() as session:
        assert session.get(AgentRun, 12).status == "succeeded"
        assert len(session.scalars(select(AgentTurn)).all()) == 2
    assert gateway.posts == 2


def test_sent_without_complete_raw_is_unknown_and_never_reposted(runtime_setup):
    factory, settings = runtime_setup
    gateway = MockGateway(crash="sent")
    runtime = AgentRuntime(factory, settings, gateway, prepare, unused_tools)
    with pytest.raises(RuntimeError, match="recovery required"):
        runtime.execute_one(12, 1)
    expire(factory)
    assert recover(factory, settings) == 1
    runtime.execute_one(12, version(factory))
    with factory.begin() as session:
        run, turn = session.get(AgentRun, 12), session.scalar(select(AgentTurn))
        assert run.status == "failed" and turn.status == "unknown"
        assert run.error == {"code": "agent_acceptance_unknown"}
        assert run.usage["reserved_output_tokens"] == 8192
    assert gateway.posts == 1 and gateway.replays == 0


def test_complete_raw_recovers_locally_without_another_paid_request(runtime_setup):
    factory, settings = runtime_setup
    gateway = MockGateway(crash="raw")
    runtime = AgentRuntime(factory, settings, gateway, prepare, unused_tools)
    with pytest.raises(RuntimeError):
        runtime.execute_one(12, 1)
    expire(factory)
    assert recover(factory, settings) == 1
    runtime.execute_one(12, version(factory))
    assert gateway.posts == 1 and gateway.replays == 1
    with factory.begin() as session:
        assert session.get(AgentRun, 12).status == "succeeded"
        assert session.get(AgentRun, 12).usage["decision_calls"] == 1
        assert len(session.scalars(select(AgentTurn)).all()) == 1


def test_prepared_recovery_reuses_frozen_input_and_duplicate_live_claim_is_denied(runtime_setup):
    factory, settings = runtime_setup
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(12, 1, prepare)
    assert store.claim_execution(12, 1, prepare) is None
    expire(factory)
    assert recover(factory, settings) == 1
    gateway = MockGateway()

    def changed_builder(*args):
        raise AssertionError("Recovery must use the persisted request")

    AgentRuntime(factory, settings, gateway, changed_builder, unused_tools).execute_one(
        12, version(factory)
    )
    with factory.begin() as session:
        assert (
            session.get(AgentTurn, claim.turn_id).request_messages[0]["kwargs"]["user_prompt"]
            == "Discuss"
        )
        assert session.get(AgentRun, 12).status == "succeeded"
    assert gateway.posts == 1


def test_stop_after_submit_saves_late_evidence_without_message_or_tools(runtime_setup):
    factory, settings = runtime_setup

    def stop():
        with factory.begin() as session:
            _project, conversation, run = lock_run(session, 12)
            run.cancel_requested = 1
            finish_locked(session, conversation, run, "cancelled")

    gateway = MockGateway(normalized(tools=True), after_send=stop)
    AgentRuntime(factory, settings, gateway, prepare, unused_tools).execute_one(12, 1)
    with factory.begin() as session:
        run, turn = session.get(AgentRun, 12), session.scalar(select(AgentTurn))
        assert run.status == "cancelled" and run.usage["output_tokens"] == 3
        assert turn.response["raw"] and turn.response["normalized"] and not turn.response["applied"]
        assert session.scalars(select(AgentToolCall)).all() == []
        assert (
            session.scalars(select(AgentMessage).where(AgentMessage.role == "assistant")).all()
            == []
        )


@pytest.mark.parametrize("change", ["model", "user"])
def test_submit_rechecks_current_model_and_identity_before_reservation(runtime_setup, change):
    factory, settings = runtime_setup

    def revoke():
        with factory.begin() as session:
            if change == "model":
                model = session.scalar(select(AIModelConfig))
                model.enabled, model.row_version = 0, model.row_version + 1
            else:
                from short_drama.domain.collaboration import User

                session.get(User, 1).status = "disabled"

    gateway = MockGateway(before_admission=revoke)
    AgentRuntime(factory, settings, gateway, prepare, unused_tools).execute_one(12, 1)
    with factory.begin() as session:
        run = session.get(AgentRun, 12)
        assert run.status == "failed" and run.error == {"code": "agent_admission_denied"}
        assert run.usage["decision_calls"] == run.usage["reserved_output_tokens"] == 0
    assert gateway.posts == 0


@pytest.mark.parametrize(
    "counter,amount",
    [("decision_calls", 8), ("reserved_output_tokens", 32768), ("active_seconds", 300)],
)
def test_limits_are_enforced_before_model_submission(runtime_setup, counter, amount):
    factory, settings = runtime_setup
    with factory.begin() as session:
        run = session.get(AgentRun, 12)
        run.usage = {**run.usage, counter: amount}
    gateway = MockGateway()
    AgentRuntime(factory, settings, gateway, prepare, unused_tools).execute_one(12, 1)
    with factory.begin() as session:
        assert session.get(AgentRun, 12).status == "failed"
        assert session.get(AgentRun, 12).error == {"code": "agent_budget_exhausted"}
        assert session.scalars(select(AgentTurn)).all() == []
    assert gateway.posts == 0


def test_missing_usage_keeps_reservation(runtime_setup):
    factory, settings = runtime_setup
    gateway = MockGateway(normalized(reported=False, tokens=0))
    AgentRuntime(factory, settings, gateway, prepare, unused_tools).execute_one(12, 1)
    with factory.begin() as session:
        assert session.get(AgentRun, 12).usage["reserved_output_tokens"] == 8192
        assert session.get(AgentRun, 12).usage["output_tokens"] == 0
        assert session.scalar(select(AgentTurn)).usage["settled"] is False


def test_publish_confirm_race_cannot_overwrite_completed_run(runtime_setup):
    factory, settings = runtime_setup
    gateway = MockGateway()
    runtime = AgentRuntime(factory, settings, gateway, prepare, unused_tools)
    publisher = AgentPublisher(
        factory, settings, send=lambda run_id, version: runtime.execute_one(run_id, version)
    )
    assert publisher.tick()
    assert not publisher.tick()
    with factory.begin() as session:
        run = session.get(AgentRun, 12)
        assert (
            run.status == "succeeded" and run.message_status == "idle" and run.lease_token is None
        )
    assert gateway.posts == 1


def test_late_normalization_before_recovery_does_not_lose_local_finalization(runtime_setup):
    factory, settings = runtime_setup
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(12, 1, prepare)
    store.before_send(claim, {"codec": "agent.request", "version": 1, "body": {}})
    raw = {
        "codec": "agent.raw-response",
        "body_b64": base64.b64encode(json.dumps({"result": normalized()}).encode()).decode(),
    }
    store.save_raw(claim, raw)
    expire(factory)
    assert store.save_result(claim, normalized()) is False
    assert recover(factory, settings) == 1
    gateway = MockGateway()
    AgentRuntime(factory, settings, gateway, prepare, unused_tools).execute_one(
        12, version(factory)
    )
    with factory.begin() as session:
        run = session.get(AgentRun, 12)
        assert run.status == "succeeded" and run.usage["output_tokens"] == 3
        assert len(session.scalars(select(AgentTurn)).all()) == 1
        assert (
            len(session.scalars(select(AgentMessage).where(AgentMessage.role == "assistant")).all())
            == 1
        )
    assert gateway.posts == 0 and gateway.replays == 1


@pytest.fixture
def local_provider():
    state = {"posts": 0, "gate": threading.Event(), "stream": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posts"] += 1
            state["stream"] = request.get("stream", False)
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if state["stream"] else "application/json"
            )
            self.send_header("Connection", "close")
            self.end_headers()
            if state["stream"]:

                def event(delta, finish=None, usage=None):
                    value = {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "mock",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    if usage is not None:
                        value["usage"] = usage
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()

                event({"role": "assistant", "content": "Visible partial "})
                state["gate"].wait(5)
                event({"content": "reply."})
                event({}, "stop", {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                body = {
                    "id": "chatcmpl-local",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "mock",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "A safe reply."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
                self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        state["gate"].set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def configure_local_gateway(factory, settings, base_url):
    with factory.begin() as session:
        model, run = session.scalar(select(AIModelConfig)), session.get(AgentRun, 12)
        model.base_url, model.row_version = base_url, model.row_version + 1
        run.config_snapshot = {
            **run.config_snapshot,
            "base_url": base_url,
            "row_version": model.row_version,
        }
    settings.generation_allowed_hosts = ["127.0.0.1"]
    settings.generation_max_response_bytes = 65536
    return model_gateway.AgentModelGateway(settings)


def test_real_gateway_raw_hooks_and_local_replay_use_one_local_post(
    runtime_setup, local_provider, monkeypatch
):
    factory, settings = runtime_setup
    base_url, state = local_provider
    monkeypatch.undo()
    gateway = configure_local_gateway(factory, settings, base_url)

    def plain_prepare(*args):
        return {**prepare(*args), "stream": False}

    runtime = AgentRuntime(factory, settings, gateway, plain_prepare, unused_tools)
    original_serializer = model_gateway.serialize_segment_result

    def crash_normalization(result):
        raise RuntimeError("Test process interruption after durable raw")

    monkeypatch.setattr(model_gateway, "serialize_segment_result", crash_normalization)
    with pytest.raises(RuntimeError, match="recovery required"):
        runtime.execute_one(12, 1)
    with factory.begin() as session:
        turn = session.scalar(select(AgentTurn))
        assert turn.response["raw"]["status_code"] == 200
        assert turn.status == "sent" and "normalized" not in turn.response
        assert "authorization" not in json.dumps(turn.request_messages).lower()
    monkeypatch.setattr(model_gateway, "serialize_segment_result", original_serializer)
    expire(factory)
    assert recover(factory, settings) == 1
    runtime.execute_one(12, version(factory))
    with factory.begin() as session:
        run, turn = session.get(AgentRun, 12), session.scalar(select(AgentTurn))
        assert run.status == "succeeded" and run.usage["output_tokens"] == 3
        assert turn.response["normalized"]["usage"]["external_requests"] == 0
    assert state["posts"] == 1


def test_real_stream_persists_display_delta_before_eof_and_complete_raw(
    runtime_setup, local_provider, monkeypatch
):
    factory, settings = runtime_setup
    base_url, state = local_provider
    monkeypatch.undo()
    gateway = configure_local_gateway(factory, settings, base_url)
    runtime = AgentRuntime(factory, settings, gateway, prepare, unused_tools)
    errors = []

    def execute():
        try:
            runtime.execute_one(12, 1)
        except Exception as error:
            errors.append(type(error).__name__)

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 4
        observed = False
        while time.monotonic() < deadline:
            with factory.begin() as session:
                deltas = session.scalars(
                    select(AgentEvent).where(AgentEvent.event_type == "assistant.delta")
                ).all()
                if deltas:
                    assert "Visible partial" in deltas[0].payload["delta"]
                    assert session.scalar(select(AgentTurn)).response is None
                    observed = True
                    break
            time.sleep(0.05)
        assert observed, "The first visible stream text must not wait for provider EOF"
    finally:
        state["gate"].set()
        worker.join(8)
    assert not worker.is_alive() and errors == []
    with factory.begin() as session:
        assert session.get(AgentRun, 12).status == "succeeded"
        assert session.scalar(select(AgentTurn)).response["normalized"]["streaming"] is True
        assert session.get(AgentRun, 12).usage["reserved_output_tokens"] == 0
    assert state["posts"] == 1 and state["stream"]


def test_admission_uses_current_model_after_an_older_mysql_read_view(runtime_setup, monkeypatch):
    factory, settings = runtime_setup
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(12, 1, prepare)
    ready, proceed = threading.Event(), threading.Event()
    original_lock = runtime_module.lock_run
    errors = []

    def gated_lock(session, run_id, **kwargs):
        # Create the same consistent-read view as the parent lookup, before a
        # concurrent model edit commits while waiting for Project ownership.
        session.scalar(select(AgentRun.id).where(AgentRun.id == run_id))
        ready.set()
        assert proceed.wait(5)
        return original_lock(session, run_id, **kwargs)

    monkeypatch.setattr(runtime_module, "lock_run", gated_lock)

    def admit():
        try:
            store.before_send(claim, {"codec": "agent.request", "body": {}})
        except model_gateway.AgentGatewayError as error:
            errors.append(error.code)

    thread = threading.Thread(target=admit, daemon=True)
    thread.start()
    try:
        assert ready.wait(5)
        with factory.begin() as session:
            model = session.scalar(select(AIModelConfig))
            model.enabled, model.row_version = 0, model.row_version + 1
    finally:
        proceed.set()
        thread.join(5)
    assert not thread.is_alive() and errors == ["agent_admission_denied"]
    with factory.begin() as session:
        assert session.get(AgentRun, 12).usage["decision_calls"] == 0
        assert session.get(AgentTurn, claim.turn_id).status == "prepared"
