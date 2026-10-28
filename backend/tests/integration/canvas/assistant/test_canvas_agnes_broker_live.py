"""显式授权：原画布、正式 API/Scheduler/生成 Worker/AMQP 与真实 Agnes。"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
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
from short_drama.tasks.runtime.publisher import RabbitSender
from tests.integration.canvas.assistant.test_canvas_agnes_live import (
    MODELS,
    PROMPTS,
    PROTOCOLS,
    assert_no_secret,
    live_key,
)
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import eventually
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, WORKSPACE
from tests.integration.canvas.resources.test_canvas_resources import canvas, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)
from tests.support.integration.official_stack import (
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
        reason="Explicit live Agnes, real AMQP, isolated storage and browser opt-in are required",
    ),
]


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("scenario", ["text", "image", "image-reference", "video"])
def test_original_canvas_real_agnes_through_formal_generation_worker(
    resource_app, tmp_path, monkeypatch, scenario, width
):
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    kind = "image" if scenario == "image-reference" else scenario
    owner, owner_user = account(resource_app, f"ag_o_{scenario.replace('-', '')}_{width}")
    foreign, foreign_user = account(resource_app, f"ag_m_{scenario.replace('-', '')}_{width}")
    outsider, _ = account(resource_app, f"ag_x_{scenario.replace('-', '')}_{width}")
    project, path, document = canvas(owner, "agnes-" + uuid4().hex)

    secret = live_key()
    profile = {"version": 1, "api_format": "openai", "protocol": PROTOCOLS[kind]}
    if kind == "video":
        profile["capability_config"] = {
            "version": 1,
            "video": {
                "references": {
                    "promptMaxChars": 8000,
                    "minImages": 0,
                    "maxImages": 5,
                    "maxImageBytes": 30 * 1024**2,
                    "maxVideos": 0,
                    "maxVideoBytes": 0,
                    "maxVideoDurationSeconds": 0,
                    "maxAudios": 3,
                    "maxAudioBytes": 50 * 1024**2,
                    "maxAudioDurationSeconds": 0,
                },
                "duration": {"selection": "range", "min": 4, "max": 12, "step": 1, "default": 4},
                "durationSupported": True,
                "ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                "defaultRatio": "16:9",
                "resolutions": ["720p"],
                "defaultResolution": "720p",
                "generateAudio": {"supported": False, "default": False},
                "watermark": {"supported": False, "default": False},
                "operations": [
                    "text_to_video",
                    "image_to_video",
                    "reference_to_video",
                    "audio_to_video",
                ],
                "defaultOperation": "text_to_video",
            },
        }
    response = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes真实验收-" + kind,
            "service_type": kind,
            "provider": "Agnes",
            "model_key": MODELS[kind],
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": profile,
        },
    )
    if response.status_code != 201:
        pytest.fail("Saved real model configuration failed: HTTP " + str(response.status_code))
    model = response.json()
    assert_no_secret(model, secret)
    selection = f"host-{model['id']}::{model['model_key']}"
    workspace = owner.get(WORKSPACE).json()
    response = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "model": selection,
                f"{kind}Model": selection,
                "size": "16:9" if kind == "video" else "1:1",
                "quality": "auto",
                "count": "1",
                "videoSeconds": "4",
                "vquality": "720p",
                "videoGenerateAudio": "false",
            },
        },
    )
    assert response.status_code == 200
    assert_no_secret(response.json(), secret)
    document["nodes"] = [
        {
            "id": "runtime-node",
            "type": kind,
            "title": "Agnes真实验收",
            "position": {"x": 420, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"prompt": "验收前保存的来源", "content": "", "manualSize": True},
        }
    ]
    reference = None
    reference_body = None
    if kind == "video":
        document["nodes"][0]["metadata"].update(
            seconds="4", vquality="720p", size="16:9", generateAudio="false", watermark="false"
        )
    if scenario == "image-reference":
        reference_path = Path(".runtime/agnes-live-probes/image-a/generated-image.bin")
        if not reference_path.is_file():
            pytest.fail("The real supplier reference image is missing")
        reference_body = reference_path.read_bytes()
        response = upload(
            owner,
            reference_body,
            source=document["id"],
            key="agnes-real-reference",
            name="agnes-reference.png",
        )
        assert response.status_code == 200
        reference = response.json()["resource"]
        document["nodes"].insert(
            0,
            {
                "id": "agnes-reference",
                "type": "image",
                "title": "真实供应商原参考图",
                "position": {"x": 0, "y": 0},
                "width": 320,
                "height": 320,
                "metadata": {
                    "storageKey": "resource:" + reference["id"],
                    "mimeType": "image/png",
                    "content": f"/api/v1/canvas-runtime/resources/{reference['id']}/file",
                    "status": "success",
                    "naturalWidth": reference["width"],
                    "naturalHeight": reference["height"],
                    "bytes": reference["size"],
                    "nodeRole": "result",
                    "resultOrigin": "upload",
                    "manualSize": True,
                },
            },
        )
        document["connections"] = [
            {
                "id": "agnes-reference-edge",
                "fromNodeId": "agnes-reference",
                "toNodeId": "runtime-node",
            }
        ]
    response = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-source"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert response.status_code == 200
    document = owner.get(path + "/my-document").json()["source_document"]
    factory = resource_app[1]
    with factory() as session:
        saved_model = session.get(AIModelConfig, int(model["id"]))
        assert saved_model.apikey != secret
        if (
            KeyCipher(resource_app[2].encryption_key.get_secret_value()).decrypt(saved_model.apikey)
            != secret
        ):
            raise AssertionError("Saved model credential does not decrypt to the authorized key")
    settings = resource_app[2].model_copy(
        update={
            "public_origin": "http://127.0.0.1:4193",
            "generation_text_budget_seconds": 120,
            "generation_image_budget_seconds": 180,
            "generation_video_budget_seconds": 900,
            "agent_enabled": True,
            "generation_queue_namespace": "assistant_broker_live_" + uuid4().hex,
            "smtp_host": "",
            "smtp_from": "",
            "render_scratch_root": str(tmp_path / "render-scratch"),
        }
    )
    release = tmp_path / "real-provider-submit-allowed"
    monkeypatch.setenv("AGNES_GENERATION_BROKER_DIRECTORY", str(tmp_path))
    config = {
        "username": owner_user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "canvasId": project["primary_canvas_id"],
        "sourceKey": document["id"],
        "kind": kind,
        "scenario": scenario,
        "width": width,
        "prompt": PROMPTS[scenario],
        "model": MODELS[kind],
        "modelId": model["id"],
        "releasePath": str(release),
        "outputDirectory": str(tmp_path / "browser"),
    }
    if reference:
        config["reference"] = {
            "sha256": hashlib.sha256(reference_body).hexdigest(),
            "width": reference["width"],
            "height": reference["height"],
        }
    with official_stack(
        factory,
        settings,
        tmp_path,
        generation_observer_module="tests.support.integration.agnes_generation_broker_observer",
    ) as (api_url, stack, processes):
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-agnes-live-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=1020)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            process.communicate(timeout=10)
            pytest.exit(
                "Formal generation timed out; reconcile original identity before another POST", 1
            )
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        provider = read_json(tmp_path / "provider-report.json")
        assert provider is not None
        if process.returncode:
            error = provider.get("error", {})
            if error.get("accepted_unknown") is True or not error:
                pytest.exit(
                    "Formal generation requires reconciliation: " + errors + "\n" + output, 1
                )
            pytest.fail("Formal live generation failed: " + errors + "\n" + output)
        browser = json.loads(output)
        assert browser["step"] == "complete"
        task_id = int(browser["task_id"])
        deliveries = eventually(
            lambda: read_json(tmp_path / "generation-worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) >= 1
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert processes[kind].pid in {deliveries["pid"], deliveries["parent_pid"]}
        stack["roles"][kind]["execution_pid"] = deliveries["pid"]
        assert provider["worker_pid"] == deliveries["pid"]
        assert provider["snowflake_worker_id"] == ROLE_IDS[kind]
        normal_deliveries = deliveries["deliveries"]
        normal_count = len(normal_deliveries)
        assert normal_count >= {"text": 1, "image": 2, "video": 3}[kind]
        for row in normal_deliveries:
            assert int(row["task_id"]) == task_id and row["state"] == "SUCCESS"
            assert row["delivery"]["exchange"] == settings.generation_queue_namespace + ".tasks"
            assert row["delivery"]["routing_key"] == kind
            assert row["delivery"]["redelivered"] is False
        with factory() as session:
            task = session.get(AsyncTask, task_id)
            assert [int(row["message_version"]) for row in normal_deliveries] == list(
                range(1, task.message_version)
            )
        sender = RabbitSender(settings)
        try:
            version = int(normal_deliveries[0]["message_version"])
            sender(task_id, version, kind)
            sender(task_id, version - 1, kind)
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
        assert all(worker.poll() is None for worker in processes.values())
    provider = read_json(tmp_path / "provider-report.json")
    assert provider["submissions"] == 1
    assert all(stack["cleanup"].values())
    # 仅把真实子进程的归档报告和原字节包装为只读观测，供既有后置合同核对。
    gateway = SimpleNamespace(
        report=provider,
        inline_outputs=[
            path.read_bytes() for path in sorted(tmp_path.glob("supplier-inline-*.bin"))
        ],
        transport=SimpleNamespace(
            media=[path.read_bytes() for path in sorted(tmp_path.glob("supplier-media-*.bin"))]
        ),
    )
    assert browser["step"] == "complete"
    assert browser["task_posts"] == gateway.report["submissions"] == 1
    assert not browser["page_errors"] and not browser["http_errors"]
    task_id = int(browser["task_id"])
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task_id)
        )
        results = list(
            session.scalars(select(CanvasResult).where(CanvasResult.task_binding_id == binding.id))
        )
        assert task.status == call.status == "succeeded"
        assert task.initiated_by == int(owner_user["id"])
        assert call.config_snapshot["model_key"] == MODELS[kind]
        assert call.credential_cipher and secret not in call.credential_cipher
        assert_no_secret(call.config_snapshot, secret)
        assert_no_secret(call.request_data, secret)
        assert_no_secret(call.response_data, secret)
        assert len(results) == 1 and results[0].attachment_status == "attached"
        assert call.call_no == 1
        if kind == "text":
            assert "红色礼盒" in call.text_content and call.text_content == browser["text"]
            assert len(call.text_content.strip()) <= 30
            assert browser["text"] == gateway.report["results"][0]["text"]
        else:
            media = session.get(MediaFile, results[0].media_id)
            location = ObjectLocation.parse(
                media.storage_locator, {getattr(settings, f"minio_{kind}_bucket")}
            )
            with resource_app[0].state.storage.open(
                location.bucket, location.object_name
            ) as stream:
                actual = stream.read()
            originals = gateway.inline_outputs or gateway.transport.media
            assert actual in originals
            assert media.checksum_sha256 == hashlib.sha256(actual).hexdigest() == browser["sha256"]
            assert media.width > 0 and media.height > 0
            (tmp_path / ("archived.png" if kind == "image" else "archived.mp4")).write_bytes(actual)
            if kind == "video":
                created = next(
                    item
                    for item in gateway.report["requests"]
                    if item["method"] == "POST" and item["path"] == "/v1/videos"
                )
                assert call.provider_task_id == created["video_id"]
                assert any(item["path"] == "/agnesapi" for item in gateway.report["requests"])
                assert 3800 <= media.duration_ms <= 4500
    own = owner.get(TASKS + "/" + str(task_id))
    assert own.status_code == 200
    assert foreign.get(TASKS + "/" + str(task_id)).status_code == 404
    assert outsider.get(TASKS + "/" + str(task_id)).status_code == 404
    for other in (foreign, outsider):
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    assert foreign.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert_no_secret(shared, secret)
    attached = next(node for node in shared["nodes"] if node["id"] == browser["node_id"])
    assert "taskId" not in attached["metadata"] and "prompt" not in attached["metadata"]
    if kind == "text":
        assert attached["metadata"]["content"] == browser["text"]
    else:
        resource_path = "/api/v1/canvas-runtime/resources/" + str(results[0].media_id) + "/file"
        assert owner.get(resource_path).content == actual
        assert foreign.get(resource_path).status_code == 404
        assert outsider.get(resource_path).status_code == 404
    if reference:
        assert len(browser["reference_views"]) == 3
        assert all(item == config["reference"] for item in browser["reference_views"])
        original = next(node for node in shared["nodes"] if node["id"] == "agnes-reference")
        assert original["metadata"]["storageKey"] == "resource:" + reference["id"]
        assert (
            owner.get(f"/api/v1/canvas-runtime/resources/{reference['id']}/file").content
            == reference_body
        )
        supplier = next(
            item for item in gateway.report["requests"] if item["path"] == "/v1/images/generations"
        )
        assert supplier["image_references"] == [hashlib.sha256(reference_body).hexdigest()]
    assert_no_secret(browser, secret)
    assert_no_secret(gateway.report, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "width": width,
                "model": MODELS[kind],
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "normal_action_count": normal_count,
                "python_postchecks": "passed",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
