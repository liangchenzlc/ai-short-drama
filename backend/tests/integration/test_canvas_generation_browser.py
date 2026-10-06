"""原画布浏览器提交、刷新恢复及真实 Python/MySQL 执行器回填；供应商受控。"""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationResult
from short_drama.domain import AsyncTask
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_identity_collaboration import PASSWORD, account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable real browser/Python/MySQL canvas generation verification",
    ),
]


def test_original_canvas_submits_then_recovers_and_binds_the_same_text_task(identity_app, tmp_path):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    client, _ = account(identity_app, "canvas_generation_browser")
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = identity_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4188"})
    release_path = tmp_path / "provider-result-allowed"
    stopped = threading.Event()
    worker_errors = []

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            self.calls += 1
            deadline = time.monotonic() + 45
            while not release_path.exists():
                if stopped.wait(0.02) or time.monotonic() >= deadline:
                    raise RuntimeError("Browser did not confirm refresh before provider completion")
            callback = kwargs.get("on_text_delta")
            if callback is not None:
                callback("浏览器刷新后恢复的")
                callback("真实任务正文")
            return GenerationResult(
                "succeeded", "openai_chat.v1", text="浏览器刷新后恢复的真实任务正文"
            )

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], settings, gateway, None)

    def run_worker():
        try:
            while not stopped.wait(0.02):
                with identity_app[1]() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version).where(
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                                AsyncTask.next_run_at <= utcnow(),
                            )
                        )
                    )
                for identifier, version in pending:
                    if stopped.is_set():
                        break
                    executor.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    with isolated_api(identity_app[1], settings) as api_url:
        worker = threading.Thread(target=run_worker, name="canvas-test-generation", daemon=True)
        worker.start()
        config = {
            "apiUrl": api_url,
            "username": "canvas_generation_browser",
            "password": PASSWORD,
            "releasePath": str(release_path),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-generation-python.mjs"],
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
            pytest.fail(f"Generation browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            worker.join(5)
        assert not worker.is_alive(), "Task-owned generation worker did not stop"
        assert not worker_errors, worker_errors
        assert process.returncode == 0, f"Generation browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["source_saved_before_admission"]
        assert result["same_task_recovered_after_refresh"]
        assert result["source_bind_applied"] and result["persisted_after_fresh_browser"]
        assert result["task_post_count"] == gateway.calls == 1
        assert not result["page_errors"]
