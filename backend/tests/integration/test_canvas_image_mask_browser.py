"""原蒙版UI、独立Worker、真实AMQP/HTTP/MySQL/MinIO与新窗口恢复。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
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
        reason="Enable original mask UI with real isolated AMQP/HTTP/MySQL/MinIO",
    ),
]


def assert_selection_mask(selection: bytes, mask: bytes) -> None:
    """按实际原画笔选择逐像素核对二值alpha，不用包围框代替蒙版。"""
    with Image.open(BytesIO(selection)) as painted, Image.open(BytesIO(mask)) as stored:
        assert painted.size == stored.size == (37, 19)
        assert stored.format == "PNG" and stored.mode == "RGBA"
        selected_bytes = painted.convert("RGBA").tobytes()
        stored_bytes = stored.tobytes()
        selected = [selected_bytes[index : index + 4] for index in range(0, len(selected_bytes), 4)]
        pixels = [stored_bytes[index : index + 4] for index in range(0, len(stored_bytes), 4)]
        assert any(pixel[3] > 0 for pixel in selected)
        assert any(pixel[3] == 0 for pixel in selected)
        for region, pixel in zip(selected, pixels, strict=True):
            assert pixel[3] == (0 if region[3] > 0 else 255)
            if pixel[3] == 255:
                assert pixel[:3] == b"\xff\xff\xff"


@pytest.mark.parametrize(
    "scenario,width", [("normal", 1440), ("recovery", 600), ("unsupported", 1440), ("batch", 1440)]
)
def test_original_mask_ui_freezes_pixels_and_restores_one_broker_task(
    broker_app, database_errors, tmp_path, scenario, width
):
    runtime = broker_app
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original mask browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user, project, path, request = seed(
        runtime.app, "mask_browser_owner", kind="image"
    )
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    original_model = owner.get(model_path)
    assert original_model.status_code == 200, original_model.text
    profile = original_model.json()["runtime_profile"]
    profile["capability_config"] = {
        "version": 1,
        "image": {
            "references": {
                "promptMaxChars": 32000,
                "maxImages": 16,
                "maxImageBytes": 30 * 1024 * 1024,
                "maskSupported": scenario != "unsupported",
            },
            "size": {
                "parameter": "size",
                "values": ["1:1"],
                "default": "1:1",
                "allowCustom": True,
            },
            "quality": {
                "supported": True,
                "values": ["auto", "low", "medium", "high"],
                "default": "auto",
            },
            "transparentBackground": {"supported": True, "default": False},
            "responseFormat": {"supported": True},
            "outputFormat": {"supported": True},
            "maxOutputs": 15,
        },
    }
    patched = owner.patch(
        model_path,
        json={
            "row_version": original_model.json()["row_version"],
            "base_url": runtime.url,
            "apikey": "canvas-mask-browser-placeholder-not-a-real-key",
            "runtime_profile": profile,
        },
    )
    assert patched.status_code == 200, patched.text
    count = 3 if scenario == "batch" else 1
    if count > 1:
        workspace = owner.get(WORKSPACE).json()
        saved_preferences = owner.put(
            WORKSPACE,
            json={
                "expected_row_version": workspace["row_version"],
                "preferences": {**workspace["preferences"], "count": str(count)},
            },
        )
        assert saved_preferences.status_code == 200, saved_preferences.text
        assert saved_preferences.json()["preferences"]["count"] == str(count)
    initial = owner.get(path + "/my-document").json()["source_document"]
    initial["nodes"] = []
    initial["connections"] = []
    commit(owner, path, initial, "mask-browser-empty")
    member, member_user = account(runtime.app, "mask_browser_member")
    outsider, _ = account(runtime.app, "mask_browser_outsider")
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
            "username": "mask_browser_owner",
            "memberUsername": "mask_browser_member",
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "scenario": scenario,
            "width": width,
            "count": count,
            "imageReleaseFile": str(runtime.image_release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-mask-python.mjs"],
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
            pytest.fail(f"Mask browser timed out: {errors}\n{output}")
        finally:
            runtime.images_released.set()
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Mask browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["scenario"] == scenario
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["empty_inputs_rejected"] and report["erase_reset_cancel_kept_source"]
    final = owner.get(path + "/my-document").json()["source_document"]
    source = report["source_node"]
    assert final["nodes"][0] == source
    assert outsider.get(path + "/my-document").status_code == 404
    source_id = source["metadata"]["storageKey"].removeprefix("resource:")
    assert owner.get(f"{ROOT}/{source_id}/file").content == original
    assert member.get(f"{ROOT}/{source_id}/file").content == original
    with runtime.app[1]() as session:
        stored_source = session.get(MediaFile, int(source_id))
        assert minio_bytes(runtime.app, stored_source) == original

    if scenario == "unsupported":
        assert report["unsupported_model_rejected"]
        assert report["uploads"] == 1 and report["submissions"] == []
        assert len(final["nodes"]) == 1 and final["connections"] == []
        assert records(runtime.app, owner_user) == {"tasks": 0, "records": 0, "bindings": 0}
        assert runtime.posts == [] and not runtime.image_started.is_set()
        return

    assert report["final_fresh_context_restored"]
    if scenario == "recovery":
        assert report["pending_fresh_context_restored"]
    assert len(report["submissions"]) == count and runtime.image_started.is_set()
    assert records(runtime.app, owner_user) == {"tasks": count, "records": count, "bindings": count}
    assert report["uploads"] == count + 1
    assert len(runtime.posts) == count
    if count > 1:
        assert report["batch_representative_matches_child"]
    mask = (tmp_path / "browser" / "persisted-mask.png").read_bytes()
    selection = (tmp_path / "browser" / "selection.png").read_bytes()
    assert_selection_mask(selection, mask)
    assert report["mask_sha256"] == hashlib.sha256(mask).hexdigest()
    for target, parts in runtime.posts:
        assert target == "/v1/images/edits"
        assert [(name, mime, data) for name, filename, mime, data in parts if filename] == [
            ("image", "image/png", original),
            ("mask", "image/png", mask),
        ]
    shared = member.get(path).json()["source_document"]
    for admitted in report["admitted_tasks"]:
        output = next(item for item in report["outputs"] if item["taskId"] == admitted["id"])
        output_id = output["storageKey"].removeprefix("resource:")
        mask_id = admitted["maskResourceId"]
        assert output["sha256"] == hashlib.sha256(runtime.image).hexdigest()
        with runtime.app[1]() as session:
            task = session.get(AsyncTask, int(admitted["id"]))
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
            )
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
            )
            assert task.status == record.status == "succeeded"
            assert binding.node_key == admitted["nodeId"]
            assert binding.source_node_key == source["id"]
            assert record.request_data["input"]["reference_media_ids"] == [source_id]
            assert record.request_data["canvas_parameters"]["mask_media_id"] == mask_id
            assert "mask" not in (record.request_data.get("resolved_parameters") or {})
            stored_mask = session.get(MediaFile, int(mask_id))
            stored_output = session.get(MediaFile, int(output_id))
            assert stored_mask.published_at is None and stored_output.published_at is not None
            assert minio_bytes(runtime.app, stored_mask) == mask
            assert minio_bytes(runtime.app, stored_output) == runtime.image
        assert owner.get(f"{ROOT}/{mask_id}/file").content == mask
        assert member.get(f"{ROOT}/{mask_id}/file").status_code == 404
        assert outsider.get(f"{ROOT}/{mask_id}/file").status_code == 404
        assert member.get(f"{ROOT}/{output_id}/file").content == runtime.image
        assert outsider.get(f"{ROOT}/{output_id}/file").status_code == 404
        result = next(node for node in shared["nodes"] if node["id"] == admitted["nodeId"])
        assert result["metadata"]["storageKey"] == output["storageKey"]
        assert "mask" not in result["metadata"] and "taskId" not in result["metadata"]
