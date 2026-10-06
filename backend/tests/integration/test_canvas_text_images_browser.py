"""原图片→文字 UI、真实本机 Chat stream/MySQL/MinIO；普通节点仅终态回填。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskTextDelta,
    MediaFile,
)
from short_drama.domain.canvas import CanvasEdge, CanvasNode
from short_drama.domain.canvas_generation import CanvasTaskMediaReference
from short_drama.service.base import utcnow
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_text_image_runtime import (
    FIRST,
    SECOND,
    WORKSPACE,
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
        reason="Enable original text image UI with isolated Chat HTTP/MySQL/MinIO",
    ),
]
PROMPT = "请按连线顺序描述这两张图，保留颜色与顺序。"
SYSTEM = "浏览器图片文字源系统上下文"


def test_original_image_to_text_ui_stream_stays_loading_then_binds_and_restores(
    text_image_runtime, database_errors, tmp_path
):
    runtime = text_image_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user = account(runtime.app, "canvas_text_image_browser_owner")
    member, member_user = account(runtime.app, "canvas_text_image_browser_member")
    outsider, _ = account(runtime.app, "canvas_text_image_browser_outsider")
    project_response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "text-image-browser-project"},
        json={"name": "原图片文字生成验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    source_key = initial.json()["source_key"]
    assert initial.json()["source_document"]["nodes"] == []
    join(runtime.app, owner, member, project["id"], member_user["id"])
    channel = save_channel(runtime, owner)
    preferences = owner.get(WORKSPACE).json()
    model_key = channel["id"] + "::gpt-4o-mini"
    selected = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": preferences["row_version"],
            "preferences": {
                **preferences["preferences"],
                "model": model_key,
                "textModel": model_key,
                "systemPrompt": SYSTEM,
            },
        },
    )
    assert selected.status_code == 200, selected.text
    originals = [png(color) for color in ("red", "blue")]
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "allow-chat-terminal"
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
                    generation.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    def release():
        while not stopped.wait(0.02):
            if release_path.exists():
                runtime.finish_allowed.set()
                return

    worker = threading.Thread(target=work, name="canvas-text-image-worker", daemon=True)
    releaser = threading.Thread(target=release, name="canvas-text-image-release", daemon=True)
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker.start()
        releaser.start()
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": source_key,
            "username": "canvas_text_image_browser_owner",
            "memberUsername": "canvas_text_image_browser_member",
            "outsiderUsername": "canvas_text_image_browser_outsider",
            "password": PASSWORD,
            "images": [base64.b64encode(content).decode() for content in originals],
            "prompt": PROMPT,
            "systemPrompt": SYSTEM,
            "first": FIRST,
            "second": SECOND,
            "releasePath": str(release_path),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-text-images-python.mjs"],
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
            pytest.fail(f"Text image browser timed out: {errors}\n{output}")
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
            f"Text image browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    for field in (
        "original_connection_created_text",
        "original_image_order_linked",
        "source_saved_before_admission",
        "private_delta_kept_original_loading",
        "original_images_unchanged",
        "original_consumer_bound_same_node",
        "fresh_context_restored",
        "permissions_checked",
    ):
        assert report[field], field
    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["failed_routes"] == []
    assert report["task_post_count"] == 1 and report["page_text_event_count"] == 0
    assert runtime.first_sent.is_set() and release_path.exists()
    assert runtime.supplier_errors == [] and len(runtime.posts) == 1
    post = runtime.posts[0]
    assert post["authenticated"] and post["custom_header"]
    assert post["body"]["model"] == "gpt-4o-mini" and post["body"]["stream"] is True
    assert post["body"].get("stream_options") == {"include_usage": True}
    assert post["body"]["messages"] == [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": "saved-image-0"}},
                {"type": "image_url", "image_url": {"url": "saved-image-1"}},
            ],
        },
    ]
    assert [image["bytes"] for image in post["images"]] == originals
    assert all(image["mime_type"] == "image/png" for image in post["images"])
    final = owner.get(path + "/my-document")
    assert final.status_code == 200, final.text
    document = final.json()["source_document"]
    assert len(document["nodes"]) == 3 and len(document["connections"]) == 2
    assert document["nodes"][-1]["id"] == report["text_node_id"]
    text = next(item for item in document["nodes"] if item["id"] == report["text_node_id"])
    assert text["metadata"]["content"] == FIRST + SECOND
    assert owner.get(WORKSPACE).json()["preferences"]["systemPrompt"] == SYSTEM
    task_id = int(report["task_id"])
    image_ids = [int(image["resource_id"]) for image in report["images"]]
    with runtime.app[1]() as session:
        task = session.get(AsyncTask, task_id)
        assert task.status == "succeeded" and task.initiated_by == int(owner_user["id"])
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task_id)
        )
        assert binding.node_key == binding.source_node_key == report["text_node_id"]
        assert binding.initiated_by == int(owner_user["id"])
        results = list(
            session.scalars(select(CanvasResult).where(CanvasResult.task_binding_id == binding.id))
        )
        assert len(results) == 1 and results[0].attachment_status == "attached"
        assert results[0].kind == "text" and results[0].content_json == {"content": FIRST + SECOND}
        assert results[0].media_id is None and results[0].attachment_receipt_id is not None
        deltas = list(
            session.scalars(
                select(CanvasTaskTextDelta)
                .where(CanvasTaskTextDelta.task_binding_id == binding.id)
                .order_by(CanvasTaskTextDelta.sequence)
            )
        )
        assert [(delta.sequence, delta.content) for delta in deltas] == [(1, FIRST), (2, SECOND)]
        assert all(delta.created_by == int(owner_user["id"]) for delta in deltas)
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        assert record.text_content == FIRST + SECOND
        frozen = record.request_data
        assert frozen["input"]["reference_media_ids"] == [
            str(identifier) for identifier in image_ids
        ]
        assert frozen["canvas_request"]["prompt"] == PROMPT
        serialized = json.dumps(frozen, ensure_ascii=False)
        assert "X-Amz-" not in serialized and "reference_urls" not in frozen["input"]
        references = list(
            session.scalars(
                select(CanvasTaskMediaReference)
                .where(CanvasTaskMediaReference.task_binding_id == binding.id)
                .order_by(CanvasTaskMediaReference.ordinal)
            )
        )
        assert [reference.media_id for reference in references] == image_ids
        stored_nodes = list(
            session.scalars(select(CanvasNode).where(CanvasNode.canvas_id == binding.canvas_id))
        )
        assert {node.node_key for node in stored_nodes} == {
            node["id"] for node in document["nodes"]
        }
        stored_edges = list(
            session.scalars(
                select(CanvasEdge)
                .where(CanvasEdge.canvas_id == binding.canvas_id)
                .order_by(CanvasEdge.position)
            )
        )
        assert [(edge.from_node_key, edge.to_node_key) for edge in stored_edges] == [
            (image["node_id"], report["text_node_id"]) for image in report["images"]
        ]
        for identifier, content in zip(image_ids, originals, strict=True):
            media = session.get(MediaFile, identifier)
            assert media.created_by == int(owner_user["id"]) and media.published_at
            assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
            assert media.byte_size == len(content) and (media.width, media.height) == (37, 19)
            assert minio_bytes(runtime.app, media) == content
    task_path = f"/api/v1/canvas-runtime/tasks/{task_id}"
    assert member.get(task_path).status_code == 404
    assert member.get(task_path + "/text-deltas").status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    assert (
        member.get(path + "/my-document").json()["source_document"]["nodes"][-1]["metadata"][
            "content"
        ]
        == FIRST + SECOND
    )
    assert database_errors == []
