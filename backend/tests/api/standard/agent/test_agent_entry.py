"""Agent entry and validation never depend on a provider or an unmigrated table."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.core.config import Settings
from short_drama.core.identity import SessionActorContext
from short_drama.main import create_app


def test_disabled_agent_status_and_mutation_without_agent_schema():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))

    def session():
        with Session() as value:
            value.info["actor"] = SessionActorContext(
                user_id=1,
                username="test",
                email="test@example.test",
                verified=True,
                session_id=1,
                csrf_hash="",
                request_id="test",
            )
            yield value

    app.dependency_overrides[get_session] = session
    client = TestClient(app, raise_server_exceptions=True)
    try:
        status = client.get("/api/v1/agent/status")
        assert status.status_code == 200
        assert status.json() == {"enabled": False, "schema_ready": False}
        response = client.post(
            "/api/v1/assistant/global/conversations",
            headers={"Idempotency-Key": "create-current"},
            json={
                "project_id": "2",
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "agent_disabled"
    finally:
        client.close()


def test_invalid_agent_payload_is_rejected_without_database_access():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    app.state.session_factory = Session
    client = TestClient(app)
    response = client.post(
        "/api/v1/assistant/global/conversations",
        json={"project_id": "2", "owner_user_id": "3"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert (
        client.patch(
            "/api/v1/assistant/global/conversations/1", json={"row_version": 1}
        ).status_code
        == 422
    )
