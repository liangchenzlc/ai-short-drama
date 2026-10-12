"""已有文字节点的原 UI Gemini 输入/生成/回填与全新浏览器恢复。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasResult, CanvasTaskBinding
from short_drama.service.base import utcnow
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_gemini_runtime import (
    DEFAULTS,
    FIRST,
    HEADER_VALUE,
    KEY,
    REASONING,
    SECOND,
    assert_wire,
    gemini_seed,
)
from tests.integration.test_canvas_gemini_runtime import gemini_runtime as gemini_runtime
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_text_image_runtime import executor
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original Gemini UI with isolated HTTP/MySQL and Chrome",
    ),
]
PROMPT = "原画布Gemini普通文字：请给出两段正文。"


def test_original_gemini_text_ui_keeps_loading_binds_final_and_restores(
    gemini_runtime, database_errors, tmp_path
):
    runtime = gemini_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for explicit Gemini UI verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    owner, owner_user, project, path, seeded = gemini_seed(runtime)
    initial = owner.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"][0]["title"] = "Gemini原界面文字"
    document["nodes"][0]["metadata"] = {"content": "", "prompt": ""}
    response = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "gemini-ui-initial"},
        json={
            "expected_row_version": initial["row_version"],
            "source_document": document,
        },
    )
    assert response.status_code == 200, response.text
    member, member_user = account(runtime.app, "canvas_gemini_ui_reader")
    outsider, _ = account(runtime.app, "canvas_gemini_ui_outside")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "allow-gemini-terminal"
    stopped, worker_errors = Event(), []
    runtime.state["hold"] = True
    execution = executor(runtime)

    def work():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version).where(
                                AsyncTask.project_id == int(project["id"]),
                                AsyncTask.initiated_by == int(owner_user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                                AsyncTask.next_run_at <= utcnow(),
                            )
                        )
                    )
                for identifier, version in pending:
                    if stopped.is_set():
                        break
                    execution.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    def release():
        while not stopped.wait(0.02):
            if release_path.exists():
                runtime.finish_allowed.set()
                return

    worker = Thread(target=work, name="canvas-gemini-ui-worker", daemon=True)
    releaser = Thread(target=release, name="canvas-gemini-ui-release", daemon=True)
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker.start()
        releaser.start()
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "nodeId": "runtime-node",
            "username": "canvas_gemini_author",
            "memberUsername": "canvas_gemini_ui_reader",
            "outsiderUsername": "canvas_gemini_ui_outside",
            "password": PASSWORD,
            "prompt": PROMPT,
            "first": FIRST,
            "second": SECOND,
            "reasoning": REASONING,
            "releasePath": str(release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-gemini-text-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Gemini UI timed out: {errors}\n{output}")
        finally:
            runtime.finish_allowed.set()
            stopped.set()
            worker.join(15)
            releaser.join(5)
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert not worker.is_alive() and not releaser.is_alive()
        assert worker_errors == [], worker_errors
        assert process.returncode == 0, (
            f"Gemini UI failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
    report = json.loads(output)
    assert (
        report["step"] == "complete"
        and report["page_errors"] == []
        and report["failed_routes"] == []
    )
    assert report["source_saved_before_admission"] and report["private_loading_verified"]
    assert report["final_bound_and_private_reasoning"] and report["fresh_context_restored"]
    assert report["saved_and_owner_closed"]
    assert report["task_posts"] == 1 and report["text_event_requests"] == 0
    assert len(runtime.posts) == 1
    evidence = runtime.posts[0]
    assert evidence["authenticated"] and evidence["bearer_absent"] and evidence["custom_header"]
    assert_wire(runtime, {"input": {"config": {}, "textHistory": []}, "prompt": PROMPT})
    with runtime.app[1]() as session:
        task = session.get(AsyncTask, int(report["task_id"]))
        assert task.status == "succeeded" and task.initiated_by == int(owner_user["id"])
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        assert record.config_id == int(seeded["logicalModelId"])
        assert record.adapter == "gemini_generate_content.v1"
        assert record.text_content == FIRST + SECOND
        assert record.response_data["canvas_reasoning"] == REASONING
        assert record.config_snapshot["canvas_text_defaults"] == DEFAULTS
        frozen = json.dumps(
            [record.request_data, record.config_snapshot, record.response_data], ensure_ascii=False
        )
        assert KEY not in frozen and HEADER_VALUE not in frozen
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
        )
        result = session.scalar(
            select(CanvasResult).where(CanvasResult.task_binding_id == binding.id)
        )
        assert result.content_json == {"content": FIRST + SECOND}
        assert result.attachment_status == "attached" and result.attachment_receipt_id is not None
        assert binding.node_key == binding.source_node_key == "runtime-node"
    assert database_errors == []
