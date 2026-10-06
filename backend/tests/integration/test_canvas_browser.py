"""真实浏览器、认证 HTTP 和隔离 MySQL；不拦截 API，不调用模型供应商。"""

import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from short_drama.db.readiness import assert_identity_ready
from short_drama.main import create_app
from tests.integration.test_identity_collaboration import PASSWORD, account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Set RUN_CANVAS_BROWSER_INTEGRATION=1 for real canvas browser/API/MySQL tests",
    ),
]


@contextmanager
def isolated_api(factory, settings, storage=None):
    # The normal lifespan opens Settings.database_url. Use only this test's isolated
    # factory, and still check its actual authentication schema before serving HTTP.
    engine = factory.kw["bind"]
    assert_identity_ready(engine, settings)
    app = create_app(settings)
    app.state.session_factory = factory
    app.state.storage = storage
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                lifespan="off",
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

        thread = threading.Thread(target=serve, name="canvas-test-api", daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                threading.Event().wait(0.05)
            assert server.started, f"Isolated canvas API did not start: {errors!r}"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(5)
            assert not thread.is_alive(), "Isolated canvas API thread did not stop"
            assert not errors, errors


def stop_browser(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_canvas_browser_saves_to_python_and_preserves_cross_window_conflicts(identity_app):
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration test"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir(), (
        "Run npm run install:all in frontend"
    )
    client, _ = account(identity_app, "canvas_browser")
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = identity_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4186"})
    with isolated_api(identity_app[1], settings) as api_url:
        config = {"apiUrl": api_url, "username": "canvas_browser", "password": PASSWORD}
        process = subprocess.Popen(
            [node, "scripts/verify-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=150)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Canvas browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Canvas browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["graph_saved"] and result["appearance_restored"]
        assert result["other_window_conflict_preserved"]
        # The report lists real 404s for the still-unmigrated runtime modules. It is
        # intentionally not interpreted as their functional acceptance.
        assert not result["page_errors"]
