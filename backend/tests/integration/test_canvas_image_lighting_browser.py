"""原打光UI、独立Worker、真实AMQP/HTTP/MySQL/MinIO及关闭旧窗口恢复。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding, MediaFile
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_broker import broker_app as broker_app
from tests.integration.test_canvas_generation_runtime import WORKSPACE, records, seed
from tests.integration.test_canvas_image_tools_browser import minio_bytes, pixel_pattern_png
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_library_deletion import commit
from tests.integration.test_canvas_resources import ROOT
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original lighting UI with real isolated AMQP/HTTP/MySQL/MinIO",
    ),
]


@pytest.mark.parametrize("scenario,width", [("normal", 1440), ("recovery", 600)])
def test_original_lighting_ui_keeps_one_task_and_permanent_result(
    broker_app, database_errors, tmp_path, scenario, width
):
    runtime = broker_app
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original lighting browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user, project, path, request = seed(
        runtime.app, "lighting_browser_owner", kind="image"
    )
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    original_model = owner.get(model_path)
    assert original_model.status_code == 200, original_model.text
    patched = owner.patch(
        model_path,
        json={
            "row_version": original_model.json()["row_version"],
            "base_url": runtime.url,
            "apikey": "canvas-lighting-browser-placeholder-not-a-real-key",
        },
    )
    assert patched.status_code == 200, patched.text
    workspace = owner.get(WORKSPACE).json()
    preferences = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {**workspace["preferences"], "count": "3"},
        },
    )
    assert preferences.status_code == 200, preferences.text
    assert preferences.json()["preferences"]["count"] == "3"
    initial = owner.get(path + "/my-document").json()["source_document"]
    initial["nodes"] = []
    initial["connections"] = []
    commit(owner, path, initial, "lighting-browser-empty")
    member, member_user = account(runtime.app, "lighting_browser_member")
    outsider, _ = account(runtime.app, "lighting_browser_outsider")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    original = pixel_pattern_png()
    runtime.hold_images.set()
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial["id"],
            "username": "lighting_browser_owner",
            "memberUsername": "lighting_browser_member",
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "scenario": scenario,
            "width": width,
            "imageReleaseFile": str(runtime.image_release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-lighting-python.mjs"],
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
            pytest.fail(f"Lighting browser timed out: {errors}\n{output}")
        finally:
            runtime.images_released.set()
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Lighting browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["scenario"] == scenario
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["lighting_controls_cancel_kept_source"]
    assert report["idle_fresh_context_restored"]
    assert report["final_fresh_context_restored"]
    if scenario == "recovery":
        assert report["pending_fresh_context_restored"]
    assert report["uploads"] == len(report["submissions"]) == 1
    assert records(runtime.app, owner_user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert runtime.image_started.is_set() and len(runtime.posts) == 1
    target_path, parts = runtime.posts[0]
    assert target_path == "/v1/images/edits"
    assert [(name, mime, data) for name, filename, mime, data in parts if filename] == [
        ("image", "image/png", original)
    ]
    submitted = report["submissions"][0]
    assert (
        next(data for name, filename, mime, data in parts if name == "prompt").decode()
        == (submitted["prompt"])
    )
    final = owner.get(path + "/my-document").json()["source_document"]
    source = report["source_node"]
    assert final["nodes"][0] == source
    assert outsider.get(path + "/my-document").status_code == 404
    source_id = source["metadata"]["storageKey"].removeprefix("resource:")
    output_id = report["output_storage_key"].removeprefix("resource:")
    assert report["output_sha256"] == hashlib.sha256(runtime.image).hexdigest()
    with runtime.app[1]() as session:
        task = session.get(AsyncTask, int(report["task_id"]))
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
        )
        assert task.status == record.status == "succeeded"
        assert binding.node_key == report["target_node_id"]
        assert binding.source_node_key == report["idle_node_id"]
        assert record.request_data["input"]["reference_media_ids"] == [source_id]
        assert "mask_media_id" not in record.request_data["canvas_parameters"]
        assert record.request_data["input"]["prompt"] == submitted["prompt"]
        stored_source = session.get(MediaFile, int(source_id))
        stored_output = session.get(MediaFile, int(output_id))
        assert stored_output.published_at is not None
        assert minio_bytes(runtime.app, stored_source) == original
        assert minio_bytes(runtime.app, stored_output) == runtime.image
    for resource_id, expected in [(source_id, original), (output_id, runtime.image)]:
        assert owner.get(f"{ROOT}/{resource_id}/file").content == expected
        assert member.get(f"{ROOT}/{resource_id}/file").content == expected
        assert outsider.get(f"{ROOT}/{resource_id}/file").status_code == 404
    assert member.get(f"/api/v1/canvas-runtime/tasks/{task.id}").status_code == 404
    shared = member.get(path).json()["source_document"]
    result = next(node for node in shared["nodes"] if node["id"] == report["target_node_id"])
    assert result["metadata"]["storageKey"] == report["output_storage_key"]
    assert "taskId" not in result["metadata"] and "model" not in result["metadata"]
