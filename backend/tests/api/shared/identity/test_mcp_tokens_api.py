"""Cookie/CSRF token management with real account-owned MySQL records."""

from datetime import timedelta
from importlib import import_module, util

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.identity import token_hash
from short_drama.core.time import utcnow
from short_drama.db.session import session_factory
from short_drama.domain.identity.accounts.user import UserSession
from short_drama.main import create_app
from short_drama.service.projects.management.project import ProjectService
from tests.integration.conftest import db_session as db_session
from tests.integration.conftest import mysql_engine as mysql_engine


@pytest.fixture
def token_http(db_session):
    project = ProjectService(db_session).create_project({"name": "令牌作品"})
    factory = session_factory(db_session.get_bind())
    with factory.begin() as session:
        session.add(
            UserSession(
                id=9,
                user_id=1,
                token_hash=token_hash("browser-cookie"),
                csrf_hash=token_hash("browser-csrf"),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    app = create_app(
        Settings(
            _env_file=None,
            auth_enabled=True,
            agent_enabled=False,
            public_origin="https://testserver",
        )
    )
    app.state.session_factory = factory
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set("sd_session", "browser-cookie")
    client.headers.update({"Origin": "https://testserver", "X-CSRF-Token": "browser-csrf"})
    yield client, project, factory
    client.close()


def test_create_list_options_delete_exposes_secret_only_once(token_http):
    client, project, factory = token_http
    path = "/api/v1/account/mcp-tokens"
    options = client.get(path + "/options")
    assert options.status_code == 200, options.text
    assert options.json()["projects"] == [{"id": str(project.id), "name": "令牌作品"}]
    names = {tool["name"] for tool in options.json()["tools"]}
    assert "workbench__apply_standard_patch" in names
    assert not {"task", "execute", "runtime__load_skill"} & names
    response = client.post(
        path,
        json={
            "name": "  外部工作台  ",
            "project_ids": [str(project.id)],
            "tool_names": ["workbench__apply_standard_patch"],
            "allowed_ips": ["192.0.2.4/24", "2001:0db8::1"],
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()
    assert token["name"] == "外部工作台" and token["secret"].startswith("sdmcp_")
    assert token["allowed_ips"] == ["192.0.2.0/24", "2001:db8::1"]
    rows = client.get(path).json()
    assert len(rows) == 1 and rows[0]["id"] == token["id"]
    assert "secret" not in rows[0] and "token_hash" not in rows[0]
    assert token["secret"] not in str(rows) and "token_hash" not in token
    assert util.find_spec("short_drama.domain.identity.accounts.mcp_token") is not None
    model = import_module("short_drama.domain.identity.accounts.mcp_token").McpAccessToken
    with factory() as session:
        row = session.scalar(select(model).where(model.id == int(token["id"])))
        assert row.token_hash == token_hash(token["secret"])
    assert client.delete(path + "/" + token["id"]).status_code == 204
    assert client.get(path).json() == []
    with factory() as session:
        assert session.get(model, int(token["id"])).deleted_at is not None


@pytest.mark.parametrize(
    "change",
    [
        {"name": "  "},
        {"name": "名" * 81},
        {"project_ids": []},
        {"tool_names": []},
        {"allowed_ips": ["bad-ip"]},
        {"expires_at": "2000-01-01T00:00:00Z"},
        {"expires_at": "2099-01-01T00:00:00"},
        {"token_hash": "forged"},
    ],
)
def test_token_input_rejects_invalid_scope_name_time_and_ip(token_http, change):
    client, project, _ = token_http
    payload = {
        "name": "valid",
        "project_ids": [str(project.id)],
        "tool_names": ["workbench__read_current_script"],
    }
    response = client.post("/api/v1/account/mcp-tokens", json=payload | change)
    assert response.status_code == 422, response.text


def test_token_management_still_requires_browser_cookie_and_csrf(token_http):
    client, project, _ = token_http
    path = "/api/v1/account/mcp-tokens"
    assert client.post(path, headers={"X-CSRF-Token": "wrong"}, json={}).status_code == 403
    client.cookies.clear()
    assert client.get(path, headers={"Authorization": "Bearer sdmcp_unknown"}).status_code == 401
