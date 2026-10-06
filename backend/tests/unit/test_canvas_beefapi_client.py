"""固定企业源的设备授权合同；真实 HTTP 仅发往本机测试服务器。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from short_drama.ai.canvas_beefapi_client import (
    CanvasBeefAPIClient,
    CanvasBeefAPIError,
    canonical_origin,
    trusted_browser_url,
)


@pytest.fixture
def enterprise():
    calls, replies = [], {}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, dict(self.headers), json.loads(raw or b"{}")))
            status, body = replies.get(self.path, (200, {}))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json")
            if status == 302:
                self.send_header("Location", "/capture-secret")
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = handle_request

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        yield CanvasBeefAPIClient(test_origin=origin), calls, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def token(origin, **changes):
    return {
        "api_key": "managed-private-key",
        "base_url": origin + "/v1",
        "market": "enterprise",
        "group": "enterprise",
        "token_id": 9007199254740997,
        "account": {"id": 9007199254740995, "display_name": "企业工作室"},
        "key_name": "BeefTV",
        **changes,
    }


def test_device_code_sends_exact_source_contract_and_validates_browser_target(enterprise):
    client, calls, replies = enterprise
    replies["/api/oauth/device/code"] = (
        200,
        {
            "device_code": "private-device-code",
            "user_code": "ABCD-1234",
            "verification_uri_complete": client.origin + "/desktop-auth?user_code=ABCD-1234",
            "expires_in": 0,
            "interval": 0,
        },
    )
    result = client.device_code()
    assert result["expires_in"] == 900 and result["interval"] == 5
    assert result["verification_uri"] == client.origin + "/desktop-auth"
    assert calls[0][0:2] == ("POST", "/api/oauth/device/code")
    assert calls[0][3]["client_id"] == "beeftv-enterprise-v1"
    assert calls[0][3]["scope"] == "inference"
    assert set(calls[0][3]) == {"client_id", "scope", "client_version", "hostname"}


def test_numeric_enterprise_identifiers_remain_lossless_strings(enterprise):
    client, calls, replies = enterprise
    replies["/api/oauth/device/token"] = (200, token(client.origin))
    result, code = client.poll_token("private-device-code")
    assert code == ""
    assert result.account.id == "9007199254740995"
    assert result.token_id == "9007199254740997"
    assert result.api_key.get_secret_value() == "managed-private-key"
    assert "managed-private-key" not in repr(result)
    assert calls[0][3] == {
        "client_id": "beeftv-enterprise-v1",
        "device_code": "private-device-code",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"market": ""},
        {"group": "public"},
        {"base_url": "https://attacker.example/v1"},
        {"token_id": 0},
        {"token_id": "1e3"},
        {"token_id": 2**63},
        {"account": {"id": True}},
        {"account": {"id": -1}},
        {"key_name": "managed-private-key"},
    ],
)
def test_invalid_token_is_rejected_without_key_echo(enterprise, changes):
    client, _, replies = enterprise
    replies["/api/oauth/device/token"] = (200, token(client.origin, **changes))
    with pytest.raises(CanvasBeefAPIError) as caught:
        client.poll_token("private-device-code")
    assert caught.value.code == "token_invalid"
    assert "managed-private-key" not in str(caught.value)


@pytest.mark.parametrize(
    "body,code",
    [
        ({"error": "authorization_pending"}, "authorization_pending"),
        ({"error": {"code": "slow_down"}}, "slow_down"),
        ({"error": {"error": "access_denied"}}, "access_denied"),
        ({"error_code": "expired_token"}, "expired_token"),
        ({}, "invalid_request"),
    ],
)
def test_oauth_error_variants_are_preserved(enterprise, body, code):
    client, _, replies = enterprise
    replies["/api/oauth/device/token"] = (400, body)
    assert client.poll_token("private-device-code") == (None, code)


@pytest.mark.parametrize(
    "status,body,code",
    [
        (200, {"success": False}, "ack_transient"),
        (400, {"error": "expired_token"}, "ack_expired"),
        (400, {"error": "access_denied"}, "ack_rejected"),
        (400, {}, "ack_expired"),
        (429, {}, "ack_transient"),
        (500, {}, "ack_transient"),
    ],
)
def test_complete_requires_explicit_success_and_classifies_retry(enterprise, status, body, code):
    client, _, replies = enterprise
    replies["/api/oauth/device/complete"] = (status, body)
    with pytest.raises(CanvasBeefAPIError) as caught:
        client.complete("private-device-code")
    assert caught.value.code == code


def test_catalog_connection_revoke_and_cancel_use_bound_origin_and_key(enterprise):
    client, calls, replies = enterprise
    replies["/v1/models"] = (200, {"data": [{"id": "gpt-6-astra", "model_type": "text"}]})
    assert client.models("managed-private-key")[0]["id"] == "gpt-6-astra"
    assert client.connection("managed-private-key")[1] == 200
    client.revoke("managed-private-key")
    client.cancel("private-device-code")
    assert [call[:2] for call in calls] == [
        ("GET", "/v1/models"),
        ("GET", "/v1/beeftv/connection"),
        ("DELETE", "/v1/beeftv/connection"),
        ("POST", "/api/oauth/device/cancel"),
    ]
    for _, _, headers, _ in calls[:3]:
        assert headers["Authorization"] == "Bearer managed-private-key"
    assert "Authorization" not in calls[3][2]


@pytest.mark.parametrize("status", [401, 403])
def test_revoked_catalog_is_not_empty_success(enterprise, status):
    client, _, replies = enterprise
    replies["/v1/models"] = (status, {"error": "managed-private-key"})
    with pytest.raises(CanvasBeefAPIError, match="连接已失效") as caught:
        client.models("managed-private-key")
    assert caught.value.code == "revoked"


def test_http_redirect_never_leaks_key_or_device_code(enterprise):
    client, calls, replies = enterprise
    replies["/v1/models"] = (302, {})
    with pytest.raises(CanvasBeefAPIError):
        client.models("managed-private-key")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://enterprise.beefapi.com",
        "http://127.0.0.1/path",
        "http://127.0.0.1?x",
        "http://user@127.0.0.1",
        "http://127.0.0.1:0",
    ],
)
def test_production_origin_cannot_be_overridden_by_arbitrary_address(origin):
    with pytest.raises(CanvasBeefAPIError):
        canonical_origin(origin)


@pytest.mark.parametrize("path", ["/other", "/desktop-auth%2F..%2Fother", "/console/topup?x"])
def test_browser_paths_are_trusted_exactly(path):
    with pytest.raises(CanvasBeefAPIError):
        trusted_browser_url(
            "https://enterprise.beefapi.com", "https://enterprise.beefapi.com" + path
        )
