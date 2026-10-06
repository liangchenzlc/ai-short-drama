"""Agent entry and validation never depend on a provider or an unmigrated table."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.main import create_app


def test_disabled_agent_status_and_mutation_without_agent_schema():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))

    def session():
        with Session() as value:
            value.info["actor"] = ActorContext(1, "test", "test@example.test", True, 1, "", "test")
            yield value

    app.dependency_overrides[get_session] = session
    with TestClient(app, raise_server_exceptions=True) as client:
        status = client.get("/api/v1/agent/status")
        assert status.status_code == 200
        assert status.json() == {"enabled": False, "schema_ready": False}
        response = client.post(
            "/api/v1/agent/conversations",
            json={
                "project_id": "1",
                "episode_id": "2",
                "stage": "source",
                "subject_type": "episode",
                "subject_id": "2",
                "task_type": "writing",
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "agent_disabled"


def test_invalid_agent_payload_is_rejected_without_database_access():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    app.state.session_factory = Session
    client = TestClient(app)
    response = client.post(
        "/api/v1/agent/conversations",
        json={"project_id": "1", "episode_id": "2", "owner_user_id": "3"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert client.patch("/api/v1/agent/conversations/1", json={"row_version": 1}).status_code == 422
