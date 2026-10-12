"""固定源内置 RunningHub 经受控 HTTP、隔离 MySQL 与 MinIO 的真实调用链。"""

import json
import os
import socket
import threading
from copy import deepcopy
from datetime import timedelta
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasLibraryAsset,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskMediaReference,
    MediaAsset,
    MediaFile,
)
from short_drama.service.base import utcnow
from tests.integration.test_canvas_audio_protocols import execute
from tests.integration.test_canvas_generation_media_runtime import playable_fixture
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, seed
from tests.integration.test_canvas_model_tests import TESTS, create
from tests.integration.test_canvas_model_tests import request as model_test_request
from tests.integration.test_canvas_resources import png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated RunningHub HTTP/MySQL/MinIO verification",
    ),
]
POINTS = "runninghub-fixture-points-key"
UPLOAD = "runninghub-fixture-enterprise-upload-key"
CUSTOM = "runninghub-fixture-private-header"
TASK_ID = "runninghub-original-task-17"
PLUGIN = "/api/v1/canvas-runtime/plugins/runninghub-workflow-provider/activation"
WORKFLOW = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "原提示词"}},
    "2": {"class_type": "KSampler", "inputs": {"cfg": 5, "seed": 7}},
}


@pytest.fixture
def runninghub_runtime(resource_app, tmp_path):
    app, factory, current = resource_app
    media = {
        "image": png("blue"),
        "video": playable_fixture(tmp_path, "video"),
        "audio": playable_fixture(tmp_path, "audio"),
        "unsupported": b"<html>provider diagnostic</html>",
        "empty": b"",
    }
    mimes = {
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "unsupported": "text/html",
        "empty": "image/png",
    }
    calls, errors = [], []
    state = {
        "mode": "image",
        "kind": "workflow",
        "create": "accepted",
        "poll": "success",
        "schema": deepcopy(WORKFLOW),
        "schema_code": 0,
        "schema_status": 200,
        "pending": 0,
        "upload_status": 200,
    }

    class Supplier(BaseHTTPRequestHandler):
        def handle_request(self):
            try:
                assert self.headers.get("Authorization") is None
                if self.command == "GET":
                    assert self.headers.get("X-RunningHub-Fixture") is None
                    index = int(self.path.rsplit("/", 1)[1])
                    kind = state.get("output_kinds", [state["mode"]])[index]
                    calls.append({"method": "GET", "path": self.path, "kind": kind})
                    sequence = state.get("download_sequence")
                    if sequence and (failure := sequence.pop(0)) is not None:
                        return self.respond({}, status=failure)
                    if state.get("download_failure"):
                        return self.respond({}, status=503)
                    return self.respond(media[kind], mime=mimes[kind])
                assert self.command == "POST"
                assert self.headers.get("X-RunningHub-Fixture") == CUSTOM
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/task/openapi/upload":
                    message = BytesParser(policy=default).parsebytes(
                        ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode()
                        + raw
                    )
                    parts = {
                        part.get_param("name", header="content-disposition"): part.get_payload(
                            decode=True
                        )
                        for part in message.iter_parts()
                    }
                    assert parts["apiKey"].decode() == UPLOAD and parts["fileType"] == b"input"
                    calls.append(
                        {"method": "POST", "path": self.path, "upload_bytes": parts["file"]}
                    )
                    return self.respond(
                        {"code": 0, "data": {"fileName": f"input/ref-{len(calls)}.media"}},
                        status=state["upload_status"],
                    )
                body = json.loads(raw)
                assert body["apiKey"] == POINTS
                calls.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "body": {key: value for key, value in body.items() if key != "apiKey"},
                    }
                )
                if self.path == "/api/openapi/getJsonApiFormat":
                    assert body["workflowId"] == "workflow-17"
                    return self.respond(
                        {"code": state["schema_code"], "data": {"prompt": state["schema"]}},
                        status=state["schema_status"],
                    )
                if self.path == "/api/webapp/apiCallDemo":
                    assert body["webappId"] == "app-17"
                    return self.respond(
                        {
                            "code": state["schema_code"],
                            "data": {"nodeInfoList": state.get("app_fields", [])},
                        },
                        status=state["schema_status"],
                    )
                if self.path in {"/task/openapi/create", "/task/openapi/ai-app/run"}:
                    assert self.path.endswith("ai-app/run" if state["kind"] == "app" else "create")
                    assert body["webappId" if state["kind"] == "app" else "workflowId"] == (
                        "app-17" if state["kind"] == "app" else "workflow-17"
                    )
                    if state["create"] == "disconnect":
                        return self.disconnect()
                    if state["create"] == "missing-id":
                        return self.respond({"code": 0})
                    if state["create"] == "rejected":
                        return self.respond({"code": 805, "message": CUSTOM})
                    return self.respond({"data": {"taskId": TASK_ID}})
                assert self.path == "/task/openapi/outputs" and body["taskId"] == TASK_ID
                if state["pending"]:
                    state["pending"] -= 1
                    return self.respond({"code": 0, "data": {"status": "running"}})
                if type(state["poll"]) is int:
                    return self.respond({}, status=state["poll"])
                if state["poll"] == "disconnect":
                    return self.disconnect()
                if state["poll"] == "malformed":
                    return self.respond(b"invalid-json")
                if state["poll"] == "failed":
                    return self.respond({"code": 805, "message": CUSTOM})
                kinds = state.get("output_kinds", [state["mode"]])
                return self.respond(
                    {
                        "code": 0,
                        "data": [
                            {"fileUrl": state["origin"] + f"/media/{index}"}
                            for index in range(len(kinds))
                        ],
                    }
                )
            except Exception as error:
                errors.append(str(error))
                self.respond({}, status=500)

        def respond(self, value, status=200, mime="application/json"):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()

        def disconnect(self):
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    settings = current.model_copy(
        update={
            "generation_allowed_hosts": ["127.0.0.1"],
            "model_discovery_allowed_hosts": ["127.0.0.1"],
        }
    )
    app.state.settings = settings
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            app=(app, factory, settings),
            state=state,
            calls=calls,
            media=media,
            gateway=GenerationGateway(settings),
        )
        assert not errors, errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def runninghub_seed(runtime, *, mode="image", kind="workflow", fields=None, workflow=None):
    runtime.state.update(mode=mode, kind=kind)
    client, user, project, path, request = seed(runtime.app, "runninghub_author", kind=mode)
    saved = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    descriptor = {
        "kind": kind,
        "workflow_id": "workflow-17" if kind == "workflow" else "",
        "webapp_id": "app-17" if kind == "app" else "",
        "title": "已保存工作流",
        "workflow_json": WORKFLOW if workflow is None else workflow,
        "fields": fields or [],
    }
    changed = client.patch(
        "/api/v1/ai-model-configs/" + saved["id"],
        json={
            "row_version": saved["row_version"],
            "provider": "runninghub",
            "model_key": "workflow-17",
            "base_url": runtime.state["origin"] + "/openapi/v2",
            "apikey": POINTS,
            "secret_key": UPLOAD,
            "headers": [{"name": "X-RunningHub-Fixture", "value": CUSTOM}],
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": f"runninghub-workflow-{mode}",
                "runninghub_workflow": descriptor,
            },
        },
    )
    assert changed.status_code == 200, changed.text
    assert client.put(PLUGIN, json={"enabled": True}).status_code == 200
    request.update(provider="runninghub", model="RunningHub · 已保存工作流")
    request["input"]["execution"] = {
        "provider": "runninghub",
        "kind": kind,
        "interfaceType": f"runninghub-workflow-{mode}",
        "workflowId": descriptor["workflow_id"],
        "webappId": descriptor["webapp_id"],
    }
    request["input"]["metadata"]["workflowParameters"] = {"field:2:cfg": 7}
    return client, user, project, path, request


def creates(runtime):
    return [
        call
        for call in runtime.calls
        if call["path"] in {"/task/openapi/create", "/task/openapi/ai-app/run"}
    ]


@pytest.mark.parametrize("mode", ["image", "video", "audio"])
@pytest.mark.parametrize("kind", ["workflow", "app"])
def test_builtin_roundtrip_freezes_keys_fields_and_original_identity(
    runninghub_runtime, mode, kind
):
    runtime = runninghub_runtime
    client, user, project, path, request = runninghub_seed(runtime, mode=mode, kind=kind)
    member, other = account(runtime.app, "runninghub_member")
    join(runtime.app, client, member, project["id"], other["id"])
    stranger, _ = account(runtime.app, "runninghub_stranger")
    task = admit(client, request)
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    saved = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    update = client.patch(
        "/api/v1/ai-model-configs/" + saved["id"],
        json={
            "row_version": saved["row_version"],
            "apikey": "changed-private-key",
            "runtime_profile": {
                **saved["runtime_profile"],
                "runninghub_workflow": {"workflow_id": "changed-workflow"},
            },
        },
    )
    assert update.status_code == 200, update.text
    assert client.put(PLUGIN, json={"enabled": False}).status_code == 200
    _, actions = execute(runtime, task)
    assert actions == ["submit", "poll", "save"]
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["status"] == "succeeded" and completed["providerRequestId"] == TASK_ID
    result = json.loads(completed["resultJson"])
    item = result["images"][0] if mode == "image" else result[mode]
    assert result["mode"] == mode and client.get(item["url"]).content == runtime.media[mode]
    assert member.get(item["url"]).status_code == stranger.get(item["url"]).status_code == 404
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    bound = bind(client, request, completed)
    assert bound["result"]["node"]["metadata"]["storageKey"] == item["storageKey"]
    assert member.get(item["url"]).content == runtime.media[mode]
    fresh = TestClient(runtime.app[0])
    fresh.cookies.update(client.cookies)
    assert fresh.get(f"{TASKS}/{task['id']}").json()["resultJson"] == completed["resultJson"]
    assert len(creates(runtime)) == 1
    info = creates(runtime)[0]["body"]["nodeInfoList"]
    assert next(value["fieldValue"] for value in info if value["fieldName"] == "cfg") == "7"
    assert (
        next(value["fieldValue"] for value in info if value["fieldName"] == "text")
        == request["prompt"]
    )
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.provider_task_id == TASK_ID
        public = json.dumps([record.request_data, record.config_snapshot, record.response_data])
        assert all(secret not in public for secret in (POINTS, UPLOAD, CUSTOM))
        asset = session.scalar(select(MediaAsset).where(MediaAsset.record_id == record.id))
        file = session.get(MediaFile, asset.media_id)
        assert asset.media_type == mode and file.storage_locator.startswith("minio://")


@pytest.mark.parametrize("kind", ["workflow", "app"])
def test_saved_schema_fetch_is_private_versioned_nonpaid_and_public(runninghub_runtime, kind):
    runtime = runninghub_runtime
    runtime.state["app_fields"] = [
        {"nodeId": "1", "fieldName": "prompt", "fieldValue": "old"},
        {"nodeId": "2", "fieldName": "cfg", "fieldValue": 5},
    ]
    client, _, _, _, request = runninghub_seed(runtime, kind=kind)
    saved = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    route = "/api/v1/canvas-runtime/ai/models/" + saved["id"] + "/runninghub-schema"
    response = client.post(route, json={"row_version": saved["row_version"]})
    assert response.status_code == 200, response.text
    descriptor = response.json()
    assert descriptor["kind"] == kind and descriptor["fields"]
    assert all(secret not in response.text for secret in (POINTS, UPLOAD, CUSTOM))
    assert not creates(runtime)
    assert client.post(route, json={"row_version": "0"}).status_code == 409
    other, _ = account(runtime.app, "runninghub_schema_other")
    assert other.post(route, json={"row_version": saved["row_version"]}).status_code == 404
    assert (
        client.post(
            route, json={"row_version": saved["row_version"], "apiKey": "forged"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("case", ["empty", "payload-code", "http-auth", "invalid-json"])
def test_settings_schema_empty_and_error_contract(runninghub_runtime, case):
    runtime = runninghub_runtime
    client, _, _, _, request = runninghub_seed(runtime)
    saved = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    if case == "empty":
        runtime.state["schema"] = ""
    elif case == "payload-code":
        runtime.state["schema_code"] = 805
    elif case == "http-auth":
        runtime.state["schema_status"] = 401
    else:
        runtime.state["schema"] = "invalid-json"
    response = client.post(
        "/api/v1/canvas-runtime/ai/models/" + saved["id"] + "/runninghub-schema",
        json={"row_version": saved["row_version"]},
    )
    assert response.status_code == (200 if case in {"empty", "payload-code"} else 502), (
        response.text
    )
    if case == "empty":
        assert response.json()["fields"] == [] and response.json()["workflow_json"] == {}
    assert not creates(runtime)


@pytest.mark.parametrize("case", ["disconnect", "missing-id"])
def test_unknown_create_never_repeats_paid_submission(runninghub_runtime, case):
    runtime = runninghub_runtime
    runtime.state["create"] = case
    client, _, _, _, request = runninghub_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    response = client.get(f"{TASKS}/{task['id']}").json()
    assert response["status"] == "failed" and not response["canRetry"] and not response["canResume"]
    with runtime.app[1]() as session:
        assert session.scalar(select(AIGenerationRecord)).status == "unknown"
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    execute(runtime, task)
    assert len(creates(runtime)) == 1


@pytest.mark.parametrize("failure", [503, "disconnect", "malformed"])
def test_failed_poll_recovers_original_task_without_create(runninghub_runtime, failure):
    runtime = runninghub_runtime
    client, _, _, _, request = runninghub_seed(runtime)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    runtime.state["poll"] = failure
    execute(runtime, task, steps=1)
    with runtime.app[1]() as session:
        current = session.get(AsyncTask, int(task["id"]))
        assert current.next_action is None and current.status == "failed"
    failed = client.get(f"{TASKS}/{task['id']}")
    assert failed.status_code == 200, failed.text
    assert failed.json()["canResume"] and not failed.json()["canRetry"]
    runtime.state["poll"] = "success"
    resumed = client.post(f"{TASKS}/{task['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    execute(runtime, task)
    response = client.get(f"{TASKS}/{task['id']}").json()
    assert response["status"] == "succeeded" and response["providerRequestId"] == TASK_ID
    assert len(creates(runtime)) == 1


def test_missing_saved_schema_fetches_before_real_media_upload(runninghub_runtime):
    runtime = runninghub_runtime
    runtime.state["schema"] = {
        **WORKFLOW,
        "3": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
    }
    client, _, _, _, request = runninghub_seed(runtime, workflow={})
    request["input"]["metadata"]["workflowParameters"] = {}
    content = png("green")
    resource = upload(client, content, source=request["projectId"], key="rh-reference")
    assert resource.status_code == 200, resource.text
    request["input"]["referenceImages"] = [
        {"id": "reference", "storageKey": "resource:" + resource.json()["resource"]["id"]}
    ]
    task = admit(client, request)
    execute(runtime, task)
    fetched = client.get(f"{TASKS}/{task['id']}")
    assert fetched.status_code == 200, fetched.text
    response = fetched.json()
    assert response["status"] == "succeeded", response
    assert [call["path"] for call in runtime.calls[:3]] == [
        "/api/openapi/getJsonApiFormat",
        "/task/openapi/upload",
        "/task/openapi/create",
    ]
    assert runtime.calls[1]["upload_bytes"] == content and len(creates(runtime)) == 1


def test_mixed_extensionless_outputs_use_actual_mime_and_last_media_projection(runninghub_runtime):
    runtime = runninghub_runtime
    runtime.state["output_kinds"] = ["video", "image", "audio", "video", "image", "audio"]
    client, _, _, _, request = runninghub_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    response = client.get(f"{TASKS}/{task['id']}").json()
    assert response["status"] == "succeeded", response
    result = json.loads(response["resultJson"])
    assert result["mode"] == "audio" and len(result["images"]) == 2
    assert result["video"]["outputIndex"] == 3 and result["audio"]["outputIndex"] == 5
    assert [item["outputIndex"] for item in response["outputs"]] == [1, 3, 4, 5]
    with runtime.app[1]() as session:
        assert sorted(session.scalars(select(CanvasResult.result_index))) == [1, 3, 4, 5]
        assets = list(session.scalars(select(CanvasLibraryAsset)))
        assert sorted(asset.payload_json["metadata"]["outputIndex"] for asset in assets) == [
            1,
            3,
            4,
            5,
        ]
    for item in [*result["images"], result["video"], result["audio"]]:
        assert client.get(item["url"]).content == runtime.media[item["mimeType"].split("/", 1)[0]]
    assert (
        len(creates(runtime)) == 1
        and len([call for call in runtime.calls if call["method"] == "GET"]) == 6
    )
    unavailable = client.post(
        "/api/v1/canvas-runtime/ops/canvas.task.bind",
        json={
            "opId": "runninghub-unselected-output",
            "params": {
                "canvasId": request["projectId"],
                "taskId": task["id"],
                "nodeId": request["input"]["metadata"]["nodeId"],
                "outputIndex": 0,
            },
        },
    )
    assert unavailable.status_code == 422, unavailable.text
    assert unavailable.json()["error"]["code"] == "output_not_available"


@pytest.mark.parametrize("mode", ["image", "video", "audio"])
def test_empty_and_unsupported_downloads_are_skipped_without_losing_valid_output(
    runninghub_runtime, mode
):
    runtime = runninghub_runtime
    runtime.state["output_kinds"] = ["unsupported", mode, "empty"]
    client, _, _, _, request = runninghub_seed(runtime, mode=mode)
    task = admit(client, request)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded", completed
    result = json.loads(completed["resultJson"])
    item = result["images"][0] if mode == "image" else result[mode]
    assert item["outputIndex"] == 1 and client.get(item["url"]).content == runtime.media[mode]
    assert [item["outputIndex"] for item in completed["outputs"]] == [1]
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        manifest = record.response_data["media_manifest"]
        assert [item["output_index"] for item in manifest if item.get("ignored")] == [1, 3]
        assert len(list(session.scalars(select(MediaAsset)))) == 1
        assert len(list(session.scalars(select(CanvasLibraryAsset)))) == 1
    execute(runtime, task)
    assert len(creates(runtime)) == 1
    assert len([call for call in runtime.calls if call["method"] == "GET"]) == 3


def test_all_empty_or_unsupported_downloads_are_terminal_without_retry(runninghub_runtime):
    runtime = runninghub_runtime
    runtime.state["output_kinds"] = ["unsupported", "empty"]
    client, _, _, _, request = runninghub_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "failed", completed
    assert completed["errorCode"] == "workflow_output_type_unsupported"
    assert not completed["canResume"] and not completed["canRetry"]
    assert completed["outputs"] == []
    with runtime.app[1]() as session:
        assert list(session.scalars(select(MediaAsset))) == []
        assert list(session.scalars(select(CanvasLibraryAsset))) == []
    execute(runtime, task)
    assert len(creates(runtime)) == 1
    assert len([call for call in runtime.calls if call["method"] == "GET"]) == 2


@pytest.mark.parametrize("mode", ["image", "video", "audio"])
def test_all_reference_kinds_and_mask_upload_frozen_bytes_in_source_order(runninghub_runtime, mode):
    runtime = runninghub_runtime
    workflow = {
        **WORKFLOW,
        "3": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "4": {"class_type": "LoadVideo", "inputs": {"video": "old.mp4"}},
        "5": {"class_type": "LoadAudio", "inputs": {"audio": "old.mp3"}},
        "6": {"class_type": "LoadImageMask", "inputs": {"mask": "old-mask.png"}},
    }
    client, _, _, _, request = runninghub_seed(runtime, mode=mode, workflow=workflow)
    expected_bytes = [runtime.media[kind] for kind in ("image", "video", "audio", "image")]
    identifiers = []
    for index, (kind, group) in enumerate(
        [
            ("image", "referenceImages"),
            ("video", "referenceVideos"),
            ("audio", "referenceAudios"),
            ("image", "mask"),
        ]
    ):
        uploaded = upload(
            client,
            expected_bytes[index],
            kind=kind,
            name=f"reference-{index}.media",
            mime={"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg"}[kind],
            source=request["projectId"],
            key=f"runninghub-reference-{index}",
        )
        assert uploaded.status_code == 200, uploaded.text
        identifier = uploaded.json()["resource"]["id"]
        identifiers.append(int(identifier))
        reference = {
            "id": f"reference-{index}",
            "storageKey": "resource:" + identifier,
            "url": "https://stale.invalid/reference",
            "bytes": 1,
        }
        request["input"][group] = reference if group == "mask" else [reference]
    task = admit(client, request)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded", completed
    uploads = [call for call in runtime.calls if "upload_bytes" in call]
    assert [call["upload_bytes"] for call in uploads] == expected_bytes
    names = {
        item["fieldName"]: item["fieldValue"]
        for item in creates(runtime)[0]["body"]["nodeInfoList"]
    }
    assert [names[name] for name in ("image", "video", "audio", "mask")] == [
        f"input/ref-{index}.media" for index in range(1, 5)
    ]
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        frozen = record.request_data["canvas_request"]["input"]
        assert all(
            not item.get("url") and item["bytes"] > 1
            for group in ("referenceImages", "referenceVideos", "referenceAudios")
            for item in frozen[group]
        )
        assert not frozen["mask"].get("url")
        refs = list(session.scalars(select(CanvasTaskMediaReference)))
        assert sorted(item.media_id for item in refs) == sorted(identifiers)
        assert {item.role for item in refs} == {
            "reference_media_ids",
            "video_reference_media_ids",
            "audio_reference_media_ids",
            "mask_media_ids",
        }
    assert len(creates(runtime)) == 1


@pytest.mark.parametrize("mode", ["image", "video", "audio"])
def test_expired_runninghub_budget_stops_before_original_task_poll(runninghub_runtime, mode):
    runtime = runninghub_runtime
    client, _, _, _, request = runninghub_seed(runtime, mode=mode)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    with runtime.app[1].begin() as session:
        record = session.scalar(select(AIGenerationRecord))
        current = session.get(AsyncTask, int(task["id"]))
        current.started_at = utcnow() - timedelta(
            seconds=record.config_snapshot["budget_seconds"] + 1
        )
        current.created_at = current.started_at - timedelta(seconds=1)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "failed" and completed["errorCode"] == "generation_timeout"
    assert not completed["canRetry"] and not completed["canResume"]
    assert not any(call["path"] == "/task/openapi/outputs" for call in runtime.calls)
    assert len(creates(runtime)) == 1


@pytest.mark.parametrize("failure", [404, "malformed"])
def test_video_query_failures_stop_after_three_and_keep_original_task(runninghub_runtime, failure):
    from short_drama.service.canvas_video_policy import POLL_STATE_KEY

    runtime = runninghub_runtime
    runtime.state["poll"] = failure
    client, _, _, _, request = runninghub_seed(runtime, mode="video")
    task = admit(client, request)
    execute(runtime, task, steps=1)
    category = "not_found" if failure == 404 else "malformed"
    for attempt in range(1, 4):
        execute(runtime, task, steps=1)
        with runtime.app[1]() as session:
            record = session.scalar(select(AIGenerationRecord))
            current = session.get(AsyncTask, int(task["id"]))
            assert record.response_data[POLL_STATE_KEY][category] == attempt
            assert current.status == ("running" if attempt < 3 else "failed")
    runtime.state["poll"] = "success"
    resumed = client.post(f"{TASKS}/{task['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded" and completed["providerRequestId"] == TASK_ID
    assert len(creates(runtime)) == 1


@pytest.mark.parametrize(
    "sequence,expected",
    [([503, 503, None], "succeeded"), ([503, 503, 503, None], "failed"), ([401, None], "failed")],
)
def test_video_download_is_bounded_and_explicit_recovery_never_creates_again(
    runninghub_runtime, sequence, expected
):
    runtime = runninghub_runtime
    runtime.state["download_sequence"] = sequence.copy()
    client, _, _, _, request = runninghub_seed(runtime, mode="video")
    task = admit(client, request)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == expected, completed
    attempts = 1 if sequence[0] == 401 else 3
    assert len([call for call in runtime.calls if call["method"] == "GET"]) == attempts
    if expected == "failed":
        assert completed["errorCode"] == "download_failed" and completed["canResume"]
        assert not completed["canRetry"]
        runtime.state["download_sequence"] = [None]
        resumed = client.post(f"{TASKS}/{task['id']}/resume")
        assert resumed.status_code == 200, resumed.text
        execute(runtime, task)
        completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded" and completed["providerRequestId"] == TASK_ID
    media = json.loads(completed["resultJson"])["video"]
    assert client.get(media["url"]).content == runtime.media["video"]
    assert len(creates(runtime)) == 1


@pytest.mark.parametrize("case", ["disabled", "selection", "parameters", "secret", "foreign-model"])
def test_invalid_workflow_admission_has_no_provider_side_effect(runninghub_runtime, case):
    runtime = runninghub_runtime
    client, _, _, _, request = runninghub_seed(runtime)
    if case == "disabled":
        client.put(PLUGIN, json={"enabled": False})
    elif case == "selection":
        request["input"]["execution"]["workflowId"] = "forged"
    elif case == "parameters":
        request["input"]["metadata"]["workflowParameters"] = {"field:999:cfg": 7}
    elif case == "secret":
        request["input"]["execution"]["apiKey"] = "forged"
    else:
        other, _ = account(runtime.app, "runninghub_other")
        response = other.post(TASKS, json=request)
        assert response.status_code == 404
        assert runtime.calls == []
        return
    response = client.post(TASKS, json=request)
    assert response.status_code in {400, 409, 422}, response.text
    assert runtime.calls == []


@pytest.mark.parametrize("mode", ["image", "video", "audio", "mixed"])
def test_saved_host_runninghub_model_test_freezes_private_config_without_binding(
    runninghub_runtime, mode
):
    runtime = runninghub_runtime
    client, user, _, _, request = runninghub_seed(
        runtime, mode="image" if mode == "mixed" else mode
    )
    model = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    if mode == "mixed":
        runtime.state["output_kinds"] = ["video", "image", "audio", "video", "image", "audio"]
    body = model_test_request(mode=model["service_type"], model=model["model_key"])
    body["channel"].update(
        id=f"host-{model['id']}",
        baseUrl=f"/api/v1/canvas-runtime/ai/models/{model['id']}",
        apiKey="forged-browser-key",
        models=[model["model_key"]],
        headers=[{"name": "X-RunningHub-Fixture", "value": "forged-header"}],
        modelProfiles=[
            {"model": model["model_key"], "capability": model["service_type"], "protocol": "forged"}
        ],
    )
    admitted = create(client, body)
    assert admitted.status_code == 202, admitted.text
    task = admitted.json()
    original = client.get("/api/v1/ai-model-configs/" + model["id"]).json()
    changed = client.patch(
        "/api/v1/ai-model-configs/" + model["id"],
        json={"row_version": original["row_version"], "apikey": "new-key-after-admission"},
    )
    assert changed.status_code == 200, changed.text
    execute(runtime, task)
    final = client.get(f"{TESTS}/{task['id']}")
    assert final.status_code == 200 and final.json()["status"] == "succeeded", final.text
    result = final.json()["result"]
    assert result["mode"] == ("audio" if mode == "mixed" else mode)
    media = [*result.get("images", [])]
    media += [result[kind] for kind in ("video", "audio") if result.get(kind)]
    assert len(media) == (4 if mode == "mixed" else 1)
    for item in media:
        assert client.get(item["url"]).content == runtime.media[item["mimeType"].split("/", 1)[0]]
    assert create(client, body).json()["id"] == task["id"]
    execute(runtime, task)
    assert len(creates(runtime)) == 1
    other, _ = account(runtime.app, "runninghub_host_other")
    assert other.get(f"{TESTS}/{task['id']}").status_code == 404
    assert other.get(media[0]["url"]).status_code == 404
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        shadow = session.get(AIModelConfig, record.config_id)
        current = session.get(AsyncTask, int(task["id"]))
        assert shadow.is_deleted and not shadow.enabled and record.config_id != int(model["id"])
        assert current.project_id is None and current.initiated_by == int(user["id"])
        assert list(session.scalars(select(CanvasTaskBinding))) == []
        assert list(session.scalars(select(CanvasResult))) == []
        public = json.dumps([record.request_data, record.config_snapshot, record.response_data])
        assert all(secret not in public for secret in (POINTS, UPLOAD, CUSTOM, "forged-browser"))


@pytest.mark.parametrize("failure", ["missing-key", "upload-401"])
def test_media_upload_failure_never_creates_a_paid_workflow(runninghub_runtime, failure):
    runtime = runninghub_runtime
    workflow = {**WORKFLOW, "3": {"class_type": "LoadImage", "inputs": {"image": "old.png"}}}
    client, _, _, _, request = runninghub_seed(runtime, workflow=workflow)
    resource = upload(client, png("green"), source=request["projectId"], key="rh-upload-boundary")
    assert resource.status_code == 200, resource.text
    request["input"]["referenceImages"] = [
        {"id": "reference", "storageKey": "resource:" + resource.json()["resource"]["id"]}
    ]
    if failure == "missing-key":
        model = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
        changed = client.patch(
            "/api/v1/ai-model-configs/" + model["id"],
            json={"row_version": model["row_version"], "secret_key": ""},
        )
        assert changed.status_code == 200, changed.text
        rejected = client.post(TASKS, json=request)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "canvas_workflow_upload_key_missing"
        assert runtime.calls == []
    else:
        runtime.state["upload_status"] = 401
        task = admit(client, request)
        execute(runtime, task)
        final = client.get(f"{TASKS}/{task['id']}").json()
        assert final["status"] == "failed" and final["errorCode"] == "workflow_upload_auth"
        with runtime.app[1]() as session:
            assert session.scalar(select(AIGenerationRecord)).status == "failed"
    assert not creates(runtime)
