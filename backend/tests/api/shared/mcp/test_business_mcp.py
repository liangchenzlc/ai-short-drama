"""The complete mounted MCP prefix authenticates every HTTP protocol request."""

import pytest
from fastapi.testclient import TestClient

from short_drama.core.config import Settings
from short_drama.main import create_app


def test_token_settings_accept_capabilities_without_project_or_tool_ids():
    from pydantic import ValidationError

    from short_drama.schemas.identity.accounts.mcp_token import McpTokenCreate

    values = McpTokenCreate.model_validate({"name": "工具", "workbench_enabled": True})
    assert values.workbench_enabled and not values.media_generation_enabled
    assert values.expires_at is None and "expires_at" not in values.model_fields_set
    for field in ("project_ids", "tool_names"):
        with pytest.raises(ValidationError):
            McpTokenCreate.model_validate({"name": "工具", field: ["1"]})


def test_token_settings_reject_non_boolean_capabilities():
    from pydantic import ValidationError

    from short_drama.schemas.identity.accounts.mcp_token import McpTokenCreate

    with pytest.raises(ValidationError):
        McpTokenCreate.model_validate({"name": "工具", "workbench_enabled": "yes"})


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Authorization", "Basic invalid")],
        [("Authorization", "Bearer invalid")],
        [("Authorization", "Bearer sdmcp_one"), ("Authorization", "Bearer sdmcp_two")],
        [("Authorization", "Bearer sdmcp_one, Bearer sdmcp_two")],
    ],
)
def test_bearer_header_is_unique_and_has_no_cookie_fallback(headers):
    client = TestClient(
        create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    )
    response = client.post("/mcp/", headers=headers, json={})
    client.close()
    assert response.status_code == 401


@pytest.mark.parametrize(
    "peer, proxies, forwarded, expected",
    [
        ("198.51.100.7", [], "192.0.2.8", "198.51.100.7"),
        ("198.51.100.7", [], "invalid", "198.51.100.7"),
        ("127.0.0.1", ["127.0.0.0/8"], "192.0.2.8", "192.0.2.8"),
        ("127.0.0.1", ["127.0.0.0/8", "10.0.0.0/8"], "192.0.2.8, 10.2.3.4", "192.0.2.8"),
        ("127.0.0.1", ["127.0.0.0/8"], "192.0.2.8, 198.51.100.7", "198.51.100.7"),
        ("::1", ["::1/128"], "2001:0db8::8", "2001:db8::8"),
        ("127.0.0.1", ["127.0.0.0/8"], None, "127.0.0.1"),
    ],
)
def test_trusted_proxy_chain_is_resolved_from_connection_peer_once(
    peer, proxies, forwarded, expected
):
    from starlette.requests import Request

    from short_drama.api.mcp.auth import client_ip

    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    request = Request({"type": "http", "client": (peer, 1234), "headers": headers})
    assert client_ip(request, proxies) == expected


@pytest.mark.parametrize("forwarded", ["", "192.0.2.8,", "bad-ip", "192.0.2.8, bad-ip"])
def test_malformed_trusted_proxy_chain_is_an_entry_error(forwarded):
    from starlette.requests import Request

    from short_drama.api.mcp.auth import client_ip
    from short_drama.core.exceptions import BusinessError

    request = Request(
        {
            "type": "http",
            "client": ("127.0.0.1", 1234),
            "headers": [(b"x-forwarded-for", forwarded.encode())],
        }
    )
    with pytest.raises(BusinessError) as raised:
        client_ip(request, ["127.0.0.0/8"])
    assert raised.value.status_code == 400


def test_actual_launcher_disables_uvicorn_address_rewriting():
    import subprocess
    from pathlib import Path

    import uvicorn
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    script = Path(__file__).resolve().parents[4] / "scripts" / "start_generation.ps1"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Role",
            "api",
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    arguments = result.stdout.split("python ", 1)[1].split()
    assert "--no-proxy-headers" in arguments
    config = uvicorn.Config(
        create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False)),
        proxy_headers=False,
        forwarded_allow_ips="*",
    )
    config.load()
    assert not isinstance(config.loaded_app, ProxyHeadersMiddleware)


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/unknown"])
@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call", "ping"])
def test_missing_bearer_is_401_before_protocol_or_redirect(path, method):
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    client = TestClient(app, follow_redirects=False)
    response = client.post(
        path,
        json={"jsonrpc": "2.0", "id": 1, "method": method},
        headers={"Cookie": "sd_session=browser-cookie", "Mcp-Session-Id": "old-session"},
    )
    client.close()
    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["GET", "DELETE", "OPTIONS"])
def test_followup_http_methods_also_require_bearer(method):
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    client = TestClient(app, follow_redirects=False)
    response = client.request(method, "/mcp/", headers={"Mcp-Session-Id": "old-session"})
    client.close()
    assert response.status_code == 401, response.text
