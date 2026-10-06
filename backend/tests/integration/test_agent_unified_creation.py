"""Unified API -> worker -> local HTTP model -> durable candidates on disposable MySQL."""

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import test_agent_conversations as conversation_tests
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_agent_artifacts import linked_asset
from test_agent_conversations import actor, service
from test_agent_services import add_model, runs, send, settings

from short_drama.agent.model_gateway import AgentModelGateway
from short_drama.agent.runtime import AgentRuntime, AgentRuntimeStore
from short_drama.core.identity import token_hash
from short_drama.domain import AIGenerationRecord, AIModelConfig, Asset, AsyncTask, ShotScript
from short_drama.domain.agent import AgentArtifact, AgentMessage, AgentRun, AgentToolCall
from short_drama.domain.collaboration import UserSession
from short_drama.main import create_app
from short_drama.service.base import utcnow
from short_drama.service.shot_script_service import ShotScriptService
from short_drama.tasks import agent_worker
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def stream_reply(*, call=None, text="候选已经生成，请审核后采用。"):
    delta = (
        {"content": text}
        if call is None
        else {
            "tool_calls": [
                {
                    "index": 0,
                    "id": call[0],
                    "type": "function",
                    "function": {
                        "name": call[1],
                        "arguments": json.dumps(call[2], ensure_ascii=False),
                    },
                }
            ]
        }
    )

    def chunk(value, finish=None):
        return {
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "custom",
            "choices": [{"index": 0, "delta": value, "finish_reason": finish}],
        }

    events = [
        chunk(delta),
        chunk({}, "stop" if call is None else "tool_calls"),
        {
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "custom",
            "choices": [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    ]
    return (
        "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
    ).encode()


@pytest.fixture
def tool_provider():
    state = {"responses": [], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            status, body, content_type = state["responses"].pop(0)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def scoped_setup(workspace, base_url, subject="asset"):
    factory, project_id, episode_id, _ = workspace
    model_id = add_model(factory, verified=False)
    if subject == "asset":
        target_id = linked_asset(factory, project_id, episode_id)
    else:
        with factory() as session:
            session.info["actor"] = actor(1)
            target_id = (
                ShotScriptService(session)
                .create(
                    {
                        "episode_id": episode_id,
                        "position": 1,
                        "script": "原镜头",
                        "duration_ms": 3000,
                    }
                )
                .id
            )
    scope = {
        "stage": "assets" if subject == "asset" else "storyboard",
        "subject_type": subject,
        "subject_id": str(target_id),
        "task_type": "creation",
    }
    with factory() as session:
        conversation = service(session).create_conversation(
            {
                "project_id": project_id,
                "episode_id": episode_id,
                **scope,
            }
        )
    with factory.begin() as session:
        session.get(AIModelConfig, model_id).base_url = base_url
    cfg = settings()
    cfg.model_discovery_allowed_hosts = ["127.0.0.1"]
    return factory, int(conversation.id), model_id, target_id, scope, cfg


def api_client(factory, cfg):
    token, csrf = "unified-owner", "unified-csrf"
    with factory.begin() as session:
        session.add(
            UserSession(
                id=next_id(),
                user_id=1,
                token_hash=token_hash(token),
                csrf_hash=token_hash(csrf),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    app = create_app(cfg)
    app.state.session_factory, app.state.agent_schema_ready = factory, True
    client = TestClient(app)
    client.cookies.set("sd_session", token)
    client.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
    return client


def drain(factory, run_id):
    for _ in range(12):
        with factory.begin() as session:
            run = session.get(AgentRun, int(run_id))
            if run.status not in {"queued", "running"}:
                return run.status
            version = run.message_version
        agent_worker.execute_agent.run(str(run_id), str(version))
    pytest.fail("Agent did not settle within its bounded worker steps")


@pytest.mark.parametrize("subject", ["asset", "shot"])
def test_auto_unverified_model_creates_scoped_candidate_through_api_worker_http(
    workspace, tool_provider, monkeypatch, subject
):
    base, provider = tool_provider
    factory, conversation_id, model_id, target_id, scope, cfg = scoped_setup(
        workspace, base, subject
    )
    patch = {"description": "更冷峻的人物"} if subject == "asset" else {"script": "更紧凑的镜头"}
    provider["responses"] = [
        (
            200,
            stream_reply(
                call=(
                    "prepare",
                    "prepare_task",
                    {
                        "kind": "asset_patch" if subject == "asset" else "shot_patch",
                        "instructions": "按要求修改",
                    },
                )
            ),
            "text/event-stream",
        ),
        (
            200,
            stream_reply(
                call=(
                    "candidate",
                    "create_candidate",
                    {
                        "step_id": "single",
                        "patch": patch,
                    },
                )
            ),
            "text/event-stream",
        ),
        (200, stream_reply(), "text/event-stream"),
    ]
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    client = api_client(factory, cfg)
    body = {"content": "请修改当前对象", "model_config_id": str(model_id), "expected_scope": scope}
    path = f"/api/v1/agent/conversations/{conversation_id}/messages"
    accepted = client.post(path, json=body, headers={"Idempotency-Key": "auto-creation"})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["run"]["mode"] == "auto" and provider["requests"] == []
    run_id = accepted.json()["run"]["id"]
    assert drain(factory, run_id) == "succeeded"
    with factory.begin() as session:
        artifact = session.scalar(select(AgentArtifact))
        artifact_id = str(artifact.id)
        assert artifact.status == "ready" and artifact.proposed_patch == patch
        target = session.get(Asset if subject == "asset" else ShotScript, target_id)
        assert getattr(target, "description" if subject == "asset" else "script") != next(
            iter(patch.values())
        )
        assert session.get(AIModelConfig, model_id).capability_cache is None
    messages = client.get(path, params=scope)
    assert messages.status_code == 200, messages.text
    assert messages.json()["items"][-1]["artifacts"] == [
        {"artifact_id": artifact_id, "kind": "asset_patch" if subject == "asset" else "shot_patch"}
    ]
    replay = client.post(path, json=body, headers={"Idempotency-Key": "auto-creation"})
    assert replay.json()["run"]["id"] == run_id and len(provider["requests"]) == 3
    followup = send(
        factory,
        conversation_id,
        model_id,
        key="candidate-explanation",
        content="解释一下刚才的候选",
        expected_scope=scope,
    )
    provider["responses"] = [
        (200, stream_reply(text="这是上一条制作请求的候选。"), "text/event-stream")
    ]
    assert drain(factory, followup.run.id) == "succeeded"
    assert client.get(path, params=scope).json()["items"][-1]["artifacts"] == []


def test_active_messages_queue_and_waiting_review_accepts_question_and_revision(
    workspace, tool_provider, monkeypatch
):
    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    first = send(factory, conversation_id, model_id, expected_scope=scope)
    second = send(factory, conversation_id, model_id, key="queued", expected_scope=scope)
    assert second.run.queue_position == 1
    store = AgentRuntimeStore(factory, cfg)
    assert store.claim_execution(int(second.run.id), 1, None) is None
    with factory() as session:
        assert len(runs(session).runtime_state(conversation_id, scope).queued_runs) == 2
        runs(session).stop(first.run.id)
    provider["responses"] = [(200, stream_reply(text="讨论回复"), "text/event-stream")]
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    assert drain(factory, second.run.id) == "succeeded"
    third = send(factory, conversation_id, model_id, key="plan", expected_scope=scope)
    # A scoped asset plan must target its asset, never the episode.
    with factory.begin() as session:
        from short_drama.agent.authorization import digest, freeze_task
        from short_drama.agent.state import wait_locked
        from short_drama.domain.agent import AgentConversation, AgentTurn

        conversation = session.get(AgentConversation, conversation_id)
        run = session.get(AgentRun, int(third.run.id))
        step = freeze_task(
            session,
            conversation,
            {"kind": "asset_patch", "instructions": "原要求"},
            step_id="step-1",
            owner_user_id=1,
        )
        turn = AgentTurn(id=next_id(), run_id=run.id, turn_no=1, status="succeeded")
        session.add(turn)
        session.flush()
        payload = {"title": "原计划", "summary": "原说明", "steps": [step]}
        tool = AgentToolCall(
            id=next_id(),
            run_id=run.id,
            turn_id=turn.id,
            call_index=1,
            provider_call_id="review",
            tool_name="propose_plan",
            arguments={},
            arguments_hash=digest({}),
            idempotency_key=digest([run.id]),
            status="waiting_review",
            review_payload=payload,
            review_hash=digest(payload),
        )
        session.add(tool)
        wait_locked(session, conversation, run)
        session.flush()
        old_tool_id, old_hash = tool.id, tool.review_hash
    question = send(
        factory,
        conversation_id,
        model_id,
        key="question",
        content="解释一下计划",
        expected_scope=scope,
    )
    provider["responses"] = [(200, stream_reply(text="计划解释"), "text/event-stream")]
    assert drain(factory, question.run.id) == "succeeded"
    with factory() as session:
        assert runs(session).get_run(third.run.id).status == "waiting_review"
    revised = send(
        factory,
        conversation_id,
        model_id,
        key="revise",
        content="改一下计划要求",
        expected_scope=scope,
    )
    provider["responses"] = [
        (
            200,
            stream_reply(
                call=(
                    "new-plan",
                    "propose_plan",
                    {
                        "title": "新计划",
                        "summary": "新说明",
                        "steps": [{"kind": "asset_patch", "instructions": "新要求"}],
                    },
                )
            ),
            "text/event-stream",
        )
    ]
    assert drain(factory, revised.run.id) == "waiting_review"
    with factory() as session:
        assert runs(session).get_run(third.run.id).status == "cancelled"
        from short_drama.core.exceptions import Conflict

        with pytest.raises(Conflict):
            runs(session).review(
                third.run.id,
                old_tool_id,
                {
                    "review_version": 1,
                    "review_hash": old_hash,
                    "decision": "approved",
                },
            )


def plan_response(title, *, steps=None):
    return (
        200,
        stream_reply(
            call=(
                title,
                "propose_plan",
                {
                    "title": title,
                    "summary": "修改当前人物后由用户审核采用",
                    "steps": steps or [{"kind": "asset_patch", "instructions": "修改当前人物"}],
                },
            )
        ),
        "text/event-stream",
    )


def single_candidate_responses():
    return [
        (
            200,
            stream_reply(
                call=(
                    "prepare-single",
                    "prepare_task",
                    {"kind": "asset_patch", "instructions": "修改当前人物"},
                )
            ),
            "text/event-stream",
        ),
        (
            200,
            stream_reply(
                call=(
                    "candidate-single",
                    "create_candidate",
                    {"step_id": "single", "patch": {"description": "新的人物描写"}},
                )
            ),
            "text/event-stream",
        ),
        (200, stream_reply(text="已处理这条消息。"), "text/event-stream"),
    ]


def test_candidate_remains_linked_in_failure_message_after_final_model_request_is_rejected(
    workspace, tool_provider, monkeypatch
):
    base, provider = tool_provider
    factory, conversation_id, model_id, target_id, scope, cfg = scoped_setup(workspace, base)
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    provider["responses"] = [
        *single_candidate_responses()[:-1],
        (
            401,
            json.dumps({"error": {"message": "invalid credential"}}).encode(),
            "application/json",
        ),
    ]
    accepted = send(
        factory, conversation_id, model_id, content="修改当前人物", expected_scope=scope
    )
    assert drain(factory, accepted.run.id) == "failed"
    with factory.begin() as session:
        artifact = session.scalars(select(AgentArtifact)).one()
        artifact_id = str(artifact.id)
        assert artifact.status == "ready" and artifact.created_by == 1
        assert session.get(Asset, target_id).description != "新的人物描写"
    messages = api_client(factory, cfg).get(
        f"/api/v1/agent/conversations/{conversation_id}/messages", params=scope
    )
    assert messages.status_code == 200, messages.text
    last_message = messages.json()["items"][-1]
    assert "身份认证" in last_message["content"]
    assert last_message["artifacts"] == [{"artifact_id": artifact_id, "kind": "asset_patch"}]
    agent_worker.execute_agent.run(str(accepted.run.id), "1")
    assert len(provider["requests"]) == 3 and provider["responses"] == []


def test_rejected_plan_cannot_execute_by_another_tool_but_new_message_can_create(
    workspace, tool_provider, monkeypatch
):
    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    accepted = send(factory, conversation_id, model_id, expected_scope=scope)
    provider["responses"] = [plan_response("被拒绝的计划")]
    assert drain(factory, accepted.run.id) == "waiting_review"
    client = api_client(factory, cfg)
    with factory() as session:
        review = runs(session).get_run(accepted.run.id).review
    rejected = client.post(
        f"/api/v1/agent/runs/{accepted.run.id}/reviews/{review.tool_call_id}",
        params=scope,
        json={
            "review_version": review.review_version,
            "review_hash": review.review_hash,
            "decision": "rejected",
        },
    )
    assert rejected.status_code == 200, rejected.text
    # Deliberately violate the rejection in the local model: the server must stop effects.
    provider["responses"] = single_candidate_responses()
    assert drain(factory, accepted.run.id) == "succeeded"
    with factory.begin() as session:
        assert session.scalar(select(AgentArtifact)) is None
        run = session.get(AgentRun, int(accepted.run.id))
        assert run.checkpoint["authorization"]["mode"] == "discuss"
        assert run.checkpoint["authorization"]["steps"] == []
        assert session.get(AgentToolCall, int(review.tool_call_id)).review_decision == "rejected"
        attempts = session.scalars(
            select(AgentToolCall).where(
                AgentToolCall.run_id == run.id,
                AgentToolCall.tool_name.in_(("prepare_task", "create_candidate")),
            )
        ).all()
        assert len(attempts) == 2 and all(call.status == "failed" for call in attempts)
    fresh = send(
        factory,
        conversation_id,
        model_id,
        key="new-explicit-task",
        content="请重新修改当前人物",
        expected_scope=scope,
    )
    provider["responses"] = single_candidate_responses()
    assert drain(factory, fresh.run.id) == "succeeded"
    with factory.begin() as session:
        assert session.scalar(select(AgentArtifact)).status == "ready"


@pytest.mark.parametrize("action", ["single", "revise"])
def test_queued_supplements_follow_latest_pending_plan_after_original_parent_is_cancelled(
    workspace, tool_provider, monkeypatch, action
):
    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    original = send(factory, conversation_id, model_id, expected_scope=scope)
    provider["responses"] = [plan_response("原计划")]
    assert drain(factory, original.run.id) == "waiting_review"
    first = send(factory, conversation_id, model_id, key="revision-1", expected_scope=scope)
    second = send(factory, conversation_id, model_id, key="revision-2", expected_scope=scope)
    with factory.begin() as session:
        assert session.get(AgentRun, int(second.run.id)).checkpoint["supplement_run_id"] == str(
            original.run.id
        )
    provider["responses"] = [plan_response("第一次修订")]
    assert drain(factory, first.run.id) == "waiting_review"
    provider["responses"] = (
        single_candidate_responses() if action == "single" else [plan_response("第二次修订")]
    )
    assert drain(factory, second.run.id) == (
        "succeeded" if action == "single" else "waiting_review"
    )
    with factory.begin() as session:
        assert session.get(AgentRun, int(original.run.id)).status == "cancelled"
        assert session.scalar(select(AgentArtifact)) is None
        pending = session.scalars(
            select(AgentRun).where(
                AgentRun.conversation_id == conversation_id, AgentRun.status == "waiting_review"
            )
        ).all()
        assert [run.id for run in pending] == [
            int(first.run.id if action == "single" else second.run.id)
        ]
        if action == "revise":
            assert session.get(AgentRun, int(first.run.id)).status == "cancelled"
        else:
            attempts = session.scalars(
                select(AgentToolCall).where(
                    AgentToolCall.run_id == int(second.run.id),
                    AgentToolCall.tool_name.in_(("prepare_task", "create_candidate")),
                )
            ).all()
            assert len(attempts) == 2 and all(call.status == "failed" for call in attempts)


@pytest.mark.parametrize("failure", ["unsupported_protocol", "tools", "unknown"])
def test_runtime_failure_persists_plain_feedback_without_paid_probe_or_resubmission(
    workspace, tool_provider, monkeypatch, failure
):
    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    if failure == "unsupported_protocol":
        with factory.begin() as session:
            session.get(AIModelConfig, model_id).base_url = "ftp://invalid.example"
    else:
        response = {"error": {"message": "tools are not supported"}}
        provider["responses"] = [
            (400 if failure == "tools" else 503, json.dumps(response).encode(), "application/json")
        ]
    accepted = send(factory, conversation_id, model_id, expected_scope=scope)
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    assert drain(factory, accepted.run.id) == "failed"
    with factory.begin() as session:
        messages = session.scalars(
            select(AgentMessage).where(AgentMessage.role == "assistant")
        ).all()
        assert len(messages) == 1
        assert (
            "接口"
            if failure == "unsupported_protocol"
            else "工具"
            if failure == "tools"
            else "重复生成"
        ) in messages[0].content
    previous_calls = len(provider["requests"])
    agent_worker.execute_agent.run(str(accepted.run.id), "1")
    assert len(provider["requests"]) == previous_calls
    assert previous_calls == (0 if failure == "unsupported_protocol" else 1)


def test_unique_single_media_task_is_admitted_without_extra_review(
    workspace, tool_provider, monkeypatch
):
    base, provider = tool_provider
    factory, conversation_id, decision_id, target_id, scope, cfg = scoped_setup(workspace, base)
    with factory.begin() as session:
        model = AIModelConfig(
            id=next_id(),
            owner_user_id=1,
            service_type="image",
            name="Native image fixture",
            provider="ark",
            model_key="fixture",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )
        session.add(model)
        session.get(Asset, target_id).prompt = "人物穿深蓝色外套"
        media_id = model.id
    provider["responses"] = [
        (
            200,
            stream_reply(
                call=(
                    "prepare-image",
                    "prepare_task",
                    {
                        "kind": "image",
                        "instructions": "生成一张当前人物参考图",
                        "model_config_id": str(media_id),
                    },
                )
            ),
            "text/event-stream",
        ),
        (
            200,
            stream_reply(
                call=(
                    "create-image",
                    "create_candidate",
                    {
                        "step_id": "single",
                    },
                )
            ),
            "text/event-stream",
        ),
    ]
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    accepted = send(
        factory, conversation_id, decision_id, content="生成一张人物参考图", expected_scope=scope
    )
    assert drain(factory, accepted.run.id) == "waiting_generation"
    with factory.begin() as session:
        run = session.get(AgentRun, int(accepted.run.id))
        assert run.checkpoint["authorization"]["mode"] == "single"
        assert run.budget["images"] == run.usage["images"] == 1
        task = session.scalar(select(AsyncTask))
        assert task.status == "queued" and task.service_type == "image"
        assert session.scalar(select(AIGenerationRecord)).status == "prepared"
        assert not session.scalars(
            select(AgentToolCall).where(AgentToolCall.status == "waiting_review")
        ).all()
        from types import SimpleNamespace

        from short_drama.agent.tools import _execute
        from short_drama.domain.agent import AgentConversation

        status = _execute(
            session,
            session.get(AgentConversation, conversation_id),
            run,
            SimpleNamespace(
                tool_name="read_task_status", arguments={"parsed": {"run_id": accepted.run.id}}
            ),
            cfg,
        )
        assert status["generation_tasks"][0]["id"] == str(task.id)
        assert status["generation_tasks"][0]["acceptance"] == "prepared"
    assert len(provider["requests"]) == 2


def test_queued_turn_refreshes_prior_reply_without_future_user_instructions(
    workspace, tool_provider, monkeypatch
):
    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    first = send(factory, conversation_id, model_id, expected_scope=scope)
    second = send(
        factory,
        conversation_id,
        model_id,
        key="followup",
        content="继续解释上一条",
        expected_scope=scope,
    )
    send(
        factory,
        conversation_id,
        model_id,
        key="future",
        content="FUTURE_USER_SENTINEL",
        expected_scope=scope,
    )
    provider["responses"] = [
        (200, stream_reply(text="PRIOR_ASSISTANT_REPLY"), "text/event-stream"),
        (200, stream_reply(text="当前追问回复"), "text/event-stream"),
    ]
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    assert drain(factory, first.run.id) == "succeeded"
    assert drain(factory, second.run.id) == "succeeded"
    sent = json.dumps(provider["requests"][-1], ensure_ascii=False)
    assert "PRIOR_ASSISTANT_REPLY" in sent
    assert "FUTURE_USER_SENTINEL" not in sent


def test_continued_parent_waits_for_running_supplement_without_mutual_queue_block(workspace):
    from test_agent_services import adopted_workflow

    from short_drama.agent.runtime import preceding_execution
    from short_drama.domain.agent import AgentConversation

    factory, _, _, _ = workspace
    run_id, artifact_id, artifact_version, *_ = adopted_workflow(workspace)
    with factory.begin() as session:
        parent = session.get(AgentRun, run_id)
        conversation_id, model_id = parent.conversation_id, parent.model_config_id
    supplementary = send(
        factory, conversation_id, model_id, key="extra-question", content="解释候选"
    )
    with factory.begin() as session:
        session.get(AgentRun, int(supplementary.run.id)).status = "running"
    with factory() as session:
        continued = runs(session).continue_after_adoption(
            run_id,
            {
                "artifact_id": artifact_id,
                "artifact_row_version": artifact_version,
            },
        )
        assert continued.status == "queued"
    with factory.begin() as session:
        parent = session.get(AgentRun, run_id)
        child = session.get(AgentRun, int(supplementary.run.id))
        assert preceding_execution(session, parent) == child.id
        assert preceding_execution(session, child) is None
        from short_drama.agent.state import finish_locked

        finish_locked(session, session.get(AgentConversation, conversation_id), child, "succeeded")
    with factory.begin() as session:
        assert preceding_execution(session, session.get(AgentRun, run_id)) is None


def test_text_without_candidate_cannot_complete_an_explicit_single_task(
    workspace, tool_provider, monkeypatch
):
    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    provider["responses"] = [(200, stream_reply(text="已经完成修改"), "text/event-stream")]
    accepted = send(
        factory,
        conversation_id,
        model_id,
        content="修改当前人物",
        expected_scope=scope,
        task={"kind": "asset_patch", "instructions": "修改人物"},
    )
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    assert drain(factory, accepted.run.id) == "failed"
    with factory.begin() as session:
        assert (
            session.get(AgentRun, int(accepted.run.id)).error["code"] == "agent_task_not_executed"
        )
        assert session.scalar(select(AgentArtifact)) is None
        assert (
            "尚未执行"
            in session.scalar(select(AgentMessage).where(AgentMessage.role == "assistant")).content
        )
    assert len(provider["requests"]) == 1


@pytest.mark.parametrize("complete_first_step", [False, True])
def test_text_cannot_complete_an_approved_workflow_with_unexecuted_steps(
    workspace, tool_provider, monkeypatch, complete_first_step
):
    from short_drama.service.agent_artifact_service import AgentArtifactService

    base, provider = tool_provider
    factory, conversation_id, model_id, _, scope, cfg = scoped_setup(workspace, base)
    monkeypatch.setattr(
        agent_worker, "runtime", lambda: AgentRuntime(factory, cfg, AgentModelGateway(cfg))
    )
    accepted = send(factory, conversation_id, model_id, expected_scope=scope)
    provider["responses"] = [
        plan_response(
            "分两步修改人物",
            steps=[
                {"kind": "asset_patch", "instructions": "调整人物描写"},
                {"kind": "asset_patch", "instructions": "补充人物提示词"},
            ],
        )
    ]
    assert drain(factory, accepted.run.id) == "waiting_review"
    with factory() as session:
        svc = runs(session)
        review = svc.get_run(accepted.run.id).review
        svc.review(
            accepted.run.id,
            review.tool_call_id,
            {
                "review_version": review.review_version,
                "review_hash": review.review_hash,
                "decision": "approved",
            },
        )
    if complete_first_step:
        provider["responses"] = [
            (
                200,
                stream_reply(
                    call=(
                        "first-candidate",
                        "create_candidate",
                        {"step_id": "step-1", "patch": {"description": "第一步的人物描写"}},
                    )
                ),
                "text/event-stream",
            )
        ]
        assert drain(factory, accepted.run.id) == "waiting_review"
        with factory.begin() as session:
            artifact = session.scalar(select(AgentArtifact))
            artifact_id = artifact.id
            payload = {
                "row_version": artifact.row_version,
                "content_version": artifact.source_snapshot["content_version"],
                "target_row_version": artifact.source_snapshot["target_row_version"],
                "confirm_shared": True,
            }
        _, project_id, episode_id, _ = workspace
        with factory() as session:
            session.info["actor"] = actor(1)
            adopted = AgentArtifactService(session).adopt(
                project_id, episode_id, artifact_id, payload, expected_scope=scope
            )
            runs(session).continue_after_adoption(
                accepted.run.id,
                {"artifact_id": artifact_id, "artifact_row_version": adopted["row_version"]},
            )
    provider["responses"] = [(200, stream_reply(text="全部计划已经完成"), "text/event-stream")]
    assert drain(factory, accepted.run.id) == "failed"
    with factory.begin() as session:
        run = session.get(AgentRun, int(accepted.run.id))
        assert run.error["code"] == "agent_plan_not_completed"
        assert run.checkpoint["authorization"]["consumed_steps"] == (
            ["step-1"] if complete_first_step else []
        )
        assert len(session.scalars(select(AgentArtifact)).all()) == int(complete_first_step)
        assert (
            "计划尚未执行完"
            in session.scalar(
                select(AgentMessage)
                .where(AgentMessage.role == "assistant")
                .order_by(AgentMessage.seq.desc())
            ).content
        )
