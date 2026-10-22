"""Invalid Agent authority and cursor inputs stop before any database or model I/O."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.agent.gateway.protocol import AgentModelGateway
from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.main import create_app


@pytest.mark.parametrize("failure", ["temporary", "resync", "access"])
def test_sse_stream_distinguishes_retry_resync_and_access_end(monkeypatch, failure):
    from short_drama.api.v1.agent import conversation_events
    from short_drama.core.exceptions import DatabaseUnavailable, NotFound, WorkflowError
    from short_drama.service.agent_run_service import AgentRunService

    reads = 0

    def events(*_args, **_kwargs):
        nonlocal reads
        reads += 1
        if reads == 1:
            return [SimpleNamespace(seq=1, model_dump_json=lambda: '{"seq":1}')]
        raise {
            "temporary": DatabaseUnavailable("database temporarily unavailable"),
            "resync": WorkflowError("agent_event_resync_required", "reload snapshot", 409),
            "access": NotFound("conversation unavailable"),
        }[failure]

    @contextmanager
    def factory():
        yield SimpleNamespace(info={})

    async def no_wait(*_args):
        return None

    async def connected():
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(session_factory=factory, settings=SimpleNamespace())
        ),
        state=SimpleNamespace(actor=None),
        is_disconnected=connected,
    )
    monkeypatch.setattr(AgentRunService, "events", events)
    monkeypatch.setattr("short_drama.api.v1.agent.asyncio.sleep", no_wait)

    async def run():
        response = await conversation_events(1, request, None, 0, None)
        return "".join([part async for part in response.body_iterator])

    frames = asyncio.run(run())
    assert "event: agent" in frames
    expected = {"temporary": "retry", "resync": "resync", "access": "access-ended"}[failure]
    assert f"event: {expected}\n" in frames
    if failure != "access":
        assert "access-ended" not in frames


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Validation invoked a database or provider")

    class GuardedSession(Session):
        execute = forbidden
        _execute_internal = forbidden

    monkeypatch.setattr(AgentModelGateway, "run_segment", forbidden)
    monkeypatch.setattr(AgentModelGateway, "validate_capability", forbidden)
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    app.state.session_factory = GuardedSession

    def session():
        with GuardedSession() as value:
            yield value

    app.dependency_overrides[get_session] = session
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize(
    "body,headers",
    [
        ({"content": "Write it"}, {}),
        ({"content": "Write it"}, {"Idempotency-Key": "bad key"}),
        ({"content": "   "}, {"Idempotency-Key": "valid"}),
        ({"content": "Write it", "owner_user_id": "2"}, {"Idempotency-Key": "valid"}),
        ({"content": "Review", "attachment_ids": ["1", "1"]}, {"Idempotency-Key": "valid"}),
        (
            {"content": "Review", "attachment_ids": [str(i) for i in range(1, 18)]},
            {"Idempotency-Key": "valid"},
        ),
        (
            {"content": "Review", "skills": [{"id": "script.v1", "content_version": "1"}] * 2},
            {"Idempotency-Key": "valid"},
        ),
        ({"content": "Review", "video_audio": "ignore"}, {"Idempotency-Key": "valid"}),
        (
            {
                "content": "Discuss",
                "mode": "discuss",
                "task": {"kind": "novel", "instructions": "Write"},
            },
            {"Idempotency-Key": "valid"},
        ),
        (
            {
                "content": "Write",
                "mode": "generate",
                "task": {"kind": "novel", "instructions": "Write", "count": 2},
            },
            {"Idempotency-Key": "valid"},
        ),
        (
            {
                "content": "Generate",
                "mode": "generate",
                "task": {"kind": "image", "instructions": "Draw", "count": True},
            },
            {"Idempotency-Key": "valid"},
        ),
        (
            {
                "content": "Generate",
                "mode": "generate",
                "task": {"kind": "video", "instructions": "Render", "count": 5},
            },
            {"Idempotency-Key": "valid"},
        ),
        (
            {
                "content": "Generate",
                "mode": "generate",
                "task": {"kind": "image", "instructions": "Draw", "project_id": "2"},
            },
            {"Idempotency-Key": "valid"},
        ),
    ],
)
def test_message_validation_cannot_create_authority_or_effects(client, body, headers):
    response = client.post("/api/v1/agent/conversations/1/messages", json=body, headers=headers)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "approved"},
        {"review_version": 0, "review_hash": "a" * 64, "decision": "approved"},
        {"review_version": 1, "review_hash": "wrong", "decision": "approved"},
        {
            "review_version": 1,
            "review_hash": "a" * 64,
            "decision": "approved",
            "steps": [{"kind": "video"}],
        },
    ],
)
def test_review_can_only_accept_frozen_version_hash_and_decision(client, body):
    assert client.post("/api/v1/agent/runs/1/reviews/2", json=body).status_code == 422


def test_model_probe_requires_version_and_forbids_caller_supplied_snapshot(client):
    path = "/api/v1/agent/models/1/verify"
    assert client.post(path, json={}).status_code == 422
    assert (
        client.post(path, json={"row_version": 1, "base_url": "https://other.test"}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"row_version": 1, "image": True, "audio": False, "owner_user_id": "2"},
        {"row_version": 0, "image": True, "audio": False},
    ],
)
def test_model_input_declaration_rejects_invalid_versions_and_authority(client, body):
    assert client.patch("/api/v1/agent/models/1/inputs", json=body).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"row_version": "1"},
        {"row_version": "1", "instructions": "a\x00b"},
        {"row_version": "1", "enabled": None},
        {"row_version": "1", "instructions": "Valid", "owner_user_id": "2"},
    ],
)
def test_skill_management_rejects_empty_or_unauthorized_changes(client, body):
    assert client.patch("/api/v1/agent/skills/1", json=body).status_code == 422


def test_attachment_upload_requires_idempotency_key_before_io(client):
    response = client.post(
        "/api/v1/agent/conversations/1/attachments/uploads",
        files={"file": ("notes.md", b"Notes", "text/markdown")},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("cursor", ["-1", str(2**64)])
def test_sse_query_cursor_must_fit_unsigned_sequence(client, cursor):
    assert (
        client.get("/api/v1/agent/conversations/1/events", params={"cursor": cursor}).status_code
        == 422
    )


@pytest.mark.parametrize("cursor", ["-1", " 1", "1.0", "١", str(2**64)])
def test_sse_reconnect_cursor_is_validated_before_private_lookup(client, cursor):
    response = client.get(
        "/api/v1/agent/conversations/1/events",
        headers={"Last-Event-ID": cursor.encode("utf-8")},
    )
    assert response.status_code == 422
