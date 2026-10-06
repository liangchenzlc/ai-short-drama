"""真实 MySQL 加本机 HTTP 验证设备授权持久性、租约及本人隔离。"""

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_canvas_workspace import actor

from short_drama.ai.canvas_beefapi_client import CanvasBeefAPIClient
from short_drama.ai.gateway import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.domain.canvas_beefapi_connection import CanvasBeefAPIConnection
from short_drama.service.base import utcnow
from short_drama.service.canvas_beefapi_service import (
    CanvasBeefAPIService,
    tick_beefapi_connections,
)
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService
from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
KEY = base64.b64encode(b"b" * 32).decode()
CIPHER = KeyCipher(KEY)


@pytest.fixture
def enterprise():
    calls, state = [], {"poll": "success", "complete": 200, "catalog": 200, "verify": 200}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, dict(self.headers), json.loads(raw or b"{}")))
            status, result = 200, {}
            if self.path.endswith("/device/code"):
                result = {
                    "device_code": "private-device-grant",
                    "user_code": "ABCD-1234",
                    "verification_uri": state["origin"] + "/desktop-auth",
                    "interval": 5,
                    "expires_in": 900,
                }
            elif self.path.endswith("/device/token"):
                if state["poll"] != "success":
                    status, result = 400, {"error": state["poll"]}
                else:
                    result = {
                        "api_key": "private-managed-key",
                        "base_url": state["origin"] + "/v1",
                        "market": "enterprise",
                        "group": "enterprise",
                        "token_id": 9007199254740997,
                        "account": {"id": 9007199254740995, "display_name": "企业账号"},
                    }
            elif self.path.endswith("/device/complete"):
                if callback := state.get("on_complete"):
                    callback()
                status = state["complete"]
                result = (
                    {"success": True}
                    if status == 200
                    else {"error": state.get("ack_error", "temporary")}
                )
            elif self.path == "/v1/models":
                status = state["catalog"]
                result = {
                    "data": [
                        {
                            "id": "gpt-6-astra",
                            "model_type": "text",
                            "supported_endpoint_types": ["openai"],
                        },
                        {"id": "gpt-image-1", "model_type": "image"},
                    ]
                }
            elif self.path.endswith("/chat/completions"):
                status = state.get("generation_status", 400)
                result = {"error": {"message": "insufficient private-managed-key"}}
            elif self.command == "GET" and self.path == "/v1/beeftv/connection":
                status = state["verify"]
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = handle_request

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state["origin"], calls, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def service(session, enterprise):
    session.info["actor"] = actor()
    settings = SimpleNamespace(
        canvas_beefapi_test_origin=enterprise[0], encryption_key=SecretStr(KEY)
    )
    return CanvasBeefAPIService(
        session,
        settings,
        client=CanvasBeefAPIClient(test_origin=enterprise[0]),
        cipher=CIPHER,
        sleep=lambda _: None,
    )


def connect(target):
    assert target.start()["state"] == "pending"
    assert target.recover_one(force=True)
    return target.status(recover=False)


def test_initial_status_is_real_persisted_state_and_never_contacts_login(db_session, enterprise):
    target = service(db_session, enterprise)
    assert target.status()["state"] == "disconnected"
    assert enterprise[1] == []
    target.cancel()
    assert target.status()["state"] == "cancelled"
    with db_session.begin():
        assert db_session.scalar(select(CanvasBeefAPIConnection)).state_json["state"] == "cancelled"


def test_token_is_durable_before_complete_and_only_catalog_success_connects(db_session, enterprise):
    target = service(db_session, enterprise)
    engine = db_session.get_bind()

    def already_saved():
        with Session(engine) as separate, separate.begin():
            row = separate.scalar(select(CanvasBeefAPIConnection))
            assert row.state_json["state"] == "pending" and not row.state_json["acked"]
            assert json.loads(CIPHER.decrypt(row.secrets_cipher))["apiKey"] == "private-managed-key"

    enterprise[2]["on_complete"] = already_saved
    summary = connect(target)
    assert summary["state"] == "connected" and summary["hasCredential"]
    assert summary["account"]["id"] == "9007199254740995"
    assert "private" not in json.dumps(summary)
    with db_session.begin():
        row = db_session.scalar(select(CanvasBeefAPIConnection))
        assert row.state_json["acked"] and row.state_json["catalogOk"]
        assert "private" not in json.dumps(row.state_json)
        assert "private" not in row.secrets_cipher
        assert "deviceCode" not in json.loads(CIPHER.decrypt(row.secrets_cipher))
        assert target.credentials_locked(enterprise[0])["apiKey"] == "private-managed-key"
        with pytest.raises(WorkflowError, match="其他地址"):
            target.credentials_locked("https://attacker.example")
    models = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    assert models["channels"] == []
    managed = next(item for item in models["models"] if item["credential_source"] == "beefapi")
    assert managed["has_api_key"] and "apiKey" not in managed
    assert managed["id"].isdecimal() and managed["selection_aliases"] == ["beefapi::gpt-6-astra"]


@pytest.mark.parametrize(
    "code,expected", [("expired_token", "expired"), ("access_denied", "rejected")]
)
def test_terminal_device_states_remain_distinct(db_session, enterprise, code, expected):
    target = service(db_session, enterprise)
    enterprise[2]["poll"] = code
    target.start()
    target.recover_one(force=True)
    assert target.status(recover=False)["state"] == expected
    assert not any(path.endswith("/complete") for _, path, _, _ in enterprise[1])


def test_pending_start_and_slow_down_never_mint_duplicate_device_grant(db_session, enterprise):
    target = service(db_session, enterprise)
    enterprise[2]["poll"] = "slow_down"
    first = target.start()
    second = target.start()
    assert first["userCode"] == second["userCode"]
    target.recover_one(force=True)
    with db_session.begin():
        row = db_session.scalar(select(CanvasBeefAPIConnection))
        assert row.state_json["device"]["intervalSeconds"] == 10
        assert row.next_poll_at > utcnow() + timedelta(seconds=8)
    assert sum(path.endswith("/device/code") for _, path, _, _ in enterprise[1]) == 1


def test_source_start_limit_counts_pending_replays_without_interrupting_poll(
    db_session, enterprise
):
    target = service(db_session, enterprise)
    for _ in range(10):
        assert target.start()["state"] == "pending"
    with pytest.raises(WorkflowError) as caught:
        target.start()
    assert caught.value.status_code == 429
    with db_session.begin():
        row = db_session.scalar(select(CanvasBeefAPIConnection))
        assert row.next_poll_at is not None
    assert sum(path.endswith("/device/code") for _, path, _, _ in enterprise[1]) == 1


def test_lost_ack_response_retries_saved_grant_after_new_service(db_session, enterprise):
    target = service(db_session, enterprise)
    enterprise[2]["complete"] = 500
    assert connect(target)["state"] == "pending"
    assert sum(path.endswith("/complete") for _, path, _, _ in enterprise[1]) == 5
    with db_session.begin():
        row = db_session.scalar(select(CanvasBeefAPIConnection))
        assert not row.state_json["acked"]
        assert json.loads(CIPHER.decrypt(row.secrets_cipher))["apiKey"] == "private-managed-key"
    enterprise[2]["complete"] = 200
    restarted = service(db_session, enterprise)
    restarted.recover_one(force=True)
    assert restarted.status(recover=False)["state"] == "connected"
    assert sum(path.endswith("/device/code") for _, path, _, _ in enterprise[1]) == 1
    assert sum(path.endswith("/device/token") for _, path, _, _ in enterprise[1]) == 1


def test_permanent_ack_expiry_removes_unconfirmed_secret(db_session, enterprise):
    target = service(db_session, enterprise)
    enterprise[2].update(complete=400, ack_error="expired_token")
    summary = connect(target)
    assert summary["state"] == "expired" and not summary["hasCredential"]
    with db_session.begin():
        row = db_session.scalar(select(CanvasBeefAPIConnection))
        assert row.secrets_cipher is None
    assert not any(path == "/v1/models" for _, path, _, _ in enterprise[1])


def test_catalog_failure_retries_without_new_login_or_duplicate_ack(db_session, enterprise):
    target = service(db_session, enterprise)
    enterprise[2]["catalog"] = 500
    failed = connect(target)
    assert failed["state"] == "catalog_failed" and failed["hasCredential"]
    enterprise[2]["catalog"] = 200
    assert target.start()["state"] == "connected"
    assert sum(path.endswith("/device/code") for _, path, _, _ in enterprise[1]) == 1
    assert sum(path.endswith("/complete") for _, path, _, _ in enterprise[1]) == 1


def test_scheduler_recovers_after_page_leaves_and_connection_is_private(db_session, enterprise):
    target = service(db_session, enterprise)
    target.start()
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False)
    assert tick_beefapi_connections(factory, target.settings) == 1
    assert target.status(recover=False)["state"] == "connected"
    with Session(db_session.get_bind()) as other:
        other.info["actor"] = actor(999)
        with other.begin():
            assert list(other.scalars(select(CanvasBeefAPIConnection))) == []
        assert (
            CanvasBeefAPIService(other, target.settings, cipher=CIPHER).status()["state"]
            == "disconnected"
        )


def test_revoked_remote_blocks_execution_and_disconnect_clears_only_managed(db_session, enterprise):
    target = service(db_session, enterprise)
    connect(target)
    enterprise[2]["verify"] = 401
    target.recover_one(force=True)
    assert target.status(recover=False)["state"] == "revoked"
    with db_session.begin(), pytest.raises(WorkflowError, match="失效"):
        target.credentials_locked()
    assert target.disconnect()["state"] == "disconnected"
    assert any(
        method == "DELETE" and path == "/v1/beeftv/connection"
        for method, path, _, _ in enterprise[1]
    )


def test_concurrent_recovery_claims_poll_once(db_session, enterprise):
    target = service(db_session, enterprise)
    target.start()
    enterprise[2]["poll"] = "authorization_pending"
    engine = db_session.get_bind()

    def recover():
        with Session(engine) as session:
            return service(session, enterprise).recover_one()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: recover(), range(2))) == 1
    assert sum(path.endswith("/device/token") for _, path, _, _ in enterprise[1]) == 1


def test_status_read_does_not_poll_remote_and_revoked_start_needs_source_disconnect(
    db_session, enterprise
):
    target = service(db_session, enterprise)
    target.start()
    before = len(enterprise[1])
    assert target.status()["state"] == "pending"
    assert len(enterprise[1]) == before
    target.recover_one(force=True)
    enterprise[2]["verify"] = 403
    target.recover_one(force=True)
    assert target.start()["state"] == "revoked"
    assert sum(path.endswith("/device/code") for _, path, _, _ in enterprise[1]) == 1
    target.disconnect()
    assert target.start()["state"] == "pending"
    assert sum(path.endswith("/device/code") for _, path, _, _ in enterprise[1]) == 2


@pytest.mark.parametrize("status", [200, 400, 429])
def test_generation_failure_keeps_source_connection_and_unknown_balance(
    db_session, enterprise, status
):
    target = service(db_session, enterprise)
    connect(target)
    before = len(enterprise[1])
    gateway = GenerationGateway(SimpleNamespace(generation_allowed_hosts=["127.0.0.1"]))
    enterprise[2]["generation_status"] = status

    def fail():
        return gateway.submit(
            {"base_url": enterprise[0], "model_key": "gpt-6-astra", "service_type": "text"},
            {"input": {"messages": [{"role": "user", "content": "hello"}]}},
            "private-managed-key",
            "openai_chat.v1",
        )

    if status == 200:
        assert fail().status == "failed"
    else:
        with pytest.raises(GenerationError) as caught:
            fail()
        assert caught.value.code == (
            "provider_rate_limit" if status == 429 else "provider_rejected"
        )
    assert target.status()["balance"] == "unknown" and target.status()["state"] == "connected"
    assert len(enterprise[1]) == before + 1


def test_real_http_routes_enforce_actor_csrf_and_keep_device_poll_out_of_get(
    identity_app, enterprise
):
    app, factory, settings = identity_app
    settings.canvas_beefapi_test_origin = enterprise[0]
    path = "/api/v1/canvas-runtime/beefapi/connection"
    anonymous = TestClient(app)
    assert anonymous.get(path).status_code == 401
    client, user = account(identity_app, "canvas_beefapi_http")
    client.headers["X-Canvas-Actor"] = user["id"]
    assert client.get(path).json()["state"] == "disconnected"
    assert (
        client.post(path + "/start", headers={"X-CSRF-Token": "wrong"}, json={}).status_code == 403
    )
    assert enterprise[1] == []
    response = client.post(path + "/start", json={"origin": "https://attacker.example"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending"
    before = len(enterprise[1])
    assert client.get(path).json()["state"] == "pending"
    assert len(enterprise[1]) == before
    assert client.get(path, headers={"X-Canvas-Actor": "1"}).status_code == 409
    assert tick_beefapi_connections(factory, settings) == 1
    connected = client.get(path)
    assert connected.status_code == 200 and connected.json()["state"] == "connected"
    assert "private" not in connected.text and "deviceCode" not in connected.text
    wallet = client.post(path + "/open-wallet", json={})
    assert wallet.json() == {
        "enterpriseOrigin": enterprise[0],
        "walletUrl": enterprise[0] + "/console/topup",
    }
    other, other_user = account(identity_app, "canvas_beefapi_other_http")
    other.headers["X-Canvas-Actor"] = other_user["id"]
    assert other.get(path).json()["state"] == "disconnected"
    assert client.post(path + "/cancel", json={}).json()["state"] == "connected"
    assert client.post(path + "/disconnect", json={}).json()["state"] == "disconnected"
