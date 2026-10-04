"""Private Agent API/service contracts against disposable MySQL, never a live model."""

import asyncio
import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
import test_agent_conversations as conversation_tests
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from test_agent_conversations import actor, service

from short_drama.agent.authorization import digest, freeze_task
from short_drama.agent.model_gateway import AgentCapabilityEvidence, AgentGatewayError
from short_drama.agent.state import wait_locked
from short_drama.api.v1.agent import conversation_events
from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext, token_hash
from short_drama.domain import AIModelConfig, Episode
from short_drama.domain.agent import (
    AgentArtifact,
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)
from short_drama.domain.collaboration import ProjectMember, User, UserSession
from short_drama.main import create_app
from short_drama.service.agent_model_service import AgentModelService, capability_evidence
from short_drama.service.agent_run_service import AgentRunService
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def settings():
    return Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=True,
        auth_cookie_secure=False,
        public_origin="http://testserver",
        encryption_key=base64.b64encode(b"0" * 32).decode(),
    )


def add_model(factory, owner=1, *, verified=True):
    with factory.begin() as session:
        row = AIModelConfig(
            id=next_id(),
            owner_user_id=owner,
            service_type="text",
            name="Local fixture",
            model_key="fixture",
            provider="compatible",
            base_url="https://fixture.invalid/v1",
            enabled=1,
            is_deleted=0,
        )
        session.add(row)
        session.flush()
        if verified:
            row.capability_cache = {
                "agent": {
                    "row_version": row.row_version,
                    "credential_identity": hashlib.sha256(b"").hexdigest(),
                    "model_key": row.model_key,
                    "base_url": row.base_url,
                    "tool_calling": True,
                    "tool_result_continuation": True,
                    "streaming": "verified",
                }
            }
        return row.id


def setup(factory, project_id, episode_id, owner=1):
    model_id = add_model(factory, owner)
    with factory() as session:
        conversation = service(session, owner).create_conversation(
            {
                "project_id": project_id,
                "episode_id": episode_id,
            }
        )
    return int(conversation.id), model_id


def runs(session, owner=1):
    session.info["actor"] = actor(owner)
    return AgentRunService(session, settings())


def send(
    factory,
    conversation_id,
    model_id,
    *,
    key="message-1",
    content="Discuss this episode.",
    **kwargs,
):
    with factory() as session:
        return runs(session).send_message(
            conversation_id,
            {
                "content": content,
                "model_config_id": model_id,
                **kwargs,
            },
            key,
        )


def pending_review(factory, conversation_id, run_id):
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversation_id)
        run = session.get(AgentRun, run_id)
        step = freeze_task(
            session,
            conversation,
            {"kind": "novel", "instructions": "Write a novel."},
            step_id="step-1",
            owner_user_id=run.initiated_by,
        )
        payload = {"title": "One task", "summary": "Generate one novel candidate.", "steps": [step]}
        turn = AgentTurn(
            id=next_id(),
            run_id=run.id,
            turn_no=1,
            status="succeeded",
            response={"private": "PRIVATE_PROVIDER_SENTINEL"},
        )
        session.add(turn)
        session.flush()
        tool = AgentToolCall(
            id=next_id(),
            run_id=run.id,
            turn_id=turn.id,
            call_index=1,
            provider_call_id="PRIVATE_CALL_SENTINEL",
            tool_name="propose_plan",
            arguments={},
            arguments_hash=digest({}),
            idempotency_key=digest(["review", run.id]),
            status="waiting_review",
            review_payload=payload,
            review_hash=digest(payload),
        )
        session.add(tool)
        wait_locked(session, conversation, run)
        session.flush()
        return tool.id, tool.review_hash


def test_send_is_private_idempotent_and_single_active_without_private_dto_leak(workspace):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    first = send(factory, conversation_id, model_id)
    replay = send(factory, conversation_id, model_id)
    assert replay.message.id == first.message.id and replay.run.id == first.run.id
    with pytest.raises(Conflict):
        send(factory, conversation_id, model_id, content="Different content")
    with pytest.raises(WorkflowError) as active:
        send(factory, conversation_id, model_id, key="message-2")
    assert active.value.code == "agent_run_active"
    with factory() as session:
        other = runs(session, 2)
        with pytest.raises(NotFound):
            other.get_run(first.run.id)
        with pytest.raises(NotFound):
            other.list_messages(conversation_id)
        with pytest.raises(NotFound):
            other.stop(first.run.id)
    tool_id, review_hash = pending_review(factory, conversation_id, int(first.run.id))
    with factory() as session:
        display = runs(session).get_run(first.run.id).model_dump_json()
        assert "PRIVATE" not in display and "credential_cipher" not in display
        assert "checkpoint" not in display and "arguments" not in display
        assert str(tool_id) in display and review_hash in display
        assert session.scalar(select(func.count(AgentMessage.id))) == 1
        assert session.scalar(select(func.count(AgentRun.id))) == 1


@pytest.mark.parametrize(
    "content,task,expected,needs_plan",
    [
        ("先讨论，暂时不要生成", {"kind": "novel", "instructions": "Write it."}, "discuss", False),
        ("Generate a novel and then a storyboard.", None, "discuss", True),
        ("Generate this novel.", {"kind": "novel", "instructions": "Write it."}, "single", False),
    ],
)
def test_server_narrows_mode_and_freezes_only_explicit_single_task(
    workspace, content, task, expected, needs_plan
):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    accepted = send(factory, conversation_id, model_id, content=content, mode="generate", task=task)
    assert accepted.run.mode == expected
    with factory() as session:
        authorization = session.get(AgentRun, int(accepted.run.id)).checkpoint["authorization"]
        assert authorization["requires_plan"] is needs_plan
        assert len(authorization["steps"]) == int(expected == "single")
        if expected == "single":
            assert authorization["steps"][0]["target_id"] == str(episode_id)
            assert authorization["steps"][0]["count"] == 1
            assert "source" in authorization["steps"][0]


def test_message_pagination_returns_chronological_pages_and_stop_replay_is_stable(workspace):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    for index in range(3):
        accepted = send(
            factory, conversation_id, model_id, key=f"message-{index}", content=f"Message {index}"
        )
        with factory() as session:
            stopped = runs(session).stop(accepted.run.id)
            repeated = runs(session).stop(accepted.run.id)
            assert repeated.row_version == stopped.row_version and repeated.status == "cancelled"
    with factory() as session:
        recent = runs(session).list_messages(conversation_id, limit=2)
        older = runs(session).list_messages(conversation_id, offset=2, limit=2)
        assert [item.content for item in recent.items] == ["Message 1", "Message 2"]
        assert [item.content for item in older.items] == ["Message 0"]
        assert recent.total == older.total == 3
    replay = send(factory, conversation_id, model_id, key="message-0", content="Message 0")
    assert replay.run.status == "cancelled"


def test_plan_approval_is_bound_to_version_hash_and_stop_prevents_late_approval(workspace):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    accepted = send(factory, conversation_id, model_id, mode="generate")
    tool_id, review_hash = pending_review(factory, conversation_id, int(accepted.run.id))
    decision = {"review_version": 1, "review_hash": review_hash, "decision": "approved"}
    with factory() as session:
        svc = runs(session)
        with pytest.raises(Conflict):
            svc.review(accepted.run.id, tool_id, {**decision, "review_version": 2})
        with pytest.raises(Conflict):
            svc.review(accepted.run.id, tool_id, {**decision, "review_hash": "0" * 64})
        approved = svc.review(accepted.run.id, tool_id, decision)
        assert approved.mode == "workflow" and approved.phase == "tools"
        assert approved.budget["decision_calls"] == 16
        assert svc.review(accepted.run.id, tool_id, decision).row_version == approved.row_version
        with pytest.raises(Conflict):
            svc.review(accepted.run.id, tool_id, {**decision, "decision": "rejected"})
        svc.stop(accepted.run.id)
    next_run = send(factory, conversation_id, model_id, key="next-run", mode="generate")
    next_tool, next_hash = pending_review(factory, conversation_id, int(next_run.run.id))
    with factory() as session:
        svc = runs(session)
        svc.stop(next_run.run.id)
        with pytest.raises(Conflict):
            svc.review(next_run.run.id, next_tool, {**decision, "review_hash": next_hash})
        assert svc.get_run(next_run.run.id).mode == "discuss"


def test_changed_source_invalidates_review_without_expanding_authorization(workspace):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    accepted = send(factory, conversation_id, model_id, mode="generate")
    tool_id, review_hash = pending_review(factory, conversation_id, int(accepted.run.id))
    with factory.begin() as session:
        session.get(Episode, episode_id).content_version += 1
    with factory() as session:
        with pytest.raises(WorkflowError) as changed:
            runs(session).review(
                accepted.run.id,
                tool_id,
                {
                    "review_version": 1,
                    "review_hash": review_hash,
                    "decision": "approved",
                },
            )
        assert changed.value.code == "agent_source_changed"
        unchanged = runs(session).get_run(accepted.run.id)
        assert unchanged.status == "waiting_review" and unchanged.mode == "discuss"


def test_model_capability_is_explicit_version_bound_and_failed_probe_invalidates_cache(workspace):
    factory, _, _, _ = workspace
    model_id = add_model(factory, verified=False)
    cfg = settings()
    calls = []

    class Probe:
        async def validate_capability(self, snapshot, credential, **kwargs):
            calls.append(snapshot["row_version"])
            await kwargs["on_request"]({})
            await kwargs["on_request"]({})
            return AgentCapabilityEvidence(
                "openai_chat.v1", True, True, streaming="verified", requests=2
            )

    with factory() as session:
        session.info["actor"] = actor(1)
        svc = AgentModelService(session, cfg, gateway=Probe())
        assert not svc.list_models().items[0].verified and calls == []
        with pytest.raises(WorkflowError) as unverified:
            svc.select_model(model_id)
        assert unverified.value.code == "agent_model_unverified"
        session.rollback()
        verified = svc.verify(model_id, {"row_version": 1})
        assert verified.verified and verified.streaming == "verified" and calls == [1]
    with factory.begin() as session:
        row = session.get(AIModelConfig, model_id)
        row.row_version += 1
        row.model_key = "changed-model"
    with factory() as session:
        session.info["actor"] = actor(1)
        svc = AgentModelService(session, cfg, gateway=Probe())
        assert not svc.list_models().items[0].verified
        with pytest.raises(Conflict):
            svc.verify(model_id, {"row_version": 1})
        assert calls == [1]

    class Failure:
        async def validate_capability(self, snapshot, credential, **kwargs):
            await kwargs["on_request"]({})
            raise AgentGatewayError("agent_provider_unknown", requests=1, accepted_unknown=True)

    with factory() as session:
        session.info["actor"] = actor(1)
        with pytest.raises(WorkflowError):
            AgentModelService(session, cfg, gateway=Failure()).verify(model_id, {"row_version": 2})
    with factory() as session:
        row = session.get(AIModelConfig, model_id)
        assert capability_evidence(row) == {}
        assert row.capability_cache["agent_probe"]["requests"] == 1


@pytest.mark.parametrize(
    "credential_cipher,master_key,expected",
    [(None, None, "verified"), ("{}", "invalid", "agent_credential_unavailable")],
)
def test_model_probe_requires_master_key_only_for_encrypted_credentials(
    workspace, credential_cipher, master_key, expected
):
    factory, _, _, _ = workspace
    model_id = add_model(factory, verified=False)
    with factory.begin() as session:
        session.get(AIModelConfig, model_id).apikey = credential_cipher
    cfg = settings()
    cfg.encryption_key = SecretStr(master_key) if master_key else None
    posted = []

    class Probe:
        async def validate_capability(self, snapshot, credential, **kwargs):
            assert credential == ""
            for _ in range(2):
                await kwargs["on_request"]({})
                posted.append(True)
            return AgentCapabilityEvidence("openai_chat.v1", True, True, requests=2)

    with factory() as session:
        session.info["actor"] = actor(1)
        svc = AgentModelService(session, cfg, gateway=Probe())
        if expected == "verified":
            assert svc.verify(model_id, {"row_version": 1}).verified
            assert len(posted) == 2
        else:
            with pytest.raises(WorkflowError) as unavailable:
                svc.verify(model_id, {"row_version": 1})
            assert unavailable.value.code == expected and posted == []
    with factory() as session:
        probe = session.get(AIModelConfig, model_id).capability_cache["agent_probe"]
        assert probe["state"] == ("complete" if expected == "verified" else "failed")
        assert probe["requests"] == len(posted)


@pytest.mark.parametrize("change", ["model", "user", "session", "feature", "request_limit"])
def test_each_probe_request_rechecks_current_authority_and_persists_admission(workspace, change):
    factory, _, _, _ = workspace
    model_id = add_model(factory, verified=False)
    login_id = next_id()
    with factory.begin() as session:
        session.add(
            UserSession(
                id=login_id,
                user_id=1,
                token_hash=token_hash("probe-session"),
                csrf_hash=token_hash("probe-csrf"),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    identity = ActorContext(1, "owner", "owner@example.test", True, login_id, "hash", "probe")
    posted = []
    cfg = settings()

    class Probe:
        async def validate_capability(self, snapshot, credential, **kwargs):
            await kwargs["on_request"]({})
            posted.append(True)
            with factory.begin() as session:
                row = session.get(AIModelConfig, model_id)
                assert row.capability_cache["agent_probe"]["requests"] == 1
                if change == "model":
                    row.row_version += 1
                    row.model_key = "changed-during-probe"
                elif change == "user":
                    session.get(User, 1).status = "disabled"
                elif change == "session":
                    session.get(UserSession, login_id).revoked_at = utcnow()
                elif change == "feature":
                    cfg.agent_enabled = False
            try:
                await kwargs["on_request"]({})
                posted.append(True)
                if change == "request_limit":
                    await kwargs["on_request"]({})
                    posted.append(True)
            except BusinessError:
                # Same safe error contract as the real gateway's admission bridge.
                raise AgentGatewayError(
                    "agent_request_checkpoint_failed", requests=len(posted)
                ) from None
            raise AssertionError("Revoked probe reached a second external request")

    with factory() as session:
        session.info["actor"] = identity
        expected_error = Conflict if change == "model" else WorkflowError
        with pytest.raises(expected_error):
            AgentModelService(session, cfg, gateway=Probe()).verify(model_id, {"row_version": 1})
    assert len(posted) == (2 if change == "request_limit" else 1)
    with factory() as session:
        row = session.get(AIModelConfig, model_id)
        probe = row.capability_cache["agent_probe"]
        assert probe["state"] == "failed" and probe["requests"] == len(posted)
        assert capability_evidence(row) == {}


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_messages_use_current_state_after_serializing_on_scope(
    workspace, monkeypatch, same_key
):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    barrier = Barrier(2)
    original = AgentRunService._conversation

    def synchronized(self, identifier, *, lock=False):
        # Both transactions establish an old RR read view before either acquires Project.
        self.session.scalar(select(AgentConversation.id).where(AgentConversation.id == identifier))
        barrier.wait(timeout=5)
        return original(self, identifier, lock=lock)

    monkeypatch.setattr(AgentRunService, "_conversation", synchronized)

    def submit(index):
        try:
            key = "parallel-same" if same_key else f"parallel-{index}"
            return send(factory, conversation_id, model_id, key=key)
        except WorkflowError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    if same_key:
        assert results[0].message.id == results[1].message.id
        assert results[0].run.id == results[1].run.id
    else:
        assert sum(value == "agent_run_active" for value in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count(AgentRun.id))) == 1


@pytest.mark.parametrize("revocation", ["logout", "membership"])
def test_open_sse_rechecks_session_and_membership_before_emitting_more_private_events(
    workspace, revocation
):
    factory, project_id, episode_id, member_id = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id, owner=2)
    login_id = next_id()
    with factory.begin() as session:
        session.add(
            UserSession(
                id=login_id,
                user_id=2,
                token_hash=token_hash("fixture-session"),
                csrf_hash=token_hash("fixture-csrf"),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    identity = ActorContext(2, "member", "member@example.test", True, login_id, "hash", "fixture")
    with factory() as session:
        session.info["actor"] = identity
        accepted = AgentRunService(session, settings()).send_message(
            conversation_id,
            {
                "content": "Private discussion",
                "model_config_id": model_id,
            },
            "private-event-message",
        )
    request = SimpleNamespace(
        state=SimpleNamespace(actor=identity),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=factory, settings=settings())),
    )

    async def connected():
        return False

    request.is_disconnected = connected

    async def exercise():
        response = await conversation_events(
            str(conversation_id), request, cursor=0, last_event_id=None
        )
        assert response.headers["cache-control"] == "no-store"
        iterator = response.body_iterator
        assert "message.created" in await anext(iterator)
        assert "run.started" in await anext(iterator)
        with factory.begin() as session:
            if revocation == "logout":
                session.get(UserSession, login_id).revoked_at = utcnow()
            else:
                session.get(ProjectMember, member_id).status = "removed"
            conversation = session.get(AgentConversation, conversation_id)
            session.add(
                AgentEvent(
                    id=next_id(),
                    conversation_id=conversation_id,
                    run_id=int(accepted.run.id),
                    seq=conversation.next_event_seq,
                    event_type="message.delta",
                    payload={"text": "SHOULD_NOT_BE_STREAMED"},
                    created_at=utcnow(),
                )
            )
            conversation.next_event_seq += 1
        ending = await asyncio.wait_for(anext(iterator), timeout=3)
        assert "access-ended" in ending and "SHOULD_NOT_BE_STREAMED" not in ending
        await iterator.aclose()

    asyncio.run(exercise())


def test_authenticated_message_api_preserves_private_scope_and_idempotency(workspace):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    cfg = settings()
    app = create_app(cfg)
    app.state.session_factory, app.state.agent_schema_ready = factory, True

    def client(owner):
        token, csrf = f"fixture-token-{owner}", f"fixture-csrf-{owner}"
        with factory.begin() as session:
            session.add(
                UserSession(
                    id=next_id(),
                    user_id=owner,
                    token_hash=token_hash(token),
                    csrf_hash=token_hash(csrf),
                    expires_at=utcnow() + timedelta(days=1),
                    created_at=utcnow(),
                )
            )
        result = TestClient(app)
        result.cookies.set("sd_session", token)
        result.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
        return result

    owner, member = client(1), client(2)
    path = f"/api/v1/agent/conversations/{conversation_id}/messages"
    body = {"content": "Private requirements", "model_config_id": str(model_id)}
    assert owner.post(path, json=body).status_code == 422
    created = owner.post(path, json=body, headers={"Idempotency-Key": "api-message"})
    assert created.status_code == 201, created.text
    repeated = owner.post(path, json=body, headers={"Idempotency-Key": "api-message"})
    assert repeated.json()["run"]["id"] == created.json()["run"]["id"]
    assert (
        owner.post(
            path, json={**body, "content": "Changed"}, headers={"Idempotency-Key": "api-message"}
        ).status_code
        == 409
    )
    assert member.get(path).status_code == 404
    run_path = "/api/v1/agent/runs/" + created.json()["run"]["id"]
    assert member.get(run_path).status_code == 404
    assert member.post(run_path + "/stop").status_code == 404
    assert owner.post(run_path + "/stop").json()["status"] == "cancelled"
    assert "credential_cipher" not in json.dumps(created.json())


def adopted_workflow(workspace, *, pending_read=False):
    """A complete response proposes both steps before the first work is adopted."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart

    from short_drama.agent.model_gateway import serialize_history
    from short_drama.agent.runtime import lock_run
    from short_drama.agent.tools import execute_tools
    from short_drama.service.agent_artifact_service import AgentArtifactService
    from short_drama.service.episode_writing_service import EpisodeWritingService

    factory, project_id, episode_id, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        EpisodeWritingService(session).save_novel(
            project_id, episode_id, {"content_version": 1, "content": "Before adoption"}
        )
    conversation_id, model_id = setup(factory, project_id, episode_id)
    accepted = send(
        factory,
        conversation_id,
        model_id,
        mode="generate",
        task={"kind": "novel", "instructions": "Create a novel"},
    )
    run_id = int(accepted.run.id)
    calls = [
        ("create_candidate", "novel-call", {"step_id": "step-1", "content": "Accepted novel"}),
        ("create_candidate", "old-script-call", {"step_id": "step-2", "content": "STALE_SCRIPT"}),
    ]
    if pending_read:
        calls.append(("read_context", "old-read-call", {"kind": "episode"}))
    history = serialize_history(
        [
            ModelRequest(parts=[UserPromptPart("An approved novel followed by a script")]),
            ModelResponse(
                parts=[ToolCallPart(name, args, call_id) for name, call_id, args in calls]
            ),
        ]
    )
    tool_ids = []
    with factory() as session, session.begin():
        session.info["actor"] = actor(1)
        _, conversation, run = lock_run(session, run_id)
        checkpoint = deepcopy(run.checkpoint)
        first_step = deepcopy(checkpoint["authorization"]["steps"][0])
        first_step["id"] = "step-1"
        step = freeze_task(
            session,
            conversation,
            {"kind": "script", "instructions": "Adapt the adopted novel"},
            step_id="step-2",
            owner_user_id=1,
        )
        checkpoint["authorization"].update(
            mode="workflow",
            approved_plan={"hash": "frozen-plan"},
            steps=[first_step, step],
        )
        checkpoint["history"], checkpoint["pending_results"] = history, {"calls": {}}
        run.checkpoint = checkpoint
        usage = deepcopy(run.usage)
        usage["decision_calls"], usage["tool_calls"] = 1, len(calls)
        run.usage = usage
        turn = AgentTurn(
            id=next_id(),
            run_id=run.id,
            turn_no=1,
            status="succeeded",
            response={
                "raw": {"private": "complete receipt"},
                "normalized": {"output_kind": "tool_requests", "history": history},
                "applied": True,
            },
        )
        session.add(turn)
        session.flush()
        for index, (name, call_id, args) in enumerate(calls, 1):
            frozen = {"raw": json.dumps(args, ensure_ascii=False), "parsed": args}
            tool = AgentToolCall(
                id=next_id(),
                run_id=run.id,
                turn_id=turn.id,
                call_index=index,
                provider_call_id=call_id,
                tool_name=name,
                arguments=frozen,
                arguments_hash=digest(frozen["raw"]),
                idempotency_key=digest(["adopted-workflow", run.id, index]),
            )
            session.add(tool)
            tool_ids.append(tool.id)
        run.status, run.phase, run.message_status = "running", "tools", "idle"
    execute_tools(factory, settings(), run_id)
    with factory.begin() as session:
        _, _, run = lock_run(session, run_id)
        assert run.status == "waiting_review"
        artifact_id = int(run.checkpoint["awaiting_artifacts"][0])
        before_scope = deepcopy(run.checkpoint["authorization"])
        old_args = deepcopy(session.get(AgentToolCall, tool_ids[1]).arguments)
    with factory() as session:
        session.info["actor"] = actor(1)
        adopted = AgentArtifactService(session).adopt(
            project_id, episode_id, artifact_id, {"row_version": 1, "content_version": 2}
        )
    return run_id, artifact_id, adopted["row_version"], tool_ids, old_args, before_scope, history


@pytest.mark.parametrize("pending_read", [False, True])
def test_continue_retires_old_creative_intents_and_fresh_segment_reads_adopted_work(
    workspace, monkeypatch, pending_read
):
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
    from test_agent_runtime import MockGateway

    from short_drama.agent import model_gateway
    from short_drama.agent.model_gateway import deserialize_history, serialize_history
    from short_drama.agent.runtime import AgentRuntime, lock_run
    from short_drama.agent.tools import execute_tools
    from short_drama.domain import EpisodeScript

    factory, project_id, episode_id, _ = workspace
    run_id, artifact_id, artifact_version, ids, old_args, before_scope, history = adopted_workflow(
        workspace, pending_read=pending_read
    )
    with factory() as session:
        resumed = runs(session).continue_after_adoption(
            run_id, {"artifact_id": artifact_id, "artifact_row_version": artifact_version}
        )
        replay = runs(session).continue_after_adoption(
            run_id, {"artifact_id": artifact_id, "artifact_row_version": artifact_version}
        )
    assert resumed.phase == ("tools" if pending_read else "model")
    assert replay.row_version == resumed.row_version
    with factory.begin() as session:
        _, _, run = lock_run(session, run_id)
        old = session.get(AgentToolCall, ids[1])
        assert old.status == "failed" and old.error == {"code": "stale_scope"}
        assert old.arguments == old_args
        assert run.checkpoint["history"] == history
        assert run.usage["tool_calls"] == len(ids)
        scope = deepcopy(run.checkpoint["authorization"])
        assert scope["consumed_steps"] == before_scope["consumed_steps"] == ["step-1"]
        assert scope["approved_plan"] == before_scope["approved_plan"]
        assert scope["steps"][0] == before_scope["steps"][0]
        expected_step = deepcopy(before_scope["steps"][1])
        expected_step["source"]["content_version"] = 3
        assert scope["steps"][1] == expected_step
    # Safe pending reads can finish, but the frozen old script must never run.
    if pending_read:
        execute_tools(factory, settings(), run_id)
    with factory.begin() as session:
        _, _, run = lock_run(session, run_id)
        assert run.phase == "model"
        deferred = deepcopy(run.checkpoint["pending_results"])
        assert deferred["calls"]["old-script-call"]["error"] == "stale_scope"
        assert deferred["calls"]["novel-call"]["artifact_id"] == str(artifact_id)
        if pending_read:
            assert deferred["calls"]["old-read-call"]["novel"] == "Accepted novel"
        assert session.scalar(select(func.count(EpisodeScript.id))) == 0
        version = run.message_version
    monkeypatch.setattr(model_gateway, "serialize_segment_result", lambda result: result)

    def result_for(call_id, name, args, new_history):
        return {
            "output_kind": "tool_requests",
            "output": {
                "calls": [{"tool_call_id": call_id, "tool_name": name, "args": args}],
                "approvals": [],
            },
            "history": new_history,
            "usage": {"output_tokens": 1, "usage_reported": True, "output_tokens_reported": True},
            "protocol": "openai_chat.v1",
            "requests": 1,
        }

    read_history = serialize_history(
        [
            *deserialize_history(history),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        next(
                            name
                            for name, cid in [
                                ("create_candidate", "novel-call"),
                                ("create_candidate", "old-script-call"),
                                ("read_context", "old-read-call"),
                            ]
                            if cid == key
                        ),
                        value,
                        key,
                    )
                    for key, value in deferred["calls"].items()
                ]
            ),
            ModelResponse(parts=[ToolCallPart("read_context", {"kind": "episode"}, "fresh-read")]),
        ]
    )

    class ReadGateway(MockGateway):
        async def run_segment(self, snapshot, credential, **kwargs):
            assert kwargs["history"] == history and kwargs["deferred_results"] == deferred
            return await super().run_segment(snapshot, credential, **kwargs)

    reader = ReadGateway(
        result_for("fresh-read", "read_context", {"kind": "episode"}, read_history)
    )
    AgentRuntime(factory, settings(), reader).execute_one(run_id, version)
    with factory.begin() as session:
        _, _, run = lock_run(session, run_id)
        assert run.phase == "model"
        read_result = run.checkpoint["pending_results"]["calls"]["fresh-read"]
        assert read_result["novel"] == "Accepted novel" and read_result["content_version"] == 3
        version = run.message_version
    fresh_args = {"step_id": "step-2", "content": "Fresh script from Accepted novel"}
    script_history = serialize_history(
        [
            *deserialize_history(read_history),
            ModelRequest(parts=[ToolReturnPart("read_context", read_result, "fresh-read")]),
            ModelResponse(parts=[ToolCallPart("create_candidate", fresh_args, "fresh-script")]),
        ]
    )

    class ScriptGateway(MockGateway):
        async def run_segment(self, snapshot, credential, **kwargs):
            assert kwargs["deferred_results"]["calls"]["fresh-read"] == read_result
            return await super().run_segment(snapshot, credential, **kwargs)

    writer = ScriptGateway(
        result_for("fresh-script", "create_candidate", fresh_args, script_history)
    )
    AgentRuntime(factory, settings(), writer).execute_one(run_id, version)
    assert reader.posts == writer.posts == 1  # local fake transport, never real HTTP
    with factory.begin() as session:
        _, _, run = lock_run(session, run_id)
        assert run.status == "waiting_review"
        assert run.checkpoint["authorization"]["consumed_steps"] == ["step-1", "step-2"]
        assert session.scalar(select(func.count(AgentArtifact.id))) == 2
        candidate = session.scalar(select(EpisodeScript))
        assert candidate.content == "Fresh script from Accepted novel"
        artifact = session.scalar(
            select(AgentArtifact).where(AgentArtifact.script_id == candidate.id)
        )
        assert artifact.source_snapshot["content_version"] == 3
        assert session.get(AgentToolCall, ids[1]).arguments == old_args


def test_continue_does_not_retire_intents_when_owner_or_post_adoption_source_check_fails(workspace):
    from short_drama.service.episode_writing_service import EpisodeWritingService

    factory, project_id, episode_id, _ = workspace
    run_id, artifact_id, version, ids, old_args, _, _ = adopted_workflow(workspace)
    payload = {"artifact_id": artifact_id, "artifact_row_version": version}
    with factory() as session:
        with pytest.raises(NotFound):
            runs(session, 2).continue_after_adoption(run_id, payload)
    with factory() as session:
        session.info["actor"] = actor(2)
        EpisodeWritingService(session).save_novel(
            project_id, episode_id, {"content_version": 3, "content": "Manual edit after adoption"}
        )
    with factory() as session:
        with pytest.raises(Conflict, match="after adoption"):
            runs(session).continue_after_adoption(run_id, payload)
    with factory.begin() as session:
        old = session.get(AgentToolCall, ids[1])
        assert old.status == "prepared" and old.arguments == old_args and old.error is None
        run = session.get(AgentRun, run_id)
        assert run.status == "waiting_review" and "continued_artifacts" not in run.checkpoint


def test_shared_artifact_schema_guard_allows_ready_when_disabled_and_blocks_unready(workspace):
    from sqlalchemy import event

    factory, project_id, episode_id, _ = workspace
    token, csrf = "schema-guard-session", "schema-guard-csrf"
    with factory.begin() as session:
        session.add(
            UserSession(
                id=next_id(),
                user_id=2,
                token_hash=token_hash(token),
                csrf_hash=token_hash(csrf),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    cfg = settings().model_copy(update={"agent_enabled": False})
    app = create_app(cfg)
    app.state.session_factory, app.state.agent_schema_ready = factory, False
    client = TestClient(app)
    client.cookies.set("sd_session", token)
    client.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
    path = f"/api/v1/projects/{project_id}/episodes/{episode_id}/agent-artifacts"
    queries = []

    def capture(_connection, _cursor, statement, *_):
        queries.append(statement.lower())

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for method, suffix, body in [
            ("GET", "", None),
            ("GET", "/123", None),
            ("POST", "/123/adopt", {"row_version": 1, "content_version": 1}),
        ]:
            response = client.request(method, path + suffix, json=body)
            assert (
                response.status_code == 503
                and response.json()["error"]["code"] == "agent_schema_unavailable"
            )
        assert not any("agent_artifacts" in query for query in queries)
        app.state.agent_schema_ready = True
        assert client.get(path).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)
