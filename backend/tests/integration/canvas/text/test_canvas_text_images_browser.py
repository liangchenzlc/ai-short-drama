"""原图片→文字 UI、真实本机 Chat/Responses/MySQL/MinIO；普通节点仅终态回填。"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

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
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    FIRST,
    SECOND,
    WORKSPACE,
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
from tests.integration.canvas.resources.test_canvas_resources import png
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.video.test_canvas_text_video_runtime import video_bytes
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original text image UI with isolated Chat/Responses HTTP/MySQL/MinIO",
    ),
]
PROMPT = "请按连线顺序描述这两张图，保留颜色与顺序。"
EXPLICIT_PROMPT = "先描述 @图片2，再描述 @图片1，复核 @图片2。"
RESOLVED_PROMPT = "先描述 @图片1，再描述 @图片2，复核 @图片1。"
SYSTEM = "浏览器图片文字源系统上下文"


@pytest.fixture
def text_deadlock_evidence(resource_app, tmp_path):
    engine = resource_app[1].kw["bind"]

    def capture(context):
        if getattr(context.original_exception, "args", (None,))[0] != 1213:
            return
        with engine.connect() as connection:
            status = connection.exec_driver_sql("SHOW ENGINE INNODB STATUS").one()[2]
        section = status.split("LATEST DETECTED DEADLOCK", 1)[-1].split("TRANSACTIONS\n", 1)[0]
        lines = []
        for line in section.splitlines():
            if line.startswith(
                (
                    "***",
                    "TRANSACTION ",
                    "mysql tables",
                    "LOCK WAIT",
                    "MySQL thread",
                    "RECORD LOCKS",
                    "TABLE LOCK",
                )
            ):
                lines.append(line)
            elif re.match(r"^(SELECT|INSERT|UPDATE|DELETE)\b", line):
                # 只保留 SQL 动词和表名；不落盘 JSON 正文、参数或凭据。
                tables = re.findall(r"(?:FROM|INTO|UPDATE|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", line)
                lines.append(line.split()[0] + " tables=" + ",".join(tables))
        (tmp_path / "deadlock-locks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    event.listen(engine, "handle_error", capture)
    try:
        yield
    finally:
        event.remove(engine, "handle_error", capture)


@pytest.mark.parametrize(
    "media_kind,scenario",
    [("image", "automatic"), ("video", "automatic"), ("image", "explicit"), ("image", "batch")],
)
def test_original_media_to_text_ui_stream_stays_loading_then_binds_and_restores(
    text_image_runtime, database_errors, text_deadlock_evidence, tmp_path, media_kind, scenario
):
    runtime = text_image_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user = account(runtime.app, "canvas_text_image_browser_owner")
    member, member_user = account(runtime.app, "canvas_text_image_browser_member")
    outsider, _ = account(runtime.app, "canvas_text_image_outsider")
    project_response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "text-image-browser-project"},
        json={"name": "原图片文字生成验证", "workspace_mode": "infinite_canvas"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    source_key = initial.json()["source_key"]
    assert initial.json()["source_document"]["nodes"] == []
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
    originals = [
        png(color) if media_kind == "image" else video_bytes(tmp_path, color)
        for color in ("red", "blue")
    ]
    text_count = 3 if scenario == "batch" else 1
    prompt = PROMPT if scenario == "automatic" else EXPLICIT_PROMPT
    effective_prompt = PROMPT if scenario == "automatic" else RESOLVED_PROMPT
    reference_order = [0, 1] if scenario == "automatic" else [1, 0]
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
            "outsiderUsername": "canvas_text_image_outsider",
            "password": PASSWORD,
            "images": [base64.b64encode(content).decode() for content in originals],
            "mediaKind": media_kind,
            "prompt": prompt,
            "effectivePrompt": effective_prompt,
            "referenceOrder": reference_order,
            "textCount": text_count,
            "verifyMissingMention": scenario != "automatic",
            # 固定源 normalizeConfigSnapshot 清空旧全局提示，不把它传入普通节点。
            # 下方仍核对持久化旧偏好未被本次节点生成改写。
            "systemPrompt": "",
            "first": FIRST,
            "second": SECOND,
            "releasePath": str(release_path),
            "protocol": runtime.protocol,
            "outputDirectory": str(tmp_path / "browser"),
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
        "original_media_order_linked",
        "source_saved_before_admission",
        "private_delta_kept_original_loading",
        "original_media_unchanged",
        "original_consumer_bound_same_node",
        "saved_and_owner_closed",
        "fresh_context_restored",
        "restored_media_decoded",
        "permissions_checked",
    ):
        assert report[field], field
    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["failed_routes"] == []
    assert report["task_post_count"] == text_count and report["page_text_event_count"] == 0
    if scenario != "automatic":
        assert report["missing_mention_rejected"] and report["explicit_references_verified"]
    if scenario == "batch":
        assert report["text_count_bounds_verified"] and report["text_batch_layout_verified"]
    assert runtime.first_sent.is_set() and release_path.exists()
    assert runtime.supplier_errors == [] and len(runtime.posts) == text_count
    ordered_contents = [originals[index] for index in reference_order]
    for post in runtime.posts:
        assert_wire(
            SimpleNamespace(protocol=runtime.protocol, posts=[post]),
            {
                "model": "gpt-4o-mini",
                "prompt": effective_prompt,
                "input": {"config": {}, "textHistory": []},
            },
            ordered_contents if media_kind == "image" else [],
            True,
            videos=ordered_contents if media_kind == "video" else [],
        )
    final = owner.get(path + "/my-document")
    assert final.status_code == 200, final.text
    document = final.json()["source_document"]
    assert len(document["nodes"]) == 2 + text_count
    assert len(document["connections"]) == 1 + text_count
    text = next(item for item in document["nodes"] if item["id"] == report["text_node_id"])
    assert text["metadata"]["content"] == FIRST + SECOND
    assert owner.get(WORKSPACE).json()["preferences"]["systemPrompt"] == SYSTEM
    assert len(report["tasks"]) == text_count
    assert [item["batch_index"] for item in report["tasks"]] == list(range(text_count))
    assert report["tasks"][0]["target_node_id"] == report["text_node_id"]
    task_ids = [int(item["task_id"]) for item in report["tasks"]]
    media_ids = [int(media["resource_id"]) for media in report["media"]]
    reference_ids = [media_ids[index] for index in reference_order]
    with runtime.app[1]() as session:
        saved_ids = set(
            session.scalars(select(AsyncTask.id).where(AsyncTask.project_id == int(project["id"])))
        )
        assert saved_ids == set(task_ids)
        for item in report["tasks"]:
            task_id = int(item["task_id"])
            task = session.get(AsyncTask, task_id)
            assert task.status == "succeeded" and task.initiated_by == int(owner_user["id"])
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task_id)
            )
            assert binding.node_key == item["target_node_id"]
            assert binding.source_node_key == report["text_node_id"]
            assert binding.initiated_by == int(owner_user["id"])
            assert binding.client_operation_id == item["client_operation_id"]
            results = list(
                session.scalars(
                    select(CanvasResult).where(CanvasResult.task_binding_id == binding.id)
                )
            )
            assert len(results) == 1 and results[0].attachment_status == "attached"
            assert results[0].kind == "text" and results[0].content_json == {
                "content": FIRST + SECOND
            }
            assert results[0].media_id is None and results[0].attachment_receipt_id is not None
            deltas = list(
                session.scalars(
                    select(CanvasTaskTextDelta)
                    .where(CanvasTaskTextDelta.task_binding_id == binding.id)
                    .order_by(CanvasTaskTextDelta.sequence)
                )
            )
            assert [(delta.sequence, delta.content) for delta in deltas] == [
                (1, FIRST),
                (2, SECOND),
            ]
            assert all(delta.created_by == int(owner_user["id"]) for delta in deltas)
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
            )
            assert record.text_content == FIRST + SECOND
            assert record.adapter == (
                "openai_responses.v1" if runtime.protocol == "openai-response" else "openai_chat.v1"
            )
            frozen = record.request_data
            reference_field = (
                "reference_media_ids" if media_kind == "image" else "video_reference_media_ids"
            )
            assert frozen["input"][reference_field] == [
                str(identifier) for identifier in reference_ids
            ]
            assert frozen["canvas_request"]["prompt"] == effective_prompt
            metadata = frozen["canvas_request"]["input"]["metadata"]
            assert metadata["batchIndex"] == item["batch_index"]
            assert metadata["batchCount"] == text_count
            serialized = json.dumps(frozen, ensure_ascii=False)
            assert "X-Amz-" not in serialized
            assert (
                "reference_urls" not in frozen["input"]
                and "video_reference_urls" not in frozen["input"]
            )
            references = list(
                session.scalars(
                    select(CanvasTaskMediaReference)
                    .where(CanvasTaskMediaReference.task_binding_id == binding.id)
                    .order_by(CanvasTaskMediaReference.ordinal)
                )
            )
            assert [reference.media_id for reference in references] == reference_ids
            assert all(reference.role == reference_field for reference in references)
            saved_node = next(
                node for node in document["nodes"] if node["id"] == item["target_node_id"]
            )
            assert saved_node["metadata"]["content"] == FIRST + SECOND
            assert saved_node["metadata"]["composerContent"] == prompt
            assert saved_node["metadata"]["prompt"] == effective_prompt
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
            *((media["node_id"], report["text_node_id"]) for media in report["media"]),
            *((report["text_node_id"], item["target_node_id"]) for item in report["tasks"][1:]),
        ]
        for identifier, content in zip(media_ids, originals, strict=True):
            media = session.get(MediaFile, identifier)
            assert media.created_by == int(owner_user["id"]) and media.published_at
            assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
            assert media.byte_size == len(content)
            assert (media.width, media.height) == ((37, 19) if media_kind == "image" else (160, 90))
            if media_kind == "video":
                assert media.duration_ms == 1000
            expected_bucket = (
                settings.minio_image_bucket
                if media_kind == "image"
                else settings.minio_video_bucket
            )
            location = ObjectLocation.parse(media.storage_locator, {expected_bucket})
            with runtime.app[0].state.storage.open(location.bucket, location.object_name) as stored:
                assert stored.read() == content
    for task_id in task_ids:
        task_path = f"/api/v1/canvas-runtime/tasks/{task_id}"
        assert member.get(task_path).status_code == 404
        assert member.get(task_path + "/text-deltas").status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    assert (
        owner.get(path).json()["source_document"]["nodes"][-1]["metadata"]["content"]
        == FIRST + SECOND
    )
    assert database_errors == []
