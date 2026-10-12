"""已有调色节点原滑杆/连线、真实CSS/Canvas像素及私人文字任务恢复。"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResourceUpload,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskTextDelta,
    MediaFile,
)
from short_drama.domain.canvas_generation import CanvasTaskMediaReference
from short_drama.service.base import utcnow
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import ROOT, canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_text_image_runtime import (
    CAPABILITY,
    FIRST,
    SECOND,
    assert_wire,
    executor,
    save_channel,
)
from tests.integration.test_canvas_text_image_runtime import (
    text_image_runtime as text_image_runtime,
)
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original color grade with isolated HTTP/MySQL/MinIO",
    ),
]
PROMPT = "描述 @图片1 的颜色。"
NEUTRAL = {"brightness": 100, "contrast": 100, "saturate": 100, "hueRotate": 0}
REFERENCE_LIMIT = 1024 * 1024


def source_png():
    image = Image.new("RGB", (256, 128), (200, 100, 50))
    ImageDraw.Draw(image).rectangle((128, 0, 255, 127), fill=(40, 200, 80))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("width", [1440, 600])
def test_existing_color_grade_uses_real_pixels_and_restores_original_text_task(
    text_image_runtime, database_errors, tmp_path, width
):
    runtime = text_image_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for explicit color-grade browser verification"
    owner, owner_user = account(runtime.app, "color_grade_owner")
    member, member_user = account(runtime.app, "color_grade_member")
    outsider, _ = account(runtime.app, "color_grade_outsider")
    project, path, document = canvas(owner, "color-grade-project")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    original = source_png()
    response = upload(owner, original, source=document["id"], key="color-grade-source")
    assert response.status_code == 200, response.text
    source = response.json()["resource"]
    document["nodes"] = [
        {
            "id": "grade-source",
            "type": "image",
            "title": "调色原图",
            "position": {"x": -450, "y": -160},
            "width": 320,
            "height": 160,
            "metadata": {
                "status": "success",
                "content": f"{ROOT}/{source['id']}/file",
                "storageKey": "resource:" + source["id"],
                "naturalWidth": 256,
                "naturalHeight": 128,
                "mimeType": "image/png",
            },
        },
        {
            "id": "existing-grade",
            "type": "colorgrade",
            "title": "已有调色",
            "position": {"x": 40, "y": -160},
            "width": 420,
            "height": 360,
            "metadata": {"status": "idle"},
        },
    ]
    committed = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "existing-color-grade-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    save_channel(
        runtime,
        owner,
        capability={
            **CAPABILITY,
            "references": {**CAPABILITY["references"], "maxImageBytes": REFERENCE_LIMIT},
        },
    )
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "allow-color-grade-terminal"
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

    worker = threading.Thread(target=work, name="canvas-color-grade-worker", daemon=True)
    releaser = threading.Thread(target=release, name="canvas-color-grade-release", daemon=True)
    frontend = Path(__file__).resolve().parents[3] / "frontend/canvas"
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api:
        worker.start()
        releaser.start()
        config = {
            "apiUrl": api,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "sourceResourceId": source["id"],
            "username": "color_grade_owner",
            "memberUsername": "color_grade_member",
            "password": PASSWORD,
            "prompt": PROMPT,
            "first": FIRST,
            "second": SECOND,
            "neutralGrade": NEUTRAL,
            "protocol": runtime.protocol,
            "releasePath": str(release_path),
            "width": width,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-color-grade-python.mjs"],
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
            pytest.fail(f"Color-grade browser timed out: {errors}\n{output}")
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
            f"Color-grade browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete"
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["resource_posts"] == report["task_posts"] == 1
    for field in (
        "original_input_connected",
        "original_sliders_and_reset",
        "original_generation_reference",
        "cookie_only_task_recovery",
        "member_result_seen",
        "creation_menu_absent_like_source",
    ):
        assert report[field], field
    assert runtime.first_sent.is_set() and release_path.exists()
    assert runtime.supplier_errors == []
    transformed = owner.get(f"{ROOT}/{report['graded_resource_id']}/file").content
    assert 0 < len(transformed) <= REFERENCE_LIMIT
    assert len(transformed) == report["graded_bytes"]
    assert hashlib.sha256(transformed).hexdigest() == report["graded_checksum"]
    assert (tmp_path / "browser" / "color-grade-reference.png").read_bytes() == transformed
    with Image.open(BytesIO(transformed)) as image:
        assert image.format == "PNG" and image.size == (256, 128)
        for position, color in (((64, 64), (100, 50, 25)), ((192, 64), (20, 100, 40))):
            assert image.convert("RGB").getpixel(position) == color
    for filename in ("color-grade-preview.png", "color-grade-member-preview.png"):
        with Image.open(tmp_path / "browser" / filename) as image:
            for fraction, color in ((0.25, (100, 50, 25)), (0.75, (20, 100, 40))):
                pixel = image.convert("RGB").getpixel(
                    (int(image.width * fraction), image.height // 2)
                )
                assert all(abs(a - b) <= 3 for a, b in zip(pixel, color, strict=True)), pixel
    assert_wire(
        runtime,
        {"model": "gpt-4o-mini", "prompt": PROMPT, "input": {"config": {}, "textHistory": []}},
        [transformed],
        True,
    )
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 3 and len(final["connections"]) == 2
    assert {(edge["fromNodeId"], edge["toNodeId"]) for edge in final["connections"]} == {
        ("grade-source", "existing-grade"),
        ("existing-grade", report["text_node_id"]),
    }
    graded = next(item for item in final["nodes"] if item["id"] == "existing-grade")
    assert graded["metadata"]["colorGrade"] == {**NEUTRAL, "brightness": 50}
    assert (
        next(item for item in final["nodes"] if item["id"] == report["text_node_id"])["metadata"][
            "content"
        ]
        == FIRST + SECOND
    )
    shared = member.get(path + "/my-document").json()["source_document"]
    assert (
        next(item for item in shared["nodes"] if item["id"] == "existing-grade")["metadata"][
            "colorGrade"
        ]
        == graded["metadata"]["colorGrade"]
    )
    assert outsider.get(path + "/my-document").status_code == 404
    assert owner.get(f"{ROOT}/{source['id']}/file").content == original
    assert member.get(f"{ROOT}/{source['id']}/file").content == original
    assert member.get(f"{ROOT}/{report['graded_resource_id']}/file").status_code == 404
    assert outsider.get(f"{ROOT}/{report['graded_resource_id']}/file").status_code == 404
    assert member.get(f"/api/v1/canvas-runtime/tasks/{report['task_id']}").status_code == 404
    with runtime.app[1]() as session:
        task = session.scalar(select(AsyncTask))
        assert str(task.id) == report["task_id"] and task.status == "succeeded"
        assert task.initiated_by == int(owner_user["id"])
        assert len(list(session.scalars(select(AsyncTask.id)))) == 1
        assert len(list(session.scalars(select(MediaFile.id)))) == 2
        assert len(list(session.scalars(select(CanvasResourceUpload.id)))) == 2
        media = session.get(MediaFile, int(report["graded_resource_id"]))
        assert media.created_by == int(owner_user["id"]) and media.project_id == int(project["id"])
        assert media.published_at is None and media.format_code == "image/png"
        assert minio_bytes(runtime.app, media) == transformed
        assert media.checksum_sha256 == report["graded_checksum"] and media.byte_size == len(
            transformed
        )
        record = session.scalar(select(AIGenerationRecord))
        assert record.text_content == FIRST + SECOND
        assert record.request_data["input"]["reference_media_ids"] == [report["graded_resource_id"]]
        frozen = record.request_data["canvas_request"]["input"]["referenceImages"]
        assert frozen[0]["storageKey"] == "resource:" + report["graded_resource_id"]
        assert "X-Amz-" not in json.dumps(record.request_data)
        references = list(session.scalars(select(CanvasTaskMediaReference)))
        assert [(item.media_id, item.role) for item in references] == [
            (media.id, "reference_media_ids")
        ]
        binding = session.scalar(select(CanvasTaskBinding))
        assert binding.node_key == report["text_node_id"]
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "attached" and result.attachment_receipt_id is not None
        assert result.content_json == {"content": FIRST + SECOND}
        deltas = list(
            session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
        )
        assert [(item.sequence, item.content) for item in deltas] == [(1, FIRST), (2, SECOND)]
    assert database_errors == []
