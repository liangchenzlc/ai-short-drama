"""原表情手工框选、多参考图、局部合成和真实独立Worker恢复。"""

import base64
import hashlib
import json
import math
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
from tests.integration.test_canvas_image_tools_browser import minio_bytes
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
        reason="Enable original emotion UI with real isolated AMQP/HTTP/MySQL/MinIO",
    ),
]


def patterned_source() -> bytes:
    image = Image.new("RGB", (320, 180))
    image.putdata(
        [
            ((x * 7) % 256, (y * 13) % 256, (x * 5 + y * 9) % 256)
            for y in range(180)
            for x in range(320)
        ]
    )
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    "scenario,width,mask_supported",
    [("normal", 1440, True), ("recovery", 600, True), ("no-mask", 1440, False)],
)
def test_original_emotion_ui_composites_only_selected_face(
    broker_app, database_errors, tmp_path, scenario, width, mask_supported
):
    runtime = broker_app
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original emotion browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user, project, path, request = seed(
        runtime.app, "emotion_browser_owner", kind="image"
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
                "maskSupported": mask_supported,
            },
            "size": {
                "parameter": "size",
                "values": ["1024x1024", "1536x1024", "1024x1536"],
                "default": "1024x1024",
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
            "apikey": "canvas-emotion-browser-placeholder-not-a-real-key",
            "runtime_profile": profile,
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
    initial = owner.get(path + "/my-document").json()["source_document"]
    initial["nodes"] = []
    initial["connections"] = []
    commit(owner, path, initial, "emotion-browser-empty")
    member, member_user = account(runtime.app, "emotion_browser_member")
    outsider, _ = account(runtime.app, "emotion_browser_outsider")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    original = patterned_source()
    runtime.hold_images.set()
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial["id"],
            "username": "emotion_browser_owner",
            "memberUsername": "emotion_browser_member",
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "imageWidth": 320,
            "imageHeight": 180,
            "scenario": scenario,
            "width": width,
            "maskSupported": mask_supported,
            "imageReleaseFile": str(runtime.image_release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-emotion-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=210)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Emotion browser timed out: {errors}\n{output}")
        finally:
            runtime.images_released.set()
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Emotion browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["scenario"] == scenario
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["actual_no_face_detection"] and report["model_loaded"]
    assert report["controls_cancel_kept_source"] and report["final_fresh_context_restored"]
    if scenario == "recovery":
        assert report["pending_fresh_context_restored"]
    assert report["uploads"] == (5 if mask_supported else 4)
    assert len(report["submissions"]) == 1
    assert records(runtime.app, owner_user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert runtime.image_started.is_set() and len(runtime.posts) == 1
    target_path, parts = runtime.posts[0]
    assert target_path == "/v1/images/edits"
    submitted = report["submissions"][0]
    assert (
        next(data for name, filename, mime, data in parts if name == "prompt").decode()
        == (submitted["prompt"])
    )
    source = report["source_node"]
    source_id = source["metadata"]["storageKey"].removeprefix("resource:")
    output_id = report["output_storage_key"].removeprefix("resource:")
    provider_id = report["provider_storage_key"].removeprefix("resource:")
    refs = [
        item["storageKey"].removeprefix("resource:")
        for item in submitted["input"]["referenceImages"]
    ]
    assert len(set(refs)) == 2 and source_id not in refs
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
        assert binding.source_node_key == source["id"]
        assert binding.source_snapshot["source_node"] == source
        assert record.request_data["input"]["reference_media_ids"] == refs
        source_media = session.get(MediaFile, int(source_id))
        output_media = session.get(MediaFile, int(output_id))
        assert output_media.published_at is not None
        provider_media = session.get(MediaFile, int(provider_id))
        assert provider_media.published_at is None
        assert minio_bytes(runtime.app, source_media) == original
        derived = minio_bytes(runtime.app, output_media)
        frozen_refs = [
            minio_bytes(runtime.app, session.get(MediaFile, int(value))) for value in refs
        ]
        assert [
            (mime, data) for name, filename, mime, data in parts if filename and name != "mask"
        ] == [
            ("image/png", frozen_refs[0]),
            ("image/jpeg", frozen_refs[1]),
        ]
        if mask_supported:
            mask_id = record.request_data["canvas_parameters"]["mask_media_id"]
            mask = minio_bytes(runtime.app, session.get(MediaFile, int(mask_id)))
            assert next(data for name, filename, mime, data in parts if name == "mask") == mask
            mask_image = Image.open(BytesIO(mask)).convert("RGBA")
            alpha = mask_image.getchannel("A")
            assert alpha.getextrema() == (0, 255)
        else:
            assert "mask_media_id" not in record.request_data["canvas_parameters"]
            assert not any(name == "mask" for name, filename, mime, data in parts)
    region = report["emotion_edit"]["editRegion"]
    crop = Image.open(BytesIO(frozen_refs[0])).convert("RGBA")
    expected_crop = (
        Image.open(BytesIO(original))
        .convert("RGBA")
        .crop(
            (
                region["x"],
                region["y"],
                region["x"] + region["width"],
                region["y"] + region["height"],
            )
        )
    )
    assert crop.size == expected_crop.size and crop.tobytes() == expected_crop.tobytes()
    assert Image.open(BytesIO(frozen_refs[1])).size == (384, 384)
    rendered = Image.open(BytesIO(derived)).convert("RGBA")
    baseline = Image.open(BytesIO(original)).convert("RGBA")
    assert rendered.size == baseline.size == (320, 180)
    face = report["emotion_edit"]["faceBox"]
    left = math.floor(face["x"] - face["width"] * 0.25)
    right = math.ceil(face["x"] + face["width"] * 1.25)
    top = math.floor(face["y"] - face["height"] * 0.3)
    bottom = math.ceil(face["y"] + face["height"] * 1.25)
    for y in range(180):
        for x in range(320):
            if x < left or x >= right or y < top or y >= bottom:
                assert rendered.getpixel((x, y)) == baseline.getpixel((x, y))
    center = (round(face["x"] + face["width"] / 2), round(face["y"] + face["height"] / 2))
    assert rendered.getpixel(center) != baseline.getpixel(center)
    assert report["output_sha256"] == hashlib.sha256(derived).hexdigest()
    final = owner.get(path + "/my-document").json()["source_document"]
    assert final["nodes"][0] == source
    for resource_id, expected in [(source_id, original), (output_id, derived)]:
        assert owner.get(f"{ROOT}/{resource_id}/file").content == expected
        assert member.get(f"{ROOT}/{resource_id}/file").content == expected
        assert outsider.get(f"{ROOT}/{resource_id}/file").status_code == 404
    for resource_id in refs:
        assert member.get(f"{ROOT}/{resource_id}/file").status_code == 404
    assert owner.get(f"{ROOT}/{provider_id}/file").status_code == 200
    assert member.get(f"{ROOT}/{provider_id}/file").status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    assert member.get(f"/api/v1/canvas-runtime/tasks/{task.id}").status_code == 404
    shared = member.get(path).json()["source_document"]
    result = next(node for node in shared["nodes"] if node["id"] == report["target_node_id"])
    assert result["metadata"]["storageKey"] == report["output_storage_key"]
    assert "taskId" not in result["metadata"] and "model" not in result["metadata"]
