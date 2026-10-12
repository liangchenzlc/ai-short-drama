"""真实HTTP/MySQL的分镜批准、结构校验及稳定回填；供应商为受控服务。"""

import json
import os
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai.gateway import GenerationGateway
from short_drama.domain import AIGenerationRecord, CanvasResult, CanvasToolRun, MediaFile
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_generation_runtime import TASKS, bind, records, seed
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_text_image_runtime import KEY, save_channel
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.unit.test_canvas_storyboard_generation import reply_body

RUNS = "/api/v1/canvas-runtime/creation-runs"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated storyboard HTTP/MySQL/MinIO",
    ),
]


@pytest.fixture(params=["chat-completion", "openai-response"])
def storyboard_runtime(resource_app, request):
    state = {"text": json.dumps(reply_body(), ensure_ascii=False), "hold": False}
    posts, errors = [], []
    first_sent, finish_allowed = Event(), Event()
    responses = request.param == "openai-response"

    class Supplier(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            try:
                assert self.path == ("/v1/responses" if responses else "/v1/chat/completions")
                assert self.headers.get("Authorization") == "Bearer " + KEY
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                posts.append(body)
                text = state["text"]
                if not body.get("stream"):
                    payload = json.dumps(
                        {"status": "completed", "output_text": text}
                        if responses
                        else {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                midpoint = len(text) // 2
                self.chunk(
                    {"type": "response.output_text.delta", "delta": text[:midpoint]}
                    if responses
                    else {"choices": [{"delta": {"content": text[:midpoint]}}]}
                )
                first_sent.set()
                if state["hold"]:
                    assert finish_allowed.wait(120), "Storyboard supplier gate was not released"
                self.chunk(
                    {"type": "response.output_text.delta", "delta": text[midpoint:]}
                    if responses
                    else {"choices": [{"delta": {"content": text[midpoint:]}}]}
                )
                if responses:
                    self.chunk(
                        {
                            "type": "response.completed",
                            "response": {"status": "completed", "output_text": text},
                        }
                    )
                else:
                    self.chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                    self.chunk("[DONE]")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except BaseException as error:
                errors.append(type(error).__name__)
                self.close_connection = True

        def chunk(self, body):
            value = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            payload = ("data: " + value + "\n\n").encode()
            self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = resource_app[2].model_copy(update={"model_discovery_allowed_hosts": ["127.0.0.1"]})
    resource_app[0].state.settings = settings
    runtime = SimpleNamespace(
        app=(resource_app[0], resource_app[1], settings),
        origin=f"http://127.0.0.1:{server.server_port}",
        protocol=request.param,
        state=state,
        posts=posts,
        errors=errors,
        first_sent=first_sent,
        finish_allowed=finish_allowed,
        gateway=GenerationGateway(settings),
    )
    try:
        yield runtime
    finally:
        finish_allowed.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive() and errors == [], errors


def storyboard_seed(runtime):
    owner, user, project, path, request = seed(runtime.app, "storyboard_runtime_owner")
    channel = save_channel(runtime, owner)
    request["logicalModelId"] = channel["modelProfiles"][0]["logicalModelId"]
    request["operation"] = "storyboard"
    request["input"].update(
        textHistory=[],
        canvasAssets=[],
        requirements="输出可编辑分镜。",
        projectStyle={"presetId": "cinematic", "title": "电影", "prompt": "低饱和"},
        characters=[],
        shotCount=2,
        shotDurationSeconds=5,
    )
    request["input"]["metadata"]["clientOperationId"] = "storyboard:" + uuid4().hex
    current = owner.get(path + "/my-document").json()
    document = current["source_document"]
    document["nodes"][0].update(
        type="script",
        title="分镜脚本",
        width=920,
        height=360,
        metadata={"storyboard": {"rows": [], "visibleColumns": ["camera"], "referenceNodeIds": []}},
    )
    document["nodes"].append(
        {
            "id": "style",
            "type": "text",
            "title": "项目画风 · 电影",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {
                "workflowKind": "styleboard",
                "stylePresetId": "cinematic",
                "content": "低饱和",
            },
        }
    )
    response = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "storyboard-source-" + uuid4().hex},
        json={"expected_row_version": current["row_version"], "source_document": document},
    )
    assert response.status_code == 200, response.text
    return owner, user, project, path, request


def prepare_storyboard(scope):
    owner, _user, _project, _path, request = scope
    metadata = request["input"]["metadata"]
    created = owner.post(
        RUNS,
        json={
            "clientKey": metadata["clientOperationId"],
            "canvasId": request["projectId"],
            "state": {
                "kind": "storyboard",
                "nodeId": metadata["nodeId"],
                "sourceNodeId": metadata["sourceNodeId"],
                "logicalModelId": request["logicalModelId"],
                "model": request["model"],
            },
        },
    )
    assert created.status_code == 201, created.text
    run = created.json()["run"]
    claimed = owner.post(
        f"{RUNS}/{run['id']}/claim",
        json={"expectedEpoch": run["executionEpoch"], "owner": "storyboard:owner"},
    )
    assert claimed.status_code == 200, claimed.text
    guard = {"executionEpoch": claimed.json()["executionEpoch"], "owner": "storyboard:owner"}
    prepared = owner.post(
        f"{RUNS}/{run['id']}/submissions/prepare",
        json={**guard, "itemKey": "storyboard", "request": request},
    )
    assert prepared.status_code == 200, prepared.text
    return run, guard, prepared.json()


def approve_and_execute(scope, run, guard, submission):
    owner = scope[0]
    approved = owner.post(
        f"{RUNS}/{run['id']}/submissions/approve",
        json={**guard, "submissionIds": [submission["id"]]},
    )
    assert approved.status_code == 200, approved.text
    response = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": submission["id"]}
    )
    assert response.status_code == 202, response.text
    return response.json()


def execute(runtime, task):
    GenerationExecutionService(
        runtime.app[1], runtime.app[2], runtime.gateway, runtime.app[0].state.storage
    ).execute(task["id"], 1)


def test_storyboard_approval_execution_stable_delivery_and_shared_bind(storyboard_runtime):
    runtime = storyboard_runtime
    scope = storyboard_seed(runtime)
    owner, user, project, path, request = scope
    member, member_user = account(runtime.app, "storyboard_runtime_member")
    outsider, _ = account(runtime.app, "storyboard_runtime_outside")
    join(runtime.app, owner, member, project["id"], member_user["id"])
    run, guard, submission = prepare_storyboard(scope)
    assert runtime.posts == [] and records(runtime.app, user)["tasks"] == 0
    assert owner.post(TASKS, json=request).status_code == 409
    command = {**guard, "submissionId": submission["id"]}
    assert owner.post(f"{RUNS}/{run['id']}/execute", json=command).status_code == 409
    task = approve_and_execute(scope, run, guard, submission)
    replay = owner.post(f"{RUNS}/{run['id']}/execute", json=command)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    before = owner.get(path + "/my-document").json()
    execute(runtime, task)
    result = owner.get(f"{TASKS}/{task['id']}")
    assert result.status_code == 200, result.text
    current = result.json()
    assert current["status"] == "succeeded" and current["resultState"] == "READY"
    structured = json.loads(current["resultJson"])
    assert [row["id"] for row in structured["rows"]] == [
        f"shot:{task['id']}:1",
        f"shot:{task['id']}:2",
    ]
    assert owner.get(path + "/my-document").json() == before
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert outsider.get(f"{RUNS}/{run['id']}").status_code == 404
    bound = bind(owner, request, task)["result"]
    assert bound["node"]["type"] == "script" and bound["node"]["title"] == structured["title"]
    assert bound["node"]["metadata"]["storyboard"]["rows"] == structured["rows"]
    shared = member.get(path + "/my-document").json()["source_document"]["nodes"][0]["metadata"]
    assert shared["storyboard"]["rows"] == structured["rows"] and "taskId" not in shared
    assert "camera" in shared["storyboard"]["visibleColumns"]
    assert len(runtime.posts) == 1 and records(runtime.app, user) == {
        "tasks": 1,
        "records": 1,
        "bindings": 1,
    }
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        assert session.scalar(select(CanvasToolRun)).source_media_id is None
        assert session.scalar(select(CanvasResult)).attachment_status == "attached"
        assert session.scalar(select(MediaFile.id)) is None
        frozen = session.scalar(select(AIGenerationRecord))
        assert frozen.response_data["canvas_storyboard"]["rows"] == structured["rows"]
        assert frozen.request_data["canvas_storyboard_source"]["rows"] == []
    wire = json.dumps(runtime.posts[0], ensure_ascii=False)
    assert "低饱和" in wire and "durationSeconds" in wire and request["prompt"] in wire


@pytest.mark.parametrize("stage", ["approve", "bind"])
def test_storyboard_edits_block_approval_or_bind_and_keep_user_rows(storyboard_runtime, stage):
    runtime = storyboard_runtime
    scope = storyboard_seed(runtime)
    owner, user, _project, path, request = scope
    run, guard, submission = prepare_storyboard(scope)
    task = approve_and_execute(scope, run, guard, submission) if stage == "bind" else None
    if task:
        execute(runtime, task)
    current = owner.get(path + "/my-document").json()
    document = current["source_document"]
    document["nodes"][0]["metadata"]["storyboard"]["rows"] = [
        {
            "id": "user-row",
            "shotNumber": 1,
            "durationSeconds": 6,
            "plotDescription": "用户后来保存的镜头。",
            "characters": [],
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "later-edit"},
        json={"expected_row_version": current["row_version"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    response = (
        owner.post(
            f"{RUNS}/{run['id']}/submissions/approve",
            json={**guard, "submissionIds": [submission["id"]]},
        )
        if stage == "approve"
        else owner.post(
            "/api/v1/canvas-runtime/ops/canvas.task.bind",
            json={
                "opId": "edited-bind",
                "params": {
                    "canvasId": request["projectId"],
                    "taskId": task["id"],
                    "nodeId": "runtime-node",
                },
            },
        )
    )
    assert response.status_code == 409, response.text
    final = owner.get(path + "/my-document").json()["source_document"]
    assert (
        final["nodes"][0]["metadata"]["storyboard"]["rows"]
        == document["nodes"][0]["metadata"]["storyboard"]["rows"]
    )
    assert len(runtime.posts) == (1 if task else 0)
    assert records(runtime.app, user)["tasks"] == (1 if task else 0)


@pytest.mark.parametrize("output", ["invalid-json", "wrong-count", "forged-media"])
def test_invalid_storyboard_output_fails_private_task_without_graph_overwrite(
    storyboard_runtime, output
):
    runtime = storyboard_runtime
    value = deepcopy(reply_body())
    if output == "wrong-count":
        value["rows"].pop()
    elif output == "forged-media":
        value["rows"][0]["imageNodeId"] = "injected"
    runtime.state["text"] = "bad JSON" if output == "invalid-json" else json.dumps(value)
    scope = storyboard_seed(runtime)
    run, guard, submission = prepare_storyboard(scope)
    task = approve_and_execute(scope, run, guard, submission)
    before = scope[0].get(scope[3] + "/my-document").json()
    execute(runtime, task)
    result = scope[0].get(f"{TASKS}/{task['id']}")
    assert result.status_code == 200, result.text
    assert (
        result.json()["status"] == "failed"
        and result.json()["errorCode"] == "invalid_storyboard_result"
    )
    assert result.json()["textDraft"] == runtime.state["text"]
    assert scope[0].get(scope[3] + "/my-document").json() == before
    with runtime.app[1]() as session:
        assert session.scalar(select(CanvasResult.id)) is None
    assert len(runtime.posts) == 1
