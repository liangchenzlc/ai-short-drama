"""原模型服务向导通过真实本人 HTTP 和 MySQL 完成 CRUD；不调用供应商。"""

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationGateway
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import (
    AIModelConfig,
    AsyncTask,
    CanvasChannelModel,
    CanvasModelCatalog,
    CanvasTaskBinding,
)
from short_drama.service.canvas_model_test_service import CanvasModelTestService
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_identity_collaboration import PASSWORD, account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original model settings browser/Python/MySQL verification",
    ),
]


@contextmanager
def controlled_model_test_runtime(identity_app, settings, enabled, monkeypatch):
    evidence = {"calls": [], "worker_errors": [], "admissions": 0}
    if not enabled:
        yield evidence
        return

    class Supplier(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer synthetic-browser-api-key"
            assert self.headers["X-Provider-Key"] == "synthetic-browser-header-key"
            assert payload["stream"] is False
            evidence["calls"].append((self.path, payload))
            failed = len(evidence["calls"]) == 2
            body = (
                {
                    "error": {
                        "message": "controlled supplier failure",
                        "code": "invalid_request_error",
                    }
                }
                if failed
                else {
                    "choices": [
                        {
                            "message": {"content": "OK from source model test HTTP"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )
            encoded = json.dumps(body).encode()
            self.send_response(400 if failed else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            return

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    provider_thread = Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    evidence["base_url"] = f"http://127.0.0.1:{provider.server_port}/v1"
    settings.model_discovery_allowed_hosts = ["127.0.0.1"]
    original_create = CanvasModelTestService.create

    def lose_first_ack(self, payload, key):
        result = original_create(self, payload, key)
        evidence["admissions"] += 1
        if evidence["admissions"] == 1:
            raise WorkflowError("controlled_ack_lost", "受控网络回执丢失", 503)
        return result

    monkeypatch.setattr(CanvasModelTestService, "create", lose_first_ack)
    stopped = Event()
    executor = GenerationExecutionService(
        identity_app[1], settings, GenerationGateway(settings), None
    )

    def execute_tasks():
        while not stopped.wait(0.05):
            try:
                with identity_app[1]() as session:
                    rows = list(
                        session.scalars(select(AsyncTask).where(AsyncTask.status == "queued"))
                    )
                    waiting = [(str(task.id), task.message_version) for task in rows]
                for identifier, version in waiting:
                    executor.execute(identifier, version)
            except BaseException as error:
                evidence["worker_errors"].append(repr(error))
                stopped.set()

    worker = Thread(target=execute_tasks, daemon=True)
    worker.start()
    try:
        yield evidence
    finally:
        stopped.set()
        worker.join(5)
        assert not worker.is_alive()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(5)
        assert not provider_thread.is_alive()


@pytest.mark.parametrize("run_model_test", [False, True])
def test_original_model_settings_persist_redact_isolate_and_keep_conflict_draft(
    identity_app, run_model_test, monkeypatch
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    author, actor = account(identity_app, "canvas_model_settings_author")
    other, reader = account(identity_app, "canvas_model_settings_other")
    assert author.post("/api/v1/auth/logout").status_code == 200
    assert other.post("/api/v1/auth/logout").status_code == 200
    settings = identity_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4194"})
    with (
        controlled_model_test_runtime(
            identity_app, settings, run_model_test, monkeypatch
        ) as evidence,
        isolated_api(identity_app[1], settings) as api_url,
    ):
        process = subprocess.Popen(
            [node, "scripts/verify-model-settings-python.mjs"],
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
            "author": "canvas_model_settings_author",
            "other": "canvas_model_settings_other",
            "password": PASSWORD,
            "actorId": actor["id"],
            "otherId": reader["id"],
            "runModelTest": run_model_test,
            "providerURL": evidence.get("base_url"),
        }
        try:
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Model settings browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Model settings browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["source_service_created"] and result["logical_id_acknowledged"]
        assert result["source_builtin_available"]
        assert result["default_choice_saved"] and result["fresh_browser_restored"]
        assert result["credentials_redacted"] and result["browser_cache_has_no_secrets"]
        assert result["source_service_updated"] and result["source_service_deleted"]
        assert result["account_catalog_isolated"] and result["conflict_kept_draft"]
        assert result["zero_generation_submissions"] is not run_model_test
        assert result["canvas_task_submissions"] == 0 and not result["page_errors"]
        if run_model_test:
            assert result["source_model_test_completed"]
            assert result["source_model_test_failure_displayed"]
            assert result["uncertain_model_test_ack_replayed"]
            assert result["source_task_center_showed_actual_result"]
            assert result["private_model_tests_hidden"]
            assert result["model_test_submissions"] == evidence["admissions"] == 3
            assert len(evidence["calls"]) == 2
            assert all(path == "/v1/chat/completions" for path, _ in evidence["calls"])
            assert not evidence["worker_errors"]
    with identity_app[1]() as session:
        catalog = session.scalar(select(CanvasModelCatalog))
        assert catalog.channels_json == []
        assert catalog.credentials_cipher is None
        binding = session.scalar(select(CanvasChannelModel))
        model = session.get(AIModelConfig, binding.model_config_id)
        assert model.is_deleted and not model.enabled
        assert session.scalar(select(func.count()).select_from(CanvasTaskBinding)) == 0
        if run_model_test:
            tasks = list(session.scalars(select(AsyncTask).order_by(AsyncTask.id)))
            assert [task.status for task in tasks] == ["succeeded", "failed"]
            assert all(
                task.project_id is None and task.scope_user_id == task.initiated_by
                for task in tasks
            )
