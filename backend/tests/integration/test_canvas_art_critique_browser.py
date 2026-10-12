"""原审美菜单、阶段观察中断及仅cookies恢复；受控HTTP与隔离MySQL/MinIO。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from sqlalchemy import func, select

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasToolRun,
    CanvasToolSubmission,
    MediaFile,
)
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_editor_tools import ART_STAGES, tool_seed
from tests.integration.test_canvas_editor_tools import tool_runtime as tool_runtime
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize("tool_runtime", ["chat-completion"], indirect=True),
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated original art critique browser/HTTP/MySQL/MinIO verification",
    ),
]
WORKSPACE = "/api/v1/canvas-runtime/workspace/model-config"


def test_original_art_critique_recovers_approved_stages_without_duplicate_supplier_calls(
    tool_runtime, database_errors, tmp_path
):
    runtime = tool_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original art critique browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, user, project, path, request, _image = tool_seed(runtime)
    initial = owner.get(path + "/my-document").json()
    document = initial["source_document"]
    document.update(nodes=[], connections=[])
    emptied = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "art-browser-empty-source"},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert emptied.status_code == 200, emptied.text
    activated = owner.put(
        "/api/v1/canvas-runtime/plugins/ai-art-critique/activation", json={"enabled": True}
    )
    assert activated.status_code == 200, activated.text
    preferences = owner.get(WORKSPACE).json()
    selected = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": preferences["row_version"],
            "preferences": {
                **preferences["preferences"],
                "textModel": f"host-{request['logicalModelId']}::saved-model",
            },
        },
    )
    assert selected.status_code == 200, selected.text
    member, member_user = account(runtime.app, "art_browser_member")
    outsider, _outsider_user = account(runtime.app, "art_browser_outsider")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    original = png("red")
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    released = tmp_path / "allow-art-worker"
    stopped, worker_errors = threading.Event(), []
    generation = GenerationExecutionService(
        runtime.app[1], settings, runtime.gateway, runtime.app[0].state.storage
    )

    def work():
        try:
            while not stopped.wait(0.02):
                # 观察中断期间只保留真实已受理任务，恢复原UI后再驱动执行器。
                if not released.exists():
                    continue
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version)
                            .where(
                                AsyncTask.project_id == int(project["id"]),
                                AsyncTask.initiated_by == int(user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                                AsyncTask.next_run_at <= utcnow(),
                            )
                            .order_by(AsyncTask.id)
                        )
                    )
                for identifier, version in pending:
                    if stopped.is_set():
                        break
                    generation.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    worker = threading.Thread(target=work, name="canvas-art-browser-worker", daemon=True)
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker.start()
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial["source_key"],
            "username": "editor_tool_author",
            "memberUsername": "art_browser_member",
            "outsiderUsername": "art_browser_outsider",
            "password": PASSWORD,
            "protocol": runtime.protocol,
            "image": base64.b64encode(original).decode(),
            "releasePath": str(released),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-art-critique-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=240)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Art critique browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            worker.join(15)
        assert not worker.is_alive() and not worker_errors, worker_errors
        assert process.returncode == 0, (
            f"Art critique browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["failed_routes"] == [] and database_errors == []
    assert sorted(item["name"] for item in runtime.posts) == sorted(ART_STAGES)
    assert all(item["images"] == [original] for item in runtime.posts)
    final = owner.get(path + "/my-document").json()["source_document"]
    source = next(item for item in final["nodes"] if item["id"] == report["source_node_id"])
    target = next(item for item in final["nodes"] if item["id"] == report["target_node_id"])
    assert target["metadata"]["artCritique"]["status"] == "completed"
    assert len(target["metadata"]["artCritique"]["stageTaskIds"]) == 9
    assert source == report["original_source"]
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasToolRun)) == 1
        assert session.scalar(select(func.count()).select_from(CanvasToolSubmission)) == 9
        assert session.scalar(select(func.count()).select_from(AIGenerationRecord)) == 9
        run = session.get(CanvasToolRun, int(report["run_id"]))
        assert run.user_id == int(user["id"]) and run.execution_epoch == 2
        submissions = list(session.scalars(select(CanvasToolSubmission)))
        assert all(
            item.approved_at is not None and item.async_task_id is not None for item in submissions
        )
        assert {str(item.async_task_id) for item in submissions} == set(
            report["all_task_ids"].values()
        )
        media = session.get(MediaFile, int(report["resource_id"]))
        assert media.created_by == int(user["id"]) and media.published_at
        location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
        with runtime.app[0].state.storage.open(location.bucket, location.object_name) as response:
            stored = response.read()
        assert stored == original and hashlib.sha256(stored).hexdigest() == report["image_sha256"]
    assert member.get(path).status_code == 200 and outsider.get(path).status_code == 404
