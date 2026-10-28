"""原隐藏菜单、六种工作流媒体组合、类型字段与仅 cookies 恢复；供应商为受控 HTTP。"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, MediaAsset, MediaFile
from short_drama.service.generations.execution.execution import GenerationExecutionService
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, records
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.runninghub.test_canvas_runninghub_runtime import (
    CUSTOM,
    POINTS,
    TASK_ID,
    UPLOAD,
    WORKFLOW,
    creates,
    runninghub_seed,
)
from tests.integration.canvas.runninghub.test_canvas_runninghub_runtime import (
    runninghub_runtime as runninghub_runtime,
)
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original RunningHub UI/Python/MySQL/MinIO verification",
    ),
]

WORKFLOW_PARAMETERS = {
    "field:2:cfg": 7,
    "field:2:sampler_name": "dpmpp_2m",
    "field:2:quality": 0.55,
    "field:2:seed": 73,
    "random:2:seed": False,
    "field:3:watermark": True,
    "field:3:negative_prompt": "原界面保存的私人负向提示词",
}


@pytest.mark.parametrize(
    ("mode", "kind", "unknown"),
    [
        pytest.param("image", "workflow", False, id="image-workflow"),
        pytest.param("image", "app", False, id="image-app"),
        pytest.param("video", "workflow", False, id="video-workflow"),
        pytest.param("video", "app", False, id="video-app"),
        pytest.param("audio", "workflow", False, id="audio-workflow"),
        pytest.param("audio", "app", False, id="audio-app"),
        pytest.param("image", "workflow", True, id="unknown-create"),
    ],
)
def test_original_workflow_ui_saves_fields_and_restores_the_same_private_task(
    runninghub_runtime, tmp_path, mode, kind, unknown
):
    runtime = runninghub_runtime
    fields = [
        {
            "nodeId": "1",
            "fieldName": "text",
            "fieldType": "STRING",
            "source": "prompt",
            "bindPrompt": True,
        },
        {
            "nodeId": "2",
            "fieldName": "cfg",
            "fieldType": "NUMBER",
            "fieldValue": 5,
            "label": "引导系数",
            "min": 1,
            "max": 20,
            "step": 1,
        },
        {
            "nodeId": "2",
            "fieldName": "sampler_name",
            "fieldType": "SELECT",
            "fieldValue": "euler",
            "label": "采样器",
            "options": ["euler", "dpmpp_2m"],
        },
        {
            "nodeId": "2",
            "fieldName": "quality",
            "fieldType": "SLIDER",
            "fieldValue": 0.5,
            "label": "质量强度",
            "source": "",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        {
            "nodeId": "2",
            "fieldName": "seed",
            "fieldType": "INT",
            "fieldValue": 7,
            "label": "随机种子",
            "min": 0,
            "max": 1000,
            "step": 1,
            "randomEnabled": True,
        },
        {
            "nodeId": "3",
            "fieldName": "watermark",
            "fieldType": "BOOLEAN",
            "fieldValue": False,
            "label": "工作流水印",
            "source": "",
        },
        {
            "nodeId": "3",
            "fieldName": "negative_prompt",
            "fieldType": "STRING",
            "fieldValue": "原负向提示词",
            "label": "负向提示词",
            "source": "",
        },
    ]
    workflow = {
        "1": WORKFLOW["1"],
        "2": {
            "class_type": "KSampler",
            "inputs": {"cfg": 5, "seed": 7, "sampler_name": "euler", "quality": 0.5},
        },
        "3": {
            "class_type": "WorkflowOutputOptions",
            "inputs": {"watermark": False, "negative_prompt": "原负向提示词"},
        },
    }
    client, user, project, path, request = runninghub_seed(
        runtime,
        mode=mode,
        kind=kind,
        fields=fields,
        workflow=workflow if kind == "workflow" else {},
    )
    initial = client.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"] = [
        {
            "id": "saved-workflow-node",
            "type": "config",
            "title": "已有工作流节点",
            "position": {"x": 230, "y": 120},
            "width": 380,
            "height": 640,
            "metadata": {"generationMode": "image", "workflowProvider": "model", "status": "idle"},
        }
    ]
    document["connections"] = []
    committed = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "runninghub-browser-empty-" + uuid4().hex},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    if unknown:
        runtime.state["create"] = "disconnect"
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    assert (frontend.parent / "dist" / "canvas-app" / "index.html").is_file()
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "runninghub-original-release"
    stopped, worker_errors = threading.Event(), []
    executor = GenerationExecutionService(
        runtime.app[1], settings, runtime.gateway, runtime.app[0].state.storage
    )

    def run_worker():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(
                                AsyncTask.id, AsyncTask.message_version, AsyncTask.next_action
                            ).where(
                                AsyncTask.initiated_by == int(user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                            )
                        )
                    )
                for identifier, version, action in pending:
                    if action != "submit" and not release_path.exists():
                        continue
                    if stopped.is_set():
                        break
                    executor.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker = threading.Thread(target=run_worker, name="runninghub-test-driver", daemon=True)
        worker.start()
        config = {
            "apiUrl": api_url,
            "username": "runninghub_author",
            "password": PASSWORD,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": request["projectId"],
            "logicalModelId": request["logicalModelId"],
            "mode": mode,
            "kind": kind,
            "unknown": unknown,
            "workflowParameters": WORKFLOW_PARAMETERS,
            "releasePath": str(release_path),
            "outputDirectory": str(tmp_path / "browser"),
            "mediaHash": hashlib.sha256(runtime.media[mode]).hexdigest(),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-runninghub-python.mjs"],
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
            pytest.fail(f"RunningHub browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            worker.join(5)
        assert not worker.is_alive(), "Task-owned RunningHub driver did not stop"
        assert not worker_errors, worker_errors
        assert process.returncode == 0, f"RunningHub browser failed: {errors}\n{output}"
        report = json.loads(output)
    assert report["step"] == "complete"
    for field in (
        "original_hidden_menu_preserved",
        "dynamic_fields_edited",
        "invalid_parameters_blocked",
        "source_saved_before_admission",
        "closed_owner_before_completion",
        "same_task_recovered",
        "fresh_report_restored",
        "restored_parameters",
    ):
        assert report[field], field
    assert report["page_errors"] == [] and report["failed_routes"] == []
    assert len(report["task_posts"]) == len(creates(runtime)) == 1
    assert report["task_posts"][0]["logicalModelId"] == request["logicalModelId"]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    logged = client.post(
        "/api/v1/auth/login", json={"username": "runninghub_author", "password": PASSWORD}
    )
    assert logged.status_code == 200, logged.text
    client.headers["X-CSRF-Token"] = client.cookies["sd_csrf"]
    completed = client.get(f"{TASKS}/{report['task_id']}").json()
    saved = client.get(path + "/my-document").json()["source_document"]
    source = next(item for item in saved["nodes"] if item["id"] == report["source_node_id"])
    assert source["type"] == "config"
    assert source["metadata"]["runningHubModelId"] == request["logicalModelId"]
    assert source["metadata"]["workflowParameters"] == WORKFLOW_PARAMETERS
    assert source["metadata"]["composerContent"] == report["task_posts"][0]["prompt"]
    info = creates(runtime)[0]["body"]["nodeInfoList"]
    assert next(item["fieldValue"] for item in info if item["fieldName"] == "cfg") == "7"
    assert (
        next(item["fieldValue"] for item in info if item["fieldName"] == "text")
        == (source["metadata"]["composerContent"])
    )
    submitted = {(item["nodeId"], item["fieldName"]): item["fieldValue"] for item in info}
    assert submitted == {
        ("1", "text"): source["metadata"]["composerContent"],
        ("2", "cfg"): "7",
        ("2", "sampler_name"): "dpmpp_2m",
        ("2", "quality"): "0.55",
        ("2", "seed"): "73",
        ("3", "watermark"): "true",
        ("3", "negative_prompt"): WORKFLOW_PARAMETERS["field:3:negative_prompt"],
    }
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert record.adapter == f"canvas_runninghub_workflow_{mode}.v1"
        public = json.dumps([record.request_data, record.config_snapshot, record.response_data])
        assert all(secret not in public for secret in (POINTS, UPLOAD, CUSTOM))
        file = session.scalar(select(MediaFile))
        if unknown:
            assert record.status == "unknown" and file is None
        else:
            assert record.status == "succeeded" and record.provider_task_id == TASK_ID
            assert file.checksum_sha256 == config["mediaHash"]
            assert file.storage_locator.startswith("minio://canvas-test-")
            location = ObjectLocation.parse(
                file.storage_locator, {getattr(settings, f"minio_{mode}_bucket")}
            )
            with runtime.app[0].state.storage.open(
                location.bucket, location.object_name
            ) as content:
                assert content.read() == runtime.media[mode]
            asset = session.scalar(select(MediaAsset))
            assert asset.media_type == mode
    member, member_user = account(runtime.app, "runninghub_browser_member")
    outsider, _ = account(runtime.app, "runninghub_browser_outsider")
    for reader in (member, outsider):
        assert reader.get(f"{TASKS}/{report['task_id']}").status_code == 404
        assert reader.get(f"{TASKS}/{report['task_id']}/logs").status_code == 404
    if unknown:
        assert report["unknown_retry_unavailable"] and completed["status"] == "failed"
        assert not completed["canRetry"] and not completed["canResume"]
        return
    assert completed["status"] == "succeeded"
    result = json.loads(completed["resultJson"])
    media = result["images"][0] if mode == "image" else result[mode]
    assert client.get(media["url"]).content == runtime.media[mode]
    assert member.get(media["url"]).status_code == 404
    assert outsider.get(media["url"]).status_code == 404
    target = next(item for item in saved["nodes"] if item["id"] == report["node_id"])
    assert target["type"] == mode and target["metadata"]["storageKey"] == media["storageKey"]
