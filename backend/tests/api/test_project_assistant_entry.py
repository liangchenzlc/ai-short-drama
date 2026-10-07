"""Project assistant validation fails before any database or provider call."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.main import create_app


@pytest.fixture
def client():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))

    def session():
        with Session() as value:
            value.info["actor"] = ActorContext(1, "test", "test@example.test", True, 1, "", "test")
            yield value

    app.dependency_overrides[get_session] = session
    with TestClient(app) as client:
        yield client


def test_assistant_status_and_disabled_creation_never_invokes_a_model(client):
    assert client.get("/api/v1/assistant/status").json() == {
        "enabled": False,
        "schema_ready": False,
    }
    response = client.post("/api/v1/assistant/conversations/resolve", json={"project_id": "1"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_disabled"


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "generate"},
        {"task": {"kind": "image"}},
        {"owner_user_id": "2"},
        {"context": {"kind": "canvas", "id": "c", "revision": "1", "nodes": []}},
    ],
)
def test_chat_rejects_execution_and_untrusted_source_content(client, extra):
    response = client.post(
        "/api/v1/assistant/conversations/1/messages",
        json={"content": "讨论作品", **extra},
        headers={"Idempotency-Key": "message-1"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
