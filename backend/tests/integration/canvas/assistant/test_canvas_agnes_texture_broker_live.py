"""显式授权：原独立肤质UI、真实Agnes图片、正式八角色/AMQP与媒体恢复。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.core.crypto import KeyCipher
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaFile,
)
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.publisher import RabbitSender
from tests.integration.canvas.assistant.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import eventually
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    records,
)
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.assistant.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    official_stack,
    read_json,
)
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
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
def test_original_independent_texture_real_agnes_through_formal_generation_worker(
    resource_app, tmp_path, monkeypatch, width
):
    app, factory, current_settings = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    assert not os.environ.get("AGNES_ASSISTANT_BROKER_LIFECYCLE_MODE")
    reference_path = Path(os.environ.get("AGNES_TEXTURE_REFERENCE_IMAGE", ""))
    assert reference_path.is_file(), (
        "Archived anonymous portrait required; prior lighting content failure remains failed"
    )
    original_bytes = reference_path.read_bytes()
    source_hash = hashlib.sha256(original_bytes).hexdigest()
    assert source_hash == "29b3fde68f828e41b0011e75176f3b3a063157401315bfba6b35c750efe12fdb"
    with Image.open(reference_path) as reference:
        assert reference.format == "PNG" and reference.size == (1024, 1024)
    app.state.settings = current_settings.model_copy(update={"agent_enabled": True})
    owner, user = account(resource_app, "at_o_" + str(width))
    foreign, foreign_user = account(resource_app, "at_m_" + str(width))
    outsider, _ = account(resource_app, "at_x_" + str(width))
    project, path, document = canvas(owner, "agnes-texture-" + uuid4().hex)
    document["nodes"] = []
    document["connections"] = []
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-texture-empty"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200

    secret = live_key()
    created = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes独立肤质真实验收",
            "service_type": "image",
            "provider": "Agnes",
            "model_key": "agnes-image-2.5-flash",
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": "openai-image"},
        },
    )
    if created.status_code != 201:
        pytest.fail("Saved live texture model failed: HTTP " + str(created.status_code))
    model = created.json()
    assert_no_secret(model, secret)
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
                "size": "1:1",
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
        assert saved_model.apikey != secret
        if (
            KeyCipher(current_settings.encryption_key.get_secret_value()).decrypt(
                saved_model.apikey
            )
            != secret
        ):
            raise AssertionError("Saved texture credential differs from the authorized key")
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
        "foreignUsername": foreign_user["username"],
        "password": PASSWORD,
        "image": base64.b64encode(original_bytes).decode(),
        "sourceSha256": source_hash,
        "sourceDimensions": {"width": 1024, "height": 1024},
        "outputDimensions": {"width": 1024, "height": 1024},
        "scenario": "recovery",
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
        generation_observer_module="tests.support.integration.agnes_generation_broker_observer",
    ) as (api_url, stack, workers):
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-agnes-texture-python.mjs"],
            cwd=Path(__file__).resolve().parents[5] / "frontend" / "canvas",
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
            pytest.exit("Live texture timed out; reconcile original task before another POST", 1)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        if process.returncode:
            pytest.exit("Live texture requires reconciliation: " + errors + "\n" + output, 1)
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
    assert request["options"]["size"] == "1K" and request["options"]["ratio"] == "1:1"
    assert request["image_references"] == [source_hash]
    assert all(stack["cleanup"].values())
    assert len(stack["roles"]) == len({row["pid"] for row in stack["roles"].values()}) == 8
    assert browser["texture_controls_hidden_like_source"]
    if width >= 640:
        assert browser["desktop_creation_hidden_like_source"]
    assert browser["idle_fresh_context_restored"] and browser["pending_fresh_context_restored"]
    assert browser["final_fresh_context_restored"]
    assert browser["uploads"] == len(browser["submissions"]) == 1
    assert records(resource_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert [row["phase"] for row in browser["source_views"]] == [
        "initial",
        "idle-fresh",
        "running-fresh",
        "terminal-fresh",
    ]
    assert all(row["sha256"] == source_hash for row in browser["source_views"])
    assert browser["output_dimensions"] == {"width": 1024, "height": 1024}
    submitted = browser["submissions"][0]
    assert submitted["prompt"] == browser["expected_prompt"]
    assert submitted["logicalModelId"] == model["id"]
    assert submitted["model"] == selection
    source_id = browser["source_node"]["metadata"]["storageKey"].removeprefix("resource:")
    output_id = browser["output_storage_key"].removeprefix("resource:")
    (supplier,) = sorted(tmp_path.glob("supplier-inline-*.bin"))
    output_bytes = supplier.read_bytes()
    assert output_bytes and output_bytes != original_bytes
    assert hashlib.sha256(output_bytes).hexdigest() == browser["output_sha256"]
    with Image.open(supplier) as generated:
        assert generated.size == (1024, 1024)
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
        assert len(results) == 1 and results[0].attachment_status == "attached"
        assert str(results[0].media_id) == output_id and record.call_no == 1
        assert binding.node_key == browser["target_node_id"]
        assert binding.source_node_key == browser["idle_node_id"]
        assert record.config_snapshot["model_key"] == "agnes-image-2.5-flash"
        assert record.credential_cipher and secret not in record.credential_cipher
        assert record.request_data["input"]["reference_media_ids"] == [source_id]
        assert record.request_data["input"]["prompt"] == submitted["prompt"]
        assert "mask_media_id" not in record.request_data["canvas_parameters"]
        for media_id, expected in ((source_id, original_bytes), (output_id, output_bytes)):
            media = session.get(MediaFile, int(media_id))
            assert media.project_id == int(project["id"]) and media.published_at is not None
            assert media.checksum_sha256 == hashlib.sha256(expected).hexdigest()
            assert media.width == media.height == 1024
            location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
            with app.state.storage.open(location.bucket, location.object_name) as stream:
                assert stream.read() == expected
        for value in (record.config_snapshot, record.request_data, record.response_data):
            assert_no_secret(value, secret)
    for media_id, expected in ((source_id, original_bytes), (output_id, output_bytes)):
        media_path = f"{ROOT}/{media_id}/file"
        assert owner.get(media_path).content == expected
        assert foreign.get(media_path).status_code == 404
        assert outsider.get(media_path).status_code == 404
    assert owner.get(TASKS + "/" + str(task_id)).status_code == 200
    for other in (foreign, outsider):
        assert other.get(TASKS + "/" + str(task_id)).status_code == 404
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    current = owner.get(path + "/my-document").json()["source_document"]
    assert len(current["nodes"]) == 2 and len(current["connections"]) == 1
    assert (
        next(node for node in current["nodes"] if node["id"] == browser["source_node"]["id"])
        == (browser["source_node"])
    )
    assert foreign.get(path).status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert outsider.get(path).status_code == 404
    shared_result = next(
        node for node in shared["nodes"] if node["id"] == browser["target_node_id"]
    )
    assert shared_result["metadata"]["storageKey"] == browser["output_storage_key"]
    assert "taskId" not in shared_result["metadata"] and "model" not in shared_result["metadata"]
    for value in (provider, browser, stack, deliveries, shared):
        assert_no_secret(value, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    (tmp_path / "original-reference.png").write_bytes(original_bytes)
    (tmp_path / "archived.png").write_bytes(output_bytes)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scope": "original independent texture UI with real Agnes and formal AMQP",
                "width": width,
                "model": "agnes-image-2.5-flash",
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "normal_action_count": normal_count,
                "python_postchecks": "passed",
                "shared_assistant_tools": "not exercised; pure chat remains unchanged",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
