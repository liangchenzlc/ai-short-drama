"""声明式 RunningHub 经真实 HTTP/MySQL/MinIO；不调用真实供应商。"""

import base64
import json
import os
import socket
import threading
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationGateway
from short_drama.ai.canvas_runninghub_workflow import RUNNINGHUB_WORKFLOW
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaAsset,
)
from short_drama.service.base import utcnow
from tests.integration.test_canvas_generation_media_runtime import playable_fixture
from tests.integration.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    admit,
    bind,
    records,
    seed,
)
from tests.integration.test_canvas_model_tests import TESTS, create
from tests.integration.test_canvas_model_tests import request as model_test_request
from tests.integration.test_canvas_resources import png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_video_protocols import execute
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated declarative RunningHub HTTP/MySQL/MinIO verification",
    ),
]
KEY = "declarative-runninghub-fixture-key"
HEADER = "declarative-runninghub-fixture-private-header"
PROVIDER_ID = "original-declarative-id"
CREATE = "/v1/task/openapi/create"
POLL = "/v1/task/openapi/status"
OPTIONS = {
    "version": "workflow-v2",
    "model": "explicit-workflow-model",
    "input": {"dynamic": {"values": [0, False, {"complete": "完整开放输入"}]}},
    "workflow": {"7": {"class_type": "Sampler", "inputs": {"seed": 0}}},
    "client_id": "workflow-client-17",
    "webhook": "https://callback.example/finished",
    "webhook_events_filter": ["completed", "failed"],
}


@pytest.fixture
def declarative_runtime(resource_app, tmp_path):
    app, factory, settings = resource_app
    video = playable_fixture(tmp_path, "video")
    calls, errors = [], []
    state = {"create": "accepted", "poll": "completed", "delivery": "url"}

    class Supplier(BaseHTTPRequestHandler):
        def handle_request(self):
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                authenticated = self.headers.get("Authorization") == "Bearer " + KEY
                private_header = self.headers.get("X-Workflow-Fixture") == HEADER
                body = json.loads(raw) if raw else None
                calls.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": body,
                        "authenticated": authenticated,
                        "private_header": private_header,
                        "external": self.server is external,
                    }
                )
                if self.server is external:
                    assert self.command == "GET" and self.path == "/cross-origin.mp4"
                    assert not authenticated and not private_header
                    return self.respond(video, mime="video/mp4")
                assert authenticated and private_header
                if self.command == "POST" and self.path == CREATE:
                    assert "apiKey" not in body and "taskId" not in body
                    if state["create"] == "disconnect":
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.close_connection = True
                        return
                    if state["create"] == "invalid":
                        return self.respond(b"invalid-json")
                    if state["create"] == "401":
                        return self.respond({}, status=401)
                    return self.respond({"prompt_id": PROVIDER_ID, "status": "queued"})
                if self.command == "GET" and self.path == POLL:
                    assert raw == b"", "The fixed manifest has no poll body or task ID"
                    if state["poll"] == "404":
                        return self.respond({}, status=404)
                    if state["poll"] == "invalid":
                        return self.respond(b"invalid-json")
                    if state["poll"] == "failed":
                        return self.respond({"error": {"code": False, "message": "工作流执行失败"}})
                    outputs = [{"fileUrl": "/workflow-output.mp4"}]
                    if state["delivery"] == "inline":
                        outputs = [{"b64_json": base64.b64encode(video).decode()}]
                    elif state["delivery"] == "external":
                        outputs = [{"url": state["external_origin"] + "/cross-origin.mp4"}]
                    elif state["delivery"] == "mixed":
                        outputs.append(
                            {"dataUrl": "data:video/mp4;base64," + base64.b64encode(video).decode()}
                        )
                    elif state["delivery"] == "missing":
                        outputs = []
                    return self.respond({"state": state["poll"], "data": outputs})
                if self.command == "GET" and self.path == "/workflow-output.mp4":
                    failures = state.get("download_failures") or []
                    if failures:
                        return self.respond({}, status=failures.pop(0))
                    return self.respond(video, mime="video/mp4")
                self.send_error(404)
            except BaseException as error:
                errors.append(type(error).__name__)
                self.close_connection = True

        do_POST = do_GET = handle_request

        def respond(self, value, status=200, mime="application/json"):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    external = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    state.update(
        origin=f"http://127.0.0.1:{server.server_port}",
        external_origin=f"http://127.0.0.1:{external.server_port}",
    )
    settings = settings.model_copy(update={"model_discovery_allowed_hosts": ["127.0.0.1"]})
    app.state.settings = settings
    threads = [
        threading.Thread(target=item.serve_forever, daemon=True) for item in (server, external)
    ]
    for thread in threads:
        thread.start()
    try:
        yield SimpleNamespace(
            app=(app, factory, settings),
            media=video,
            calls=calls,
            state=state,
            gateway=GenerationGateway(settings),
        )
        assert errors == [], errors
    finally:
        for item, thread in zip((server, external), threads, strict=True):
            item.shutdown()
            item.server_close()
            thread.join(5)
            assert not thread.is_alive(), "Task-owned declarative supplier did not stop"


def declarative_seed(runtime, *, defaults=None, capability=None):
    client, user, project, path, request = seed(
        runtime.app, "runninghub_declarative_author", kind="video"
    )
    response = client.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "声明式 RunningHub 受控模型",
            "model_key": "workflow-model",
            "service_type": "video",
            "provider": "受控供应商",
            "base_url": runtime.state["origin"] + "/v1",
            "apikey": KEY,
            "headers": [{"name": "X-Workflow-Fixture", "value": HEADER}],
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": "runninghub-workflow",
                **({"default_options": defaults} if defaults is not None else {}),
                **({"capability_config": {"video": capability}} if capability is not None else {}),
            },
        },
    )
    assert response.status_code == 201, response.text
    model = response.json()
    request["logicalModelId"], request["model"] = model["id"], model["model_key"]
    request["input"]["config"] = {"videoSeconds": "6", "size": "16:9", "vquality": "720p"}
    workspace = client.get(WORKSPACE).json()
    selection = f"host-{model['id']}::{model['model_key']}"
    selected = client.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "model": selection,
                "videoModel": selection,
                **request["input"]["config"],
                "videoGenerateAudio": "false",
            },
        },
    )
    assert selected.status_code == 200, selected.text
    return client, user, project, path, request


def creates(runtime):
    return [call for call in runtime.calls if call["path"] == CREATE]


@pytest.mark.parametrize("delivery", ["url", "inline", "external", "mixed"])
def test_declarative_create_poll_archive_first_video_bind_and_author_boundaries(
    declarative_runtime, delivery
):
    runtime = declarative_runtime
    runtime.state["delivery"] = delivery
    client, user, project, path, request = declarative_seed(runtime)
    request["input"]["metadata"]["providerOptions"] = {"runninghub-workflow": deepcopy(OPTIONS)}
    member, other = account(runtime.app, "runninghub_declarative_member")
    join(runtime.app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    _, actions = execute(runtime, task)
    assert actions == ["submit", "poll", "save"]
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    media = json.loads(completed["resultJson"])["video"]
    assert client.get(media["url"]).content == runtime.media
    assert media["storageKey"].startswith("resource:")
    assert (media["width"], media["height"], media["durationMs"]) == (160, 90, 1000)
    assert len(creates(runtime)) == 1
    expected = {
        **{key: value for key, value in OPTIONS.items() if key != "workflow"},
        "prompt": OPTIONS["workflow"],
    }
    assert creates(runtime)[0]["body"] == expected
    polls = [call for call in runtime.calls if call["path"] == POLL]
    assert len(polls) == 1 and polls[0]["body"] is None and polls[0]["method"] == "GET"
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert member.get(media["url"]).status_code == 404
    assert (
        client.get(path + "/my-document").json()["source_document"]["nodes"][0]["metadata"][
            "content"
        ]
        == before["source_document"]["nodes"][0]["metadata"]["content"]
    )
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.adapter == RUNNINGHUB_WORKFLOW and record.provider_task_id == PROVIDER_ID
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == (
            2 if delivery == "mixed" else 1
        )
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 1
    if delivery == "mixed":
        rejected = client.post(
            "/api/v1/canvas-runtime/ops/canvas.task.bind",
            json={
                "op_id": "unselected-" + uuid4().hex,
                "params": {
                    "canvasId": request["projectId"],
                    "taskId": task["id"],
                    "nodeId": "runtime-node",
                    "outputIndex": 1,
                },
            },
        )
        assert (
            rejected.status_code == 422
            and rejected.json()["error"]["code"] == "output_not_available"
        )
    bound = bind(client, request, task)
    assert bound["result"]["outputIndex"] == 0
    assert member.get(media["url"]).content == runtime.media
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404


def test_permanent_image_video_audio_references_inline_exact_frozen_bytes(
    declarative_runtime, tmp_path
):
    runtime = declarative_runtime
    capability = {
        "references": {"maxImages": 9, "maxVideos": 1, "maxAudios": 1},
        "operations": ["reference_to_video"],
    }
    client, user, project, path, request = declarative_seed(runtime, capability=capability)
    request["operation"] = "reference_to_video"
    content = {
        "image": png("purple"),
        "video": runtime.media,
        "audio": playable_fixture(tmp_path, "audio"),
    }
    for kind, group in (
        ("image", "referenceImages"),
        ("video", "referenceVideos"),
        ("audio", "referenceAudios"),
    ):
        response = upload(
            client,
            content[kind],
            source=request["projectId"],
            key=uuid4().hex,
            kind=kind,
            name=kind + {"image": ".png", "video": ".mp4", "audio": ".mp3"}[kind],
            mime={"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg"}[kind],
        )
        assert response.status_code == 200, response.text
        resource = response.json()["resource"]
        request["input"][group] = [
            {
                "id": kind + "-reference",
                "storageKey": "resource:" + resource["id"],
                "url": "https://forged.example/preview",
                "type": "forged/type",
                "bytes": 99999,
                "width": 999,
                "height": 999,
            }
        ]
    task = admit(client, request)
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        for group in ("referenceImages", "referenceVideos", "referenceAudios"):
            reference = record.request_data["canvas_request"]["input"][group][0]
            assert reference["storageKey"].startswith("resource:") and "url" not in reference
            assert reference.get("width") != 999 and reference["bytes"] != 99999
    execute(runtime, task)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    body = creates(runtime)[0]["body"]
    for kind in ("image", "video", "audio"):
        data_url = body["input"][kind + "s"][0]
        assert data_url.startswith("data:" + kind + "/")
        assert base64.b64decode(data_url.split(",", 1)[1], validate=True) == content[kind]
    assert set(body) == {"model", "input"}


@pytest.mark.parametrize("failure", ["disconnect", "invalid"])
def test_uncertain_create_is_private_nonretryable_and_never_recreated(declarative_runtime, failure):
    runtime = declarative_runtime
    runtime.state["create"] = failure
    client, user, _, _, request = declarative_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed"
    assert failed["errorCode"] == (
        "transport_error" if failure == "disconnect" else "invalid_response"
    )
    assert failed["canRetry"] is False and failed["canResume"] is False
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.status == "unknown" and record.provider_task_id is None
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    retry = deepcopy(request)
    retry["input"]["metadata"].update(clientOperationId=uuid4().hex, retryOf=task["id"])
    assert client.post(TASKS, json=retry).status_code == 409
    assert len(creates(runtime)) == 1
    assert not any(call["path"] == POLL for call in runtime.calls)


@pytest.mark.parametrize("failure", ["404", "invalid"])
def test_three_video_poll_failures_stop_then_resume_same_unparameterized_get(
    declarative_runtime, failure
):
    runtime = declarative_runtime
    runtime.state["poll"] = failure
    client, user, _, _, request = declarative_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and failed["canRetry"] is False
    assert len([call for call in runtime.calls if call["path"] == POLL]) == 3
    runtime.state["poll"] = "completed"
    resumed = client.post(f"{TASKS}/{task['id']}/query-provider")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["recovered"] is True and resumed.json()["task"]["id"] == task["id"]
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert len(creates(runtime)) == 1
    assert len([call for call in runtime.calls if call["path"] == POLL]) == 4


@pytest.mark.parametrize("delivery", ["missing", "url"])
def test_failed_or_empty_completion_does_not_invent_media_content(declarative_runtime, delivery):
    runtime = declarative_runtime
    runtime.state["delivery"] = delivery
    runtime.state["poll"] = "completed" if delivery == "missing" else "failed"
    client, user, _, _, request = declarative_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and failed.get("resultJson") is None
    assert len(creates(runtime)) == 1
    assert not any(
        call["path"].endswith("/content") or call["path"] == "/workflow-output.mp4"
        for call in runtime.calls
    )
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0


@pytest.mark.parametrize("failure", [False, True])
def test_saved_host_model_test_uses_server_bearer_protocol_and_private_output(
    declarative_runtime, failure
):
    runtime = declarative_runtime
    client, user, _, _, request = declarative_seed(runtime)
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    before = client.get(model_path).json()
    body = model_test_request(mode="video", model=request["model"], prompt="独立视频连接测试")
    body["channel"].update(
        id="host-" + request["logicalModelId"],
        baseUrl="/api/v1/canvas-runtime/ai/models/" + request["logicalModelId"],
        apiKey="forged-browser-key",
        models=[request["model"]],
        modelProfiles=[
            {"model": request["model"], "capability": "video", "protocol": "plugin/forged"}
        ],
        headers=[{"name": "X-Workflow-Fixture", "value": "forged-header"}],
    )
    if failure:
        runtime.state["create"] = "401"
    admitted = create(client, body)
    assert admitted.status_code == 202, admitted.text
    task = admitted.json()
    other, _ = account(runtime.app, "rh_declarative_test_other")
    assert other.get(f"{TESTS}/{task['id']}").status_code == 404
    assert create(other, body).status_code == 404
    execute(runtime, task)
    final = client.get(f"{TESTS}/{task['id']}").json()
    if failure:
        assert final["status"] == "failed" and final["errorCode"] == "provider_auth"
        assert final.get("result") is None
    else:
        assert final["status"] == "succeeded"
        media = final["result"]["video"]
        assert client.get(media["url"]).content == runtime.media
        assert other.get(media["url"]).status_code == 404
    assert create(client, body).json()["id"] == task["id"]
    assert client.get(model_path).json() == before
    assert (
        len(creates(runtime)) == 1
        and creates(runtime)[0]["authenticated"]
        and creates(runtime)[0]["private_header"]
    )
    assert creates(runtime)[0]["body"] == {
        "model": request["model"],
        "input": {"prompt": body["prompt"], "images": [], "videos": [], "audios": []},
    }
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.adapter == RUNNINGHUB_WORKFLOW and record.config_id != int(
            request["logicalModelId"]
        )
        assert session.get(AIModelConfig, record.config_id).is_deleted
        assert session.get(AsyncTask, int(task["id"])).project_id is None
        assert session.scalar(select(func.count()).select_from(CanvasTaskBinding)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0


def test_expired_declarative_video_budget_does_not_poll_original_task(declarative_runtime):
    runtime = declarative_runtime
    client, user, _, _, request = declarative_seed(runtime)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    with runtime.app[1].begin() as session:
        record = session.scalar(select(AIGenerationRecord))
        row = session.get(AsyncTask, int(task["id"]))
        row.started_at = utcnow() - timedelta(seconds=record.config_snapshot["budget_seconds"] + 1)
        row.created_at = row.started_at - timedelta(seconds=1)
    execute(runtime, task)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "failed" and final["errorCode"] == "generation_timeout"
    assert not any(call["path"] == POLL for call in runtime.calls)
    assert len(creates(runtime)) == 1


def test_queued_options_and_credentials_remain_frozen_after_saved_model_changes(
    declarative_runtime,
):
    runtime = declarative_runtime
    client, user, _, _, request = declarative_seed(runtime)
    request["input"]["metadata"]["providerOptions"] = {"runninghub-workflow": deepcopy(OPTIONS)}
    task = admit(client, request)
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    current = client.get(model_path).json()
    changed = client.patch(
        model_path,
        json={
            "row_version": current["row_version"],
            "apikey": "changed-placeholder-key",
            "headers": [{"name": "X-Workflow-Fixture", "value": "changed-placeholder-header"}],
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": "runninghub-workflow",
                "default_options": {"workflow": {"new": "new-input"}},
            },
        },
    )
    assert changed.status_code == 200, changed.text
    request["input"]["metadata"]["providerOptions"]["runninghub-workflow"]["input"] = {
        "changed": True
    }
    execute(runtime, task)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded"
    expected = {
        **{key: value for key, value in OPTIONS.items() if key != "workflow"},
        "prompt": OPTIONS["workflow"],
    }
    assert len(creates(runtime)) == 1 and creates(runtime)[0]["body"] == expected
    assert creates(runtime)[0]["authenticated"] and creates(runtime)[0]["private_header"]


def test_download_retry_resumes_save_without_query_or_create(declarative_runtime):
    runtime = declarative_runtime
    runtime.state["download_failures"] = [503, 503, 503]
    client, user, _, _, request = declarative_seed(runtime)
    task = admit(client, request)
    execute(runtime, task)
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and failed["canResume"] is True
    assert len([call for call in runtime.calls if call["path"] == "/workflow-output.mp4"]) == 3
    resumed = client.post(f"{TASKS}/{task['id']}/resume", json={})
    assert resumed.status_code == 200, resumed.text
    execute(runtime, resumed.json())
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert (
        len(creates(runtime)) == 1
        and len([call for call in runtime.calls if call["path"] == POLL]) == 1
    )
