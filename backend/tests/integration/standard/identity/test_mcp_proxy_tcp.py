"""Real Uvicorn TCP peer reaches MCP authentication without upstream rewriting."""

import socket
import threading
import time

import httpx2
import pytest
import uvicorn

from short_drama.db.session import session_factory
from short_drama.service.projects.management.project import ProjectService
from tests.integration.standard.identity.test_mcp_tokens import create_token, mcp_app

pytestmark = pytest.mark.integration


def test_uvicorn_preserves_peer_until_mcp_trusts_forwarded_chain(db_session):
    project = ProjectService(db_session).create_project({"name": "TCP 来源边界"})
    token = create_token(
        db_session,
        project.id,
        tools=["workbench__read_project"],
        allowed_ips=["192.0.2.8"],
    )
    app = mcp_app(session_factory(db_session.get_bind()))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=listener.getsockname()[1],
                proxy_headers=False,
                forwarded_allow_ips="*",
                access_log=False,
                log_config=None,
                log_level="error",
            )
        )
        errors = []

        def serve():
            try:
                server.run(sockets=[listener])
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=serve, name="mcp-proxy-tcp-test", daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                threading.Event().wait(0.05)
            assert server.started, f"Isolated MCP TCP server did not start: {errors!r}"
            with httpx2.Client(
                base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                trust_env=False,
                timeout=10,
                headers={
                    "Authorization": "Bearer " + token.secret,
                    "Accept": "application/json, text/event-stream",
                    "X-Forwarded-For": "192.0.2.8, 10.2.3.4",
                },
            ) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "proxy-tcp-test", "version": "1"},
                    },
                }
                untrusted = client.post("/mcp/", json=payload)
                assert untrusted.status_code == 403
                assert untrusted.json()["code"] == "mcp_ip_forbidden"
                app.state.settings.mcp_trusted_proxy_cidrs = ["127.0.0.1/32"]
                assert client.post("/mcp/", json=payload).status_code == 403
                app.state.settings.mcp_trusted_proxy_cidrs = ["127.0.0.1/32", "10.0.0.0/8"]
                trusted = client.post("/mcp/", json=payload)
                assert trusted.status_code == 200
                assert trusted.json()["result"]["serverInfo"]["name"] == "Miniart"
        finally:
            server.should_exit = True
            thread.join(5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(5)
            assert not thread.is_alive(), "Isolated MCP TCP server did not stop"
            assert not errors, errors
