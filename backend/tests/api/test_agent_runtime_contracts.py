"""Invalid Agent authority and cursor inputs stop before any database or model I/O."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.agent.model_gateway import AgentModelGateway
from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.main import create_app


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
        (
            {"content": "Discuss", "task": {"kind": "novel", "instructions": "Write"}},
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
