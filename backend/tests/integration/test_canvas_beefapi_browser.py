"""原企业连接按钮通过真实浏览器、本机企业 HTTP、Python scheduler 和 MySQL。"""

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.domain import AsyncTask, CanvasTaskBinding
from short_drama.domain.canvas_beefapi_connection import CanvasBeefAPIConnection
from short_drama.domain.canvas_model_catalog import CanvasModelCatalog
from short_drama.service.canvas_beefapi_service import tick_beefapi_connections
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_identity_collaboration import PASSWORD, account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable source enterprise authorization browser/Python/MySQL verification",
    ),
]


@contextmanager
def browser_enterprise():
    state = {"grants": {}, "calls": [], "errors": []}
    lock = Lock()

    class Enterprise(BaseHTTPRequestHandler):
        def handle_request(self):
            path = urlsplit(self.path).path
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                self.respond(path, raw)
            except BaseException as error:
                state["errors"].append(repr(error))
                self.send_response(500)
                self.end_headers()

        def respond(self, path, raw):
            with lock:
                state["calls"].append((self.command, path))
                status, body, content_type = 200, {}, "application/json"
                if path == "/api/oauth/device/code":
                    incoming = json.loads(raw)
                    assert incoming["client_id"] == "beeftv-enterprise-v1"
                    index = len(state["grants"]) + 1
                    code, public = f"synthetic-device-secret-{index}", f"TEST-{index:04d}"
                    state["grants"][code] = {
                        "code": public,
                        "approved": False,
                        "cancelled": False,
                    }
                    body = {
                        "device_code": code,
                        "user_code": public,
                        "verification_uri": state["origin"] + "/desktop-auth",
                        "verification_uri_complete": state["origin"]
                        + "/desktop-auth?user_code="
                        + public,
                        "interval": 1,
                        "expires_in": 900,
                    }
                elif path == "/api/oauth/device/token":
                    grant = state["grants"][json.loads(raw)["device_code"]]
                    if grant["cancelled"] or not grant["approved"]:
                        status, body = 400, {"error": "authorization_pending"}
                    else:
                        body = {
                            "api_key": "synthetic-managed-browser-api-key",
                            "base_url": state["origin"] + "/v1",
                            "market": "enterprise",
                            "group": "enterprise",
                            "token_id": "9007199254740997",
                            "key_name": "本机授权测试",
                            "account": {
                                "id": "9007199254740995",
                                "display_name": "受控企业账号",
                            },
                        }
                elif path == "/api/oauth/device/complete":
                    assert state["grants"][json.loads(raw)["device_code"]]["approved"]
                    body = {"success": True}
                elif path == "/api/oauth/device/cancel":
                    state["grants"][json.loads(raw)["device_code"]]["cancelled"] = True
                elif path == "/v1/models":
                    assert self.headers["Authorization"] == (
                        "Bearer synthetic-managed-browser-api-key"
                    )
                    body = {
                        "data": [
                            {
                                "id": "gpt-6-astra",
                                "model_type": "text",
                                "supported_endpoint_types": ["openai"],
                            },
                            {"id": "gpt-image-1", "model_type": "image"},
                        ]
                    }
                elif path == "/v1/chat/completions":
                    assert self.headers["Authorization"] == (
                        "Bearer synthetic-managed-browser-api-key"
                    )
                    assert self.headers["User-Agent"] == "synthetic-managed-browser-header"
                    payload = json.loads(raw)
                    assert payload["model"] == "gpt-6-astra"
                    assert payload["stream"] is False
                    body = {
                        "choices": [
                            {
                                "message": {"content": "OK from managed enterprise HTTP"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                elif path == "/v1/beeftv/connection":
                    assert self.headers["Authorization"] == (
                        "Bearer synthetic-managed-browser-api-key"
                    )
                elif path == "/desktop-auth" and self.command == "GET":
                    code = parse_qs(urlsplit(self.path).query)["user_code"][0]
                    assert code in [item["code"] for item in state["grants"].values()]
                    content_type = "text/html; charset=utf-8"
                    body = (
                        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        f"<h1>企业授权 {code}</h1>"
                        '<form method="post" action="/desktop-auth/approve">'
                        f'<input type="hidden" name="user_code" value="{code}">'
                        '<button type="submit">确认授权</button></form></html>'
                    )
                elif path == "/desktop-auth/approve":
                    code = parse_qs(raw.decode())["user_code"][0]
                    grant = next(item for item in state["grants"].values() if item["code"] == code)
                    assert not grant["cancelled"]
                    grant["approved"] = True
                    content_type, body = "text/html; charset=utf-8", "<h1>企业授权已确认</h1>"
                elif path == "/console/topup":
                    assert not urlsplit(self.path).query
                    content_type, body = "text/html; charset=utf-8", "<h1>企业钱包</h1>"
                elif path == "/favicon.ico":
                    status, body = 204, ""
                else:
                    raise AssertionError(f"Unimplemented enterprise route: {self.command} {path}")
                encoded = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        do_GET = do_POST = do_DELETE = handle_request

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Enterprise)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@contextmanager
def enterprise_scheduler(factory, settings, model_test):
    stopped, errors = Event(), []
    executor = GenerationExecutionService(factory, settings, GenerationGateway(settings), None)

    def tick():
        while not stopped.wait(0.05):
            try:
                tick_beefapi_connections(factory, settings)
                if model_test:
                    with factory() as session:
                        tasks = list(
                            session.scalars(select(AsyncTask).where(AsyncTask.status == "queued"))
                        )
                        waiting = [(str(task.id), task.message_version) for task in tasks]
                    for identifier, version in waiting:
                        executor.execute(identifier, version)
            except BaseException as error:
                errors.append(repr(error))
                stopped.set()

    worker = Thread(target=tick, daemon=True)
    worker.start()
    try:
        yield errors
    finally:
        stopped.set()
        worker.join(10)
        assert not worker.is_alive()


@pytest.mark.parametrize("run_model_test", [False, True])
def test_source_enterprise_popup_authorization_wallet_cancel_disconnect_and_isolation(
    identity_app, run_model_test
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    author, actor = account(identity_app, "beefapi_browser_author")
    other, reader = account(identity_app, "beefapi_browser_other")
    assert author.post("/api/v1/auth/logout").status_code == 200
    assert other.post("/api/v1/auth/logout").status_code == 200
    with browser_enterprise() as enterprise:
        settings = identity_app[2].model_copy(
            update={
                "public_origin": "http://127.0.0.1:4195",
                "canvas_beefapi_test_origin": enterprise["origin"],
                "generation_allowed_hosts": ["127.0.0.1"],
            }
        )
        with (
            enterprise_scheduler(identity_app[1], settings, run_model_test) as errors,
            isolated_api(identity_app[1], settings) as api_url,
        ):
            process = subprocess.Popen(
                [node, "scripts/verify-beefapi-python.mjs"],
                cwd=frontend,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            config = {
                "apiUrl": api_url,
                "enterpriseOrigin": enterprise["origin"],
                "author": "beefapi_browser_author",
                "other": "beefapi_browser_other",
                "password": PASSWORD,
                "actorId": actor["id"],
                "otherId": reader["id"],
                "runModelTest": run_model_test,
            }
            try:
                output, stderr = process.communicate(json.dumps(config), timeout=180)
            except subprocess.TimeoutExpired:
                stop_browser(process)
                output, stderr = process.communicate(timeout=10)
                pytest.fail(f"Enterprise browser timed out: {stderr}\n{output}")
            finally:
                if process.poll() is None:
                    stop_browser(process)
                    process.communicate(timeout=10)
            assert process.returncode == 0, f"Enterprise browser failed: {stderr}\n{output}"
            result = json.loads(output)
            assert all(
                result[key]
                for key in (
                    "builtin_available",
                    "pending_authorization_opened",
                    "popup_opener_isolated",
                    "source_cancel_completed",
                    "real_enterprise_approval",
                    "scheduler_connected",
                    "managed_catalog_saved",
                    "fresh_browser_connected",
                    "wallet_actually_opened",
                    "other_account_isolated",
                    "secrets_not_exposed",
                    "source_disconnect_completed",
                )
            )
            assert not result["page_errors"] and not result["failed_routes"]
            assert result["source_managed_header_saved"] is run_model_test
            assert result["source_managed_model_test_completed"] is run_model_test
            assert result["source_tasks_showed_managed_result"] is run_model_test
            assert result["managed_model_test_private"] is run_model_test
            assert result["model_test_submissions"] == int(run_model_test)
            assert not errors
        assert not enterprise["errors"]
        calls = enterprise["calls"]
        assert calls.count(("POST", "/api/oauth/device/code")) == 2
        assert calls.count(("POST", "/api/oauth/device/cancel")) == 1
        assert calls.count(("POST", "/api/oauth/device/complete")) == 1
        assert calls.count(("POST", "/desktop-auth/approve")) == 1
        assert calls.count(("GET", "/v1/models")) == 1
        assert calls.count(("GET", "/console/topup")) == 1
        assert calls.count(("DELETE", "/v1/beeftv/connection")) == 1
        assert calls.count(("POST", "/v1/chat/completions")) == int(run_model_test)
    with identity_app[1]() as session:
        connection = session.scalar(select(CanvasBeefAPIConnection))
        assert connection.user_id == int(actor["id"])
        assert connection.state_json["state"] == "disconnected"
        assert connection.secrets_cipher is None
        catalog = session.scalar(select(CanvasModelCatalog))
        managed = next(item for item in catalog.channels_json if item["id"] == "beefapi")
        assert managed["models"] == [] and not managed["hasApiKey"]
        tasks = list(session.scalars(select(AsyncTask)))
        assert len(tasks) == int(run_model_test)
        assert all(
            task.project_id is None
            and task.scope_user_id == int(actor["id"])
            and task.status == "succeeded"
            for task in tasks
        )
        assert session.scalar(select(CanvasTaskBinding)) is None
