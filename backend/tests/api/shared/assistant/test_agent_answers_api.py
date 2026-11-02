"""Real Cookie/CSRF/answer HTTP boundary backed by the isolated MySQL fixture."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.identity import token_hash
from short_drama.core.time import utcnow
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.identity.accounts.user import UserSession
from short_drama.main import create_app
from tests.integration.conftest import db_session as db_session
from tests.integration.conftest import mysql_engine as mysql_engine
from tests.integration.standard.assistant.test_agent_questions import execute_next, wait_question
from tests.integration.standard.assistant.test_global_assistant_tasks import harness_workspace
from tests.support.assistant_runtime import RuntimeTransport

task_workspace = harness_workspace


def test_cookie_csrf_strict_answer_and_terminal_replay_over_http(task_workspace):
    _, accepted, operation_id = wait_question(task_workspace)
    factory, _, _ = task_workspace
    with factory.begin() as session:
        session.add(
            UserSession(
                id=7,
                user_id=1,
                token_hash=token_hash("test-cookie"),
                csrf_hash=token_hash("test-csrf"),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    app = create_app(
        Settings(
            _env_file=None,
            auth_enabled=True,
            agent_enabled=True,
            public_origin="https://testserver",
        )
    )
    app.state.session_factory = factory
    client = TestClient(app, base_url="https://testserver")
    path = f"/api/v1/assistant/operations/{operation_id}/answer"
    payload = {"question_version": "1", "outcome": "answered", "values": {"ending": "开放结尾"}}
    headers = {
        "Origin": "https://testserver",
        "X-CSRF-Token": "test-csrf",
        "Idempotency-Key": "answer-http",
    }
    assert client.post(path, json=payload, headers=headers).status_code == 401
    client.cookies.set("sd_session", "test-cookie")
    assert (
        client.post(path, json=payload, headers={"Origin": "https://testserver"}).status_code == 403
    )
    assert client.post(path, json=payload, headers={"X-CSRF-Token": "test-csrf"}).status_code == 403
    assert (
        client.post(
            path, json=payload, headers={k: v for k, v in headers.items() if k != "Idempotency-Key"}
        ).status_code
        == 422
    )
    assert (
        client.post(path, json={**payload, "interrupt_id": "forged"}, headers=headers).status_code
        == 422
    )
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["run_id"] == str(accepted.run.id) and receipt["operation_id"] == str(
        operation_id
    )
    execute_next(task_workspace, accepted, RuntimeTransport([{"text": "已读取定向答案"}]))
    assert client.post(path, json=payload, headers=headers).json() == receipt
    assert (
        client.post(
            path, json={**payload, "values": {"ending": "不同答案"}}, headers=headers
        ).status_code
        == 409
    )
    with factory() as session:
        assert (
            len(
                session.scalars(
                    select(AgentMessage).where(AgentMessage.kind == "tool_answer")
                ).all()
            )
            == 1
        )
    client.close()
