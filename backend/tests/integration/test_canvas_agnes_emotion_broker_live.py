"""显式授权：原表情UI、真实Agnes双参考图、正式八角色与局部合成恢复。"""

import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageChops
from sqlalchemy import select

from short_drama.core.crypto import KeyCipher
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasLibraryAsset,
    CanvasLibraryAssetReference,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskMediaReference,
    MediaFile,
)
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.publisher import RabbitSender
from tests.integration.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.test_canvas_browser import stop_browser
from tests.integration.test_canvas_generation_broker import eventually
from tests.integration.test_canvas_generation_runtime import TASKS, WORKSPACE, records
from tests.integration.test_canvas_resources import ROOT, canvas
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    official_stack,
    read_json,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_BROKER_INTEGRATION",
            )
        ),
        reason="Explicit live Agnes, AMQP, isolated media and original browser opt-in required",
    ),
]


@pytest.mark.parametrize("width", [1440, 390])
def test_original_emotion_real_agnes_through_formal_generation_worker(
    resource_app, tmp_path, monkeypatch, width
):
    app, factory, current_settings = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    assert not os.environ.get("AGNES_ASSISTANT_BROKER_LIFECYCLE_MODE")
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original emotion browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    reference_path = Path(os.environ.get("AGNES_EMOTION_REFERENCE_IMAGE", ""))
    assert reference_path.is_file(), (
        "Archived anonymous portrait required; prior lighting content failure remains failed"
    )
    original_bytes = reference_path.read_bytes()
    source_hash = hashlib.sha256(original_bytes).hexdigest()
    assert source_hash == "29b3fde68f828e41b0011e75176f3b3a063157401315bfba6b35c750efe12fdb"
    with Image.open(reference_path) as reference:
        assert reference.format == "PNG" and reference.size == (1024, 1024)
    app.state.settings = current_settings.model_copy(update={"agent_enabled": True})
    owner, user = account(resource_app, "ae_o_" + str(width))
    member, member_user = account(resource_app, "ae_m_" + str(width))
    outsider, _ = account(resource_app, "ae_x_" + str(width))
    project, path, document = canvas(owner, "agnes-emotion-" + uuid4().hex)
    document["nodes"] = []
    document["connections"] = []
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-emotion-empty"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200
    join(resource_app, owner, member, project["id"], member_user["id"])
    secret = live_key()
    capability = {
        "version": 1,
        "image": {
            "references": {
                "promptMaxChars": 32000,
                "maxImages": 16,
                "maxImageBytes": 30 * 1024 * 1024,
                "maskSupported": False,
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
            "transparentBackground": {"supported": False, "default": False},
            "responseFormat": {"supported": True},
            "outputFormat": {"supported": True},
            "maxOutputs": 1,
        },
    }
    created = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes表情真实验收",
            "service_type": "image",
            "provider": "Agnes",
            "model_key": "agnes-image-2.5-flash",
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": "openai-image",
                "capability_config": capability,
            },
        },
    )
    if created.status_code != 201:
        pytest.fail("Saved live emotion model failed: HTTP " + str(created.status_code))
    model = created.json()
    assert_no_secret(model, secret)
    assert model["runtime_profile"]["capability_config"] == capability
    selection = f"host-{model['id']}::{model['model_key']}"
    workspace = owner.get(WORKSPACE).json()
    preferences = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "model": selection,
                "imageModel": selection,
                "size": "1024x1024",
                "quality": "auto",
                "count": "1",
                "canvasImageCount": "1",
            },
        },
    )
    assert preferences.status_code == 200
    assert preferences.json()["preferences"]["count"] == "1"
    assert_no_secret(preferences.json(), secret)
    with factory() as session:
        saved_model = session.get(AIModelConfig, int(model["id"]))
        if saved_model.apikey == secret:
            raise AssertionError("Saved emotion credential was not encrypted")
        if (
            KeyCipher(current_settings.encryption_key.get_secret_value()).decrypt(
                saved_model.apikey
            )
            != secret
        ):
            raise AssertionError("Saved emotion credential differs from the authorized key")
    browser_port = 4199
    settings = app.state.settings.model_copy(
        update={
            "public_origin": f"http://127.0.0.1:{browser_port}",
            "generation_image_budget_seconds": 360,
            "generation_queue_namespace": "assistant_broker_live_" + uuid4().hex,
            "smtp_host": "",
            "smtp_from": "",
            "render_scratch_root": str(tmp_path / "render-scratch"),
        }
    )
    monkeypatch.setenv("AGNES_GENERATION_BROKER_DIRECTORY", str(tmp_path))
    config = {
        "projectId": project["id"],
        "canvasId": project["primary_canvas_id"],
        "sourceKey": document["id"],
        "username": user["username"],
        "memberUsername": member_user["username"],
        "password": PASSWORD,
        "image": base64.b64encode(original_bytes).decode(),
        "sourceSha256": source_hash,
        "sourceDimensions": {"width": 1024, "height": 1024},
        "faceBox": {"x": 496, "y": 191, "width": 194, "height": 226},
        "maskSupported": False,
        "count": 1,
        "width": width,
        "browserPort": browser_port,
        "imageReleaseFile": str(tmp_path / "real-provider-submit-allowed"),
        "outputDirectory": str(tmp_path / "browser"),
    }
    with official_stack(
        factory,
        settings,
        tmp_path,
        generation_observer_module="tests.integration.agnes_generation_broker_observer",
    ) as (api_url, stack, workers):
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [node, "scripts/verify-agnes-emotion-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=600)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            process.communicate(timeout=10)
            pytest.exit("Live emotion timed out; reconcile original task before another POST", 1)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        if process.returncode:
            pytest.exit("Live emotion requires reconciliation: " + errors + "\n" + output, 1)
        browser = json.loads(output)
        assert browser["step"] == "complete" and browser["width"] == width
        assert browser["page_errors"] == browser["http_errors"] == []
        task_id = int(browser["task_id"])
        deliveries = eventually(
            lambda: read_json(tmp_path / "generation-worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) >= 2
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert workers["image"].pid in {deliveries["pid"], deliveries["parent_pid"]}
        stack["roles"]["image"]["execution_pid"] = deliveries["pid"]
        normal_deliveries = deliveries["deliveries"]
        normal_count = len(normal_deliveries)
        with factory() as session:
            task = session.get(AsyncTask, task_id)
            assert [int(row["message_version"]) for row in normal_deliveries] == list(
                range(1, task.message_version)
            )
        for row in normal_deliveries:
            assert int(row["task_id"]) == task_id and row["state"] == "SUCCESS"
            assert row["delivery"]["exchange"] == settings.generation_queue_namespace + ".tasks"
            assert row["delivery"]["routing_key"] == "image"
            assert row["delivery"]["redelivered"] is False
        sender = RabbitSender(settings)
        try:
            original_version = int(normal_deliveries[0]["message_version"])
            sender(task_id, original_version, "image")
            sender(task_id, original_version - 1, "image")
        finally:
            sender.app.close()
        deliveries = eventually(
            lambda: read_json(tmp_path / "generation-worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) == normal_count + 2
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert all(row["state"] == "SUCCESS" for row in deliveries["deliveries"])
        assert all(worker.poll() is None for worker in workers.values())
    provider = read_json(tmp_path / "provider-report.json")
    assert provider is not None
    assert provider["worker_pid"] == stack["roles"]["image"]["execution_pid"]
    assert provider["snowflake_worker_id"] == ROLE_IDS["image"]
    assert provider["model"] == "agnes-image-2.5-flash" and provider["submissions"] == 1
    assert len(provider["requests"]) == len(provider["results"]) == 1
    assert provider["results"][0]["status"] == "succeeded" and provider.get("error") is None
    request = provider["requests"][0]
    assert request["method"] == "POST" and request["host"] == "api.agnes-ai.cn"
    assert request["path"] == "/v1/images/generations" and request["http_status"] == 200
    assert request["options"]["model"] == "agnes-image-2.5-flash"
    assert request["options"]["size"] == browser["emotion_edit"]["providerSize"]
    assert "ratio" not in request["options"]
    assert all(stack["cleanup"].values())
    assert len(stack["roles"]) == len({row["pid"] for row in stack["roles"].values()}) == 8
    assert browser["controls_cancel_kept_source"] and browser["model_loaded"]
    assert browser["pre_submit_fresh_context_restored"]
    assert browser["pending_fresh_context_restored"] and browser["final_fresh_context_restored"]
    assert browser["uploads"] == 4 and len(browser["submissions"]) == 1
    assert browser["node_bind_operations"] == 0 and browser["observation_errors"] == []
    assert records(resource_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert [row["phase"] for row in browser["source_views"]] == [
        "initial",
        "pre-submit-fresh",
        "running-fresh",
        "terminal-fresh",
    ]
    assert all(row["sha256"] == source_hash for row in browser["source_views"])
    assert browser["output_dimensions"] == {"width": 1024, "height": 1024}
    submitted = browser["submissions"][0]
    assert submitted["prompt"] == browser["expected_prompt"]
    assert submitted["logicalModelId"] == model["id"] and submitted["model"] == selection
    assert submitted["input"]["config"]["quality"] == "high"
    assert int(submitted["input"]["config"]["count"]) == 1
    assert not submitted["input"].get("mask")
    source = browser["source_node"]
    final_node = next(
        item for item in browser["final_nodes"] if item["id"] == browser["target_node_id"]
    )
    source_id = source["metadata"]["storageKey"].removeprefix("resource:")
    output_id = browser["output_storage_key"].removeprefix("resource:")
    provider_id = browser["provider_storage_key"].removeprefix("resource:")
    refs = [
        item["storageKey"].removeprefix("resource:")
        for item in submitted["input"]["referenceImages"]
    ]
    assert len(set(refs)) == 2 and source_id not in refs
    assert len({source_id, output_id, provider_id, *refs}) == 5
    (supplier,) = sorted(tmp_path.glob("supplier-inline-*.bin"))
    provider_bytes = supplier.read_bytes()
    assert provider_bytes
    with Image.open(supplier) as generated:
        provider_dimensions = {"width": generated.width, "height": generated.height}
    assert browser["provider_dimensions"] == provider_dimensions
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task_id)
        )
        results = list(
            session.scalars(select(CanvasResult).where(CanvasResult.task_binding_id == binding.id))
        )
        assert task.status == record.status == "succeeded" and task.initiated_by == int(user["id"])
        assert record.call_no == 1 and len(results) == 1
        assert results[0].result_index == 0 and results[0].kind == "image"
        assert results[0].content_json["storageKey"] == browser["provider_storage_key"]
        assert str(results[0].media_id) == provider_id
        assert results[0].attachment_status == "detached"
        assert results[0].attachment_receipt_id is None and results[0].attached_at is None
        assert binding.node_key == browser["target_node_id"]
        assert binding.source_node_key == source["id"]
        assert binding.source_snapshot["source_node"] == source
        assert binding.source_snapshot["request"] == record.request_data
        assert record.config_snapshot["model_key"] == "agnes-image-2.5-flash"
        if not record.credential_cipher or secret in record.credential_cipher:
            raise AssertionError("Frozen emotion credential is missing or not encrypted")
        assert record.request_data["input"]["reference_media_ids"] == refs
        assert record.request_data["input"]["prompt"] == submitted["prompt"]
        assert "mask_media_id" not in record.request_data["canvas_parameters"]
        task_refs = list(
            session.scalars(
                select(CanvasTaskMediaReference)
                .where(CanvasTaskMediaReference.task_binding_id == binding.id)
                .order_by(CanvasTaskMediaReference.role, CanvasTaskMediaReference.ordinal)
            )
        )
        assert [(item.role, item.ordinal, str(item.media_id)) for item in task_refs] == [
            ("reference_media_ids", index, media_id) for index, media_id in enumerate(refs)
        ]
        asset = session.scalar(
            select(CanvasLibraryAsset).where(
                CanvasLibraryAsset.user_id == int(user["id"]),
                CanvasLibraryAsset.source_key == final_node["metadata"]["assetId"],
            )
        )
        assert asset.project_id == int(project["id"])
        assert asset.kind == "image" and asset.status == "confirmed"
        assert asset.payload_json["data"]["storageKey"] == browser["output_storage_key"]
        for key, expected in {
            "source": "canvas-generation",
            "canvasId": document["id"],
            "nodeId": browser["target_node_id"],
            "taskId": str(task_id),
            "resourceKey": browser["output_storage_key"],
        }.items():
            assert asset.payload_json["metadata"][key] == expected
        asset_refs = list(
            session.scalars(
                select(CanvasLibraryAssetReference).where(
                    CanvasLibraryAssetReference.library_asset_id == asset.id
                )
            )
        )
        assert len(asset_refs) == 1
        assert str(asset_refs[0].media_id) == output_id and asset_refs[0].binary_id is None
        frozen = {}
        for media_id in [source_id, output_id, provider_id, *refs]:
            media = session.get(MediaFile, int(media_id))
            assert media.project_id == int(project["id"]) and media.created_by == int(user["id"])
            assert (media.published_at is not None) == (media_id in {source_id, output_id})
            if media_id in refs:
                assert media.scope_user_id is None
                assert media.format_code == ("image/png" if media_id == refs[0] else "image/jpeg")
            if media_id == output_id:
                assert media.format_code == "image/png"
            location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
            with app.state.storage.open(location.bucket, location.object_name) as stream:
                payload = stream.read()
            assert media.checksum_sha256 == hashlib.sha256(payload).hexdigest()
            with Image.open(BytesIO(payload)) as decoded:
                assert (media.width, media.height) == decoded.size
            frozen[media_id] = payload
        assert frozen[source_id] == original_bytes and frozen[provider_id] == provider_bytes
        derived = frozen[output_id]
        frozen_refs = [frozen[value] for value in refs]
        for value in (record.config_snapshot, record.request_data, record.response_data):
            assert_no_secret(value, secret)
    assert request["image_references"] == [
        hashlib.sha256(value).hexdigest() for value in frozen_refs
    ]
    region = browser["emotion_edit"]["editRegion"]
    with Image.open(BytesIO(original_bytes)) as original_image:
        baseline = original_image.convert("RGBA")
    with Image.open(BytesIO(frozen_refs[0])) as crop_image:
        assert crop_image.format == "PNG"
        crop = crop_image.convert("RGBA")
    expected_crop = baseline.crop(
        (
            region["x"],
            region["y"],
            region["x"] + region["width"],
            region["y"] + region["height"],
        )
    )
    assert crop.size == expected_crop.size and crop.tobytes() == expected_crop.tobytes()
    with Image.open(BytesIO(frozen_refs[1])) as identity_image:
        assert identity_image.format == "JPEG" and identity_image.size == (384, 384)
    with Image.open(BytesIO(derived)) as composite_image:
        assert composite_image.format == "PNG"
        rendered = composite_image.convert("RGBA")
    assert rendered.size == baseline.size == (1024, 1024)
    face = browser["emotion_edit"]["faceBox"]
    left = max(0, math.floor(face["x"] - face["width"] * 0.25))
    right = min(1024, math.ceil(face["x"] + face["width"] * 1.25))
    top = max(0, math.floor(face["y"] - face["height"] * 0.3))
    bottom = min(1024, math.ceil(face["y"] + face["height"] * 1.25))
    allowed = (left, top, right, bottom)
    for box in (
        (0, 0, 1024, top),
        (0, bottom, 1024, 1024),
        (0, top, left, bottom),
        (right, top, 1024, bottom),
    ):
        assert rendered.crop(box).tobytes() == baseline.crop(box).tobytes()
    difference_bbox = ImageChops.difference(
        rendered.convert("RGB"), baseline.convert("RGB")
    ).getbbox()
    assert difference_bbox is not None
    assert (
        difference_bbox[0] >= left
        and difference_bbox[1] >= top
        and difference_bbox[2] <= right
        and difference_bbox[3] <= bottom
    )
    face_bounds = (
        round(face["x"]),
        round(face["y"]),
        round(face["x"] + face["width"]),
        round(face["y"] + face["height"]),
    )
    assert ImageChops.difference(
        rendered.crop(face_bounds).convert("RGB"), baseline.crop(face_bounds).convert("RGB")
    ).getbbox()
    assert derived != original_bytes and derived != provider_bytes
    assert hashlib.sha256(derived).hexdigest() == browser["output_sha256"]
    for media_id, expected in ((source_id, original_bytes), (output_id, derived)):
        media_path = f"{ROOT}/{media_id}/file"
        assert owner.get(media_path).content == member.get(media_path).content == expected
        assert outsider.get(media_path).status_code == 404
    for media_id in [provider_id, *refs]:
        media_path = f"{ROOT}/{media_id}/file"
        assert owner.get(media_path).content == frozen[media_id]
        assert member.get(media_path).status_code == outsider.get(media_path).status_code == 404
    assert owner.get(TASKS + "/" + str(task_id)).status_code == 200
    for other in (member, outsider):
        assert other.get(TASKS + "/" + str(task_id)).status_code == 404
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    current = owner.get(path + "/my-document").json()["source_document"]
    assert len(current["nodes"]) == 2 and len(current["connections"]) == 1
    assert next(item for item in current["nodes"] if item["id"] == source["id"]) == source
    current_result = next(
        item for item in current["nodes"] if item["id"] == browser["target_node_id"]
    )
    assert current_result["metadata"]["storageKey"] == browser["output_storage_key"]
    assert current_result["metadata"]["assetId"]
    assert current_result["metadata"]["generationEffectKeys"] == [browser["effect_key"]]
    shared = member.get(path).json()["source_document"]
    assert outsider.get(path).status_code == 404
    shared_result = next(
        item for item in shared["nodes"] if item["id"] == browser["target_node_id"]
    )
    assert shared_result["metadata"]["storageKey"] == browser["output_storage_key"]
    assert "taskId" not in shared_result["metadata"] and "model" not in shared_result["metadata"]
    for value in (provider, browser, stack, deliveries, shared):
        assert_no_secret(value, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    (tmp_path / "original-reference.png").write_bytes(original_bytes)
    (tmp_path / "supplier-original.png").write_bytes(provider_bytes)
    (tmp_path / "archived.png").write_bytes(derived)
    baseline.crop(allowed).save(tmp_path / "original-face-region.png")
    rendered.crop(allowed).save(tmp_path / "composite-face-region.png")
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scope": "original emotion UI with real Agnes and formal AMQP",
                "width": width,
                "model": "agnes-image-2.5-flash",
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "normal_action_count": normal_count,
                "python_postchecks": "passed",
                "allowed_changed_region": allowed,
                "actual_difference_bbox": difference_bbox,
                "content_verdict": "requires inspection of actual supplier and composite images",
                "content_requirements": [
                    "same anonymous person and recognizable facial identity",
                    "natural bright smile with visible teeth matching selected joy preset",
                    "smooth local blend without warped mouth, face or edit boundary",
                    "exact original pixels outside the selected face blend region",
                ],
                "shared_assistant_tools": "not exercised; pure chat remains unchanged",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
