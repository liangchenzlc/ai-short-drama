"""原已有分镜节点生成、窗口中断及同任务结构回填；不调用收费模型。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import select

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    CanvasToolRun,
    CanvasToolSubmission,
    MediaFile,
)
from short_drama.service.base import utcnow
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_storyboard_runtime import execute, storyboard_seed
from tests.integration.test_canvas_storyboard_runtime import (
    storyboard_runtime as storyboard_runtime,
)
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated original storyboard generation UI",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
def test_original_storyboard_generation_restores_the_running_task_and_stable_rows(
    storyboard_runtime, database_errors, tmp_path, width
):
    runtime = storyboard_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for explicit storyboard generation browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    owner, user, project, path, request = storyboard_seed(runtime)
    member, member_user = account(runtime.app, "storyboard_generation_reader")
    outsider, _ = account(runtime.app, "storyboard_generation_outside")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "allow-storyboard-terminal"
    runtime.state["hold"] = True
    stopped, worker_errors = Event(), []

    def work():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version).where(
                                AsyncTask.project_id == int(project["id"]),
                                AsyncTask.initiated_by == int(user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                                AsyncTask.next_run_at <= utcnow(),
                            )
                        )
                    )
                for identifier, version in pending:
                    if stopped.is_set():
                        break
                    assert version == 1
                    execute(runtime, {"id": str(identifier)})
        except BaseException as error:
            worker_errors.append(error)

    def release():
        while not stopped.wait(0.02):
            if release_path.exists():
                runtime.finish_allowed.set()
                return

    worker = Thread(target=work, name="storyboard-browser-executor", daemon=True)
    releaser = Thread(target=release, name="storyboard-browser-release", daemon=True)
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker.start()
        releaser.start()
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": request["projectId"],
            "username": "storyboard_runtime_owner",
            "memberUsername": "storyboard_generation_reader",
            "password": PASSWORD,
            "width": width,
            "prompt": "原分镜生成：列车进站，朋友在站台道别。",
            "partial": runtime.state["text"][: len(runtime.state["text"]) // 2],
            "title": "车站道别",
            "releasePath": str(release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-storyboard-generation-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=300)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Storyboard generation browser timed out: {errors}\n{output}")
        finally:
            runtime.finish_allowed.set()
            stopped.set()
            worker.join(15)
            releaser.join(5)
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert not worker.is_alive() and not releaser.is_alive()
        assert worker_errors == [], worker_errors
        assert process.returncode == 0, (
            f"Storyboard browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
    report = json.loads(output)
    assert report["step"] == "complete" and report["width"] == width
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["run_posts"] == report["execute_posts"] == 1 and report["task_posts"] == 0
    assert all(
        report[key]
        for key in (
            "source_saved_and_private_loading",
            "running_cookie_only_recovery",
            "stable_rows_restored",
            "member_shared_rows_private_task_hidden",
        )
    )
    assert len(runtime.posts) == 1
    final = owner.get(path + "/my-document").json()["source_document"]
    script = next(item for item in final["nodes"] if item["id"] == "runtime-node")
    assert (
        script["type"] == "script"
        and script["metadata"]["storyboard"]["rows"] == report["final_rows"]
    )
    assert (
        script["metadata"]["storyboardShotCount"] == "2"
        and script["metadata"]["storyboardShotDuration"] == "5"
    )
    assert outsider.get(path + "/my-document").status_code == 404
    assert member.get(f"/api/v1/canvas-runtime/tasks/{report['task_id']}").status_code == 404
    with runtime.app[1]() as session:
        task = session.get(AsyncTask, int(report["task_id"]))
        assert task.status == "succeeded" and task.initiated_by == int(user["id"])
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        assert record.adapter == (
            "openai_responses.v1" if runtime.protocol == "openai-response" else "openai_chat.v1"
        )
        assert record.response_data["canvas_storyboard"]["rows"] == report["final_rows"]
        assert record.request_data["canvas_storyboard_source"]["rows"] == []
        assert (
            record.request_data["source_snapshot"]["source_node"]["metadata"]["composerContent"]
            == config["prompt"]
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
        )
        result = session.scalar(
            select(CanvasResult).where(CanvasResult.task_binding_id == binding.id)
        )
        assert (
            result.attachment_status == "attached"
            and result.content_json["storyboard"]["rows"] == report["final_rows"]
        )
        run = session.scalar(select(CanvasToolRun))
        submission = session.scalar(select(CanvasToolSubmission))
        assert run.source_media_id is None and submission.run_id == run.id
        assert submission.approved_at and submission.async_task_id == task.id
        assert session.scalar(select(MediaFile.id)) is None
    assert database_errors == []
