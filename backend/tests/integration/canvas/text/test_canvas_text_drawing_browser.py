"""原绘图→文字界面、真实笔画与永久版本引用；不调用付费供应商。"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasDrawing,
    CanvasDrawingVersion,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskTextDelta,
    MediaFile,
)
from short_drama.domain.canvas_generation import CanvasTaskMediaReference
from short_drama.service.base import utcnow
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    CAPABILITY,
    FIRST,
    SECOND,
    assert_wire,
    executor,
    save_channel,
)
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    text_image_runtime as text_image_runtime,
)
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original drawing reference UI with isolated HTTP/MySQL/MinIO",
    ),
]
PROMPT = "描述 @绘图1 中的笔画。"


def test_original_drawing_text_reference_rejects_empty_then_freezes_saved_render(
    text_image_runtime, database_errors, tmp_path
):
    runtime = text_image_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    owner, owner_user = account(runtime.app, "canvas_drawing_text_owner")
    member, member_user = account(runtime.app, "canvas_drawing_text_member")
    outsider, _ = account(runtime.app, "canvas_drawing_text_outside")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "drawing-text-ui-project"},
        json={"name": "原绘图文字引用验证", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    source_key = initial.json()["source_key"]
    assert initial.json()["source_document"]["nodes"] == []
    capability = deepcopy(CAPABILITY)
    capability["references"]["maxImageBytes"] = 10 * 1024 * 1024
    save_channel(runtime, owner, capability=capability)
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "allow-drawing-text-terminal"
    stopped = threading.Event()
    worker_errors = []
    runtime.state["hold"] = True
    generation = executor(runtime)

    def work():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version)
                            .where(
                                AsyncTask.project_id == int(project["id"]),
                                AsyncTask.initiated_by == int(owner_user["id"]),
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

    def release():
        while not stopped.wait(0.02):
            if release_path.exists():
                runtime.finish_allowed.set()
                return

    worker = threading.Thread(target=work, name="canvas-drawing-text-worker", daemon=True)
    releaser = threading.Thread(target=release, name="canvas-drawing-text-release", daemon=True)
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker.start()
        releaser.start()
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": source_key,
            "username": "canvas_drawing_text_owner",
            "memberUsername": "canvas_drawing_text_member",
            "outsideUsername": "canvas_drawing_text_outside",
            "password": PASSWORD,
            "prompt": PROMPT,
            "first": FIRST,
            "second": SECOND,
            "releasePath": str(release_path),
            "protocol": runtime.protocol,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-text-drawing-python.mjs"],
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
            pytest.fail(f"Drawing text browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            runtime.finish_allowed.set()
            worker.join(15)
            releaser.join(5)
        assert not worker.is_alive() and not releaser.is_alive()
        assert not worker_errors, worker_errors
        assert process.returncode == 0, (
            f"Drawing text browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["failed_routes"] == [] and report["task_post_count"] == 1
    for field in (
        "original_search_created_drawing",
        "empty_drawing_rejected",
        "pointer_drawing_saved",
        "exact_drawing_reference_reused",
        "private_delta_kept_loading",
        "original_consumer_bound_text",
        "fresh_context_restored",
        "permissions_checked",
    ):
        assert report[field], field
    assert runtime.first_sent.is_set() and release_path.exists()
    assert runtime.supplier_errors == [] and len(runtime.posts) == 1
    drawing_path = (
        f"/api/v1/canvas-runtime/canvas-projects/{source_key}/drawings/{report['drawing_id']}"
    )
    current = owner.get(drawing_path).json()["drawing"]
    evidence = report["drawing"]
    assert current["revision"] == evidence["revision"] and current["shapeCount"] == 1
    assert current["render"]["resourceId"] == evidence["render_resource_id"]
    assert current["previewResourceId"] == evidence["preview_resource_id"]
    content = owner.get(
        f"/api/v1/canvas-runtime/resources/{evidence['render_resource_id']}/file"
    ).content
    assert hashlib.sha256(content).hexdigest() == evidence["render_sha256"]
    assert_wire(
        runtime,
        {"model": "gpt-4o-mini", "prompt": PROMPT, "input": {"config": {}, "textHistory": []}},
        [content],
        True,
    )
    document = owner.get(path + "/my-document").json()["source_document"]
    assert len(document["nodes"]) == 2 and len(document["connections"]) == 1
    saved_text = next(node for node in document["nodes"] if node["id"] == report["text_node_id"])
    assert saved_text["metadata"]["content"] == FIRST + SECOND
    with runtime.app[1]() as session:
        tasks = list(session.scalars(select(AsyncTask)))
        assert len(tasks) == 1 and tasks[0].status == "succeeded"
        task_id = tasks[0].id
        assert str(task_id) == report["task_id"] and tasks[0].initiated_by == int(owner_user["id"])
        binding = session.scalar(select(CanvasTaskBinding))
        assert binding.node_key == report["text_node_id"]
        record = session.scalar(select(AIGenerationRecord))
        assert record.text_content == FIRST + SECOND
        assert record.request_data["input"]["reference_media_ids"] == [
            evidence["render_resource_id"]
        ]
        frozen = record.request_data["canvas_request"]["input"]["referenceImages"]
        assert frozen[0]["storageKey"] == f"resource:{evidence['render_resource_id']}"
        assert "X-Amz-" not in json.dumps(record.request_data)
        references = list(session.scalars(select(CanvasTaskMediaReference)))
        assert [(row.media_id, row.role) for row in references] == [
            (int(evidence["render_resource_id"]), "reference_media_ids")
        ]
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "attached" and result.attachment_receipt_id is not None
        assert result.content_json == {"content": FIRST + SECOND}
        deltas = list(
            session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
        )
        assert [(delta.sequence, delta.content) for delta in deltas] == [(1, FIRST), (2, SECOND)]
        drawing = session.scalar(select(CanvasDrawing))
        assert drawing.source_key == report["drawing_id"] and drawing.archived_at is None
        version = session.scalar(
            select(CanvasDrawingVersion).where(
                CanvasDrawingVersion.drawing_id == drawing.id,
                CanvasDrawingVersion.row_version == int(evidence["revision"]),
            )
        )
        assert version.document_json["$snapshot_encoding"] == "json"
        assert json.loads(version.document_json["snapshot"]) == current["snapshot"]
        media = session.get(MediaFile, int(evidence["render_resource_id"]))
        assert media.checksum_sha256 == evidence["render_sha256"] and media.published_at
        assert (media.width, media.height) == (evidence["width"], evidence["height"])
        location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
        with runtime.app[0].state.storage.open(location.bucket, location.object_name) as stored:
            assert stored.read() == content
    assert member.get(f"/api/v1/canvas-runtime/tasks/{task_id}").status_code == 404
    assert outsider.get(drawing_path).status_code == 404
    assert database_errors == []
