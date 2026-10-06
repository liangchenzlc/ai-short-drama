"""真实 Chat 图片 wire、MinIO、正文增量与原子准入；不调用付费供应商。"""

import hashlib
import json
import os
import socket
import time
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskTextDelta, MediaFile
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_generation_runtime import TASKS, admit, records, seed
from tests.integration.test_canvas_resources import canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_text_stream import events
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated Chat image HTTP/MySQL/MinIO verification",
    ),
]
WORKSPACE = "/api/v1/canvas-runtime/workspace/model-config"
KEY = "canvas-chat-image-placeholder-not-a-real-key"
HEADER = "X-Chat-Image-Fixture"
HEADER_VALUE = "canvas-chat-image-placeholder-private-header"
FIRST, SECOND = "按顺序看到红色图片🌈", "，然后是蓝色图片。"
CAPABILITY = {
    "references": {"maxImages": 2, "maxImageBytes": 1024, "promptMaxChars": 32000},
    "streaming": True,
}


@pytest.fixture
def text_image_runtime(resource_app):
    app, factory, original_settings = resource_app
    state = {"mode": "success", "hold": False}
    posts, supplier_errors = [], []
    first_sent, finish_allowed = Event(), Event()

    class Supplier(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            try:
                assert self.path == "/v1/chat/completions"
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                wire, images = deepcopy(body), []
                evidence = {
                    "path": self.path,
                    "authenticated": self.headers.get("Authorization") == "Bearer " + KEY,
                    "custom_header": self.headers.get(HEADER) == HEADER_VALUE,
                    "body": wire,
                    "images": images,
                }
                posts.append(evidence)
                with httpx.Client(timeout=5, trust_env=False) as client:
                    for message in wire["messages"]:
                        if not isinstance(message["content"], list):
                            continue
                        for item in message["content"]:
                            if item["type"] != "image_url":
                                continue
                            url = item["image_url"]["url"]
                            signed = urlsplit(url)
                            assert signed.scheme in {"http", "https"}
                            assert "X-Amz-Signature=" in signed.query
                            response = client.get(url)
                            assert response.status_code == 200
                            content = response.content
                            images.append(
                                {
                                    "bytes": content,
                                    "sha256": hashlib.sha256(content).hexdigest(),
                                    "mime_type": response.headers["content-type"],
                                }
                            )
                            # 不把临时签名 URL、header 或 API Key 写入测试证据。
                            item["image_url"]["url"] = f"saved-image-{len(images) - 1}"
                if state["mode"] == "401":
                    self.json_response(401, {"error": {"message": "controlled auth failure"}})
                    return
                if body["stream"] is False:
                    self.json_response(
                        200,
                        {
                            "choices": [
                                {"message": {"content": FIRST + SECOND}, "finish_reason": "stop"}
                            ]
                        },
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.chunk({"choices": [{"delta": {"content": FIRST}}]})
                first_sent.set()
                if state["hold"]:
                    assert finish_allowed.wait(15), "Test did not release the supplier stream"
                if state["mode"] == "disconnect":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                # 跨过真实 writer 的批次间隔，独立保存第二段，不替换 callback/Gateway。
                Event().wait(0.14)
                self.chunk({"choices": [{"delta": {"content": SECOND}}]})
                self.chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                self.chunk("[DONE]")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except BaseException as error:
                supplier_errors.append(type(error).__name__)
                self.close_connection = True

        def json_response(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def chunk(self, body):
            value = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            payload = ("data: " + value + "\n\n").encode()
            self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    settings = original_settings.model_copy(update={"generation_allowed_hosts": ["127.0.0.1"]})
    app.state.settings = settings
    runtime = SimpleNamespace(
        app=(app, factory, settings),
        origin=origin,
        state=state,
        posts=posts,
        supplier_errors=supplier_errors,
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
        server_thread.join(5)
        assert not server_thread.is_alive(), "Task-owned Chat supplier did not stop"
        assert supplier_errors == [], supplier_errors


def save_channel(runtime, client, *, capability=None, protocol="chat-completion"):
    current = client.get(WORKSPACE).json()
    capability_config = {"version": 1}
    if capability != {}:
        capability_config["text"] = deepcopy(CAPABILITY if capability is None else capability)
    channel = {
        "id": "chat-image-provider",
        "name": "真实图片文字协议验证",
        "baseUrl": runtime.origin + "/v1",
        "apiKey": KEY,
        "apiFormat": "openai",
        "enabled": True,
        "headers": [{"name": HEADER, "value": HEADER_VALUE}],
        "models": ["gpt-4o-mini"],
        "modelProfiles": [
            {
                "model": "gpt-4o-mini",
                "capability": "text",
                "protocol": protocol,
                "capabilityConfig": capability_config,
            }
        ],
    }
    response = client.put(
        WORKSPACE,
        json={
            "expected_row_version": current["row_version"],
            "preferences": current["preferences"],
            "channels": [channel],
        },
    )
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["channels"] if item["id"] == channel["id"])


def saved_image(client, source_key, color):
    content = png(color)
    response = upload(client, content, source=source_key, key=uuid4().hex)
    assert response.status_code == 200, response.text
    resource = response.json()["resource"]
    return (
        {
            "id": "image-" + uuid4().hex,
            "name": color,
            "storageKey": "resource:" + resource["id"],
            "type": "image/jpeg",
            "bytes": 1,
            "width": 999,
            "height": 999,
            "url": "https://never-fetch.example/forged.png",
        },
        content,
    )


def text_seed(runtime, *, capability=None, protocol="chat-completion"):
    client, user, project, path, request = seed(runtime.app, "canvas_chat_image_author")
    channel = save_channel(runtime, client, capability=capability, protocol=protocol)
    profile = channel["modelProfiles"][0]
    request["logicalModelId"] = profile["logicalModelId"]
    references, contents = zip(
        *[saved_image(client, request["projectId"], color) for color in ("red", "blue")],
        strict=True,
    )
    request["input"]["referenceImages"] = list(references)
    request["input"]["textHistory"] = [
        {"role": "user", "content": "上一条输入"},
        {"role": "assistant", "content": "上一条回复"},
    ]
    return client, user, project, path, request, list(contents)


def executor(runtime):
    return GenerationExecutionService(
        runtime.app[1], runtime.app[2], runtime.gateway, runtime.app[0].state.storage
    )


@contextmanager
def running(runtime, task):
    failures = []

    def work():
        try:
            executor(runtime).execute(task["id"], 1)
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=work, daemon=True)
    thread.start()
    try:
        assert runtime.first_sent.wait(10), failures
        yield
    finally:
        runtime.finish_allowed.set()
        thread.join(15)
        assert not thread.is_alive(), "Task-owned Chat executor did not stop"
        assert failures == [], failures


def first_delta(client, task):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"{TASKS}/{task['id']}/text-deltas")
        assert response.status_code == 200, response.text
        value = response.json()
        if value["textDraft"] == FIRST:
            return value
        Event().wait(0.03)
    pytest.fail("The actual provider first chunk was not persisted before completion")


def assert_wire(runtime, request, contents, stream):
    assert len(runtime.posts) == 1, runtime.posts
    post = runtime.posts[0]
    assert post["authenticated"] and post["custom_header"]
    body = post["body"]
    assert body["model"] == request["model"] and body["stream"] is stream
    assert body.get("stream_options") == ({"include_usage": True} if stream else None)
    assert body["messages"] == [
        {"role": "system", "content": request["input"]["config"]["systemPrompt"]},
        *request["input"]["textHistory"],
        {
            "role": "user",
            "content": [
                {"type": "text", "text": request["prompt"]},
                {"type": "image_url", "image_url": {"url": "saved-image-0"}},
                {"type": "image_url", "image_url": {"url": "saved-image-1"}},
            ],
        },
    ]
    assert [item["bytes"] for item in post["images"]] == contents
    assert [item["sha256"] for item in post["images"]] == [
        hashlib.sha256(content).hexdigest() for content in contents
    ]
    assert all(item["mime_type"] == "image/png" for item in post["images"])


def assert_frozen(runtime, request, task, contents, capability):
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.config_snapshot["canvas_text_capability"] == capability
        frozen = record.request_data
        expected = [
            {
                "media_id": item["storageKey"].removeprefix("resource:"),
                "mime_type": "image/png",
                "byte_size": len(content),
            }
            for item, content in zip(request["input"]["referenceImages"], contents, strict=True)
        ]
        assert frozen["canvas_text_references"] == {"mode": "text", "references": expected}
        assert frozen["input"]["reference_media_ids"] == [item["media_id"] for item in expected]
        normalized = frozen["canvas_request"]["input"]["referenceImages"]
        assert all(item["type"] == "image/png" for item in normalized)
        assert [item["bytes"] for item in normalized] == [len(content) for content in contents]
        assert all((item["width"], item["height"]) == (37, 19) for item in normalized)
        assert all("url" not in item and "dataUrl" not in item for item in normalized)
        public_input = json.loads(task["inputJson"])
        assert public_input["referenceImages"] == normalized
        serialized = json.dumps(
            [frozen, record.config_snapshot, record.response_data, public_input], ensure_ascii=False
        )
        assert "X-Amz-" not in serialized and "reference_urls" not in frozen["input"]
        assert KEY not in serialized and HEADER_VALUE not in serialized


@pytest.mark.parametrize("variant", ["stream", "nonstream", "channel-nonstream"])
def test_chat_two_saved_images_preserve_wire_freeze_and_private_replay(text_image_runtime, variant):
    runtime = text_image_runtime
    capability = deepcopy(CAPABILITY)
    if variant == "channel-nonstream":
        capability["streaming"] = False
    client, user, project, path, request, contents = text_seed(runtime, capability=capability)
    request["input"]["textOptions"]["stream"] = variant != "nonstream"
    stream = variant == "stream"
    member, other = account(runtime.app, "canvas_chat_image_reader")
    join(runtime.app, client, member, project["id"], other["id"])
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    assert_frozen(runtime, request, task, contents, capability)
    changed = deepcopy(request)
    changed["input"]["referenceImages"].reverse()
    assert client.post(TASKS, json=changed).status_code == 409
    if stream:
        runtime.state["hold"] = True
        with running(runtime, task):
            first = first_delta(client, task)
            assert first["status"] == "running" and first["complete"] is False
            assert first["textDraft"] == FIRST and not first.get("finalText")
            assert [item["sequence"] for item in first["deltas"]] == [1]
            detail = client.get(f"{TASKS}/{task['id']}").json()
            assert detail["textDraft"] == FIRST and detail["textDraftSequence"] == 1
            assert client.get(path + "/my-document").json() == before
            assert member.get(f"{TASKS}/{task['id']}/text-deltas").status_code == 404
            with runtime.app[1]() as session:
                delta = session.scalar(select(CanvasTaskTextDelta))
                assert delta.content == FIRST and delta.sequence == 1
    else:
        executor(runtime).execute(task["id"], 1)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded"
    assert json.loads(final["resultJson"])["text"] == FIRST + SECOND
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert client.get(path + "/my-document").json() == before
    if stream:
        delta_path = f"{TASKS}/{task['id']}/text-deltas"
        replay = client.get(delta_path, params={"after": 1}).json()
        assert replay["complete"] and replay["finalText"] == FIRST + SECOND
        assert [item["content"] for item in replay["deltas"]] == [SECOND]
        resumed = events(
            client.get(f"{TASKS}/{task['id']}/text-events", headers={"Last-Event-ID": "1"}).text
        )
        assert [item["data"]["content"] for item in resumed if item["event"] == "delta"] == [SECOND]
        assert any(item["event"] == "terminal" for item in resumed)
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert_frozen(runtime, request, task, contents, capability)
    assert_wire(runtime, request, contents, stream)


def test_queued_chat_images_keep_saved_capability_credentials_and_wire_after_catalog_change(
    text_image_runtime,
):
    runtime = text_image_runtime
    client, _, _, _, request, contents = text_seed(runtime)
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    current = client.get(WORKSPACE).json()
    channel = next(item for item in current["channels"] if item["id"] == "chat-image-provider")
    channel["apiKey"] = "changed-chat-image-placeholder-key"
    channel["headers"] = [{"name": HEADER, "value": "changed-chat-image-placeholder-header"}]
    channel["modelProfiles"][0]["capabilityConfig"]["text"] = {
        "references": {"maxImages": 0, "maxImageBytes": 0, "promptMaxChars": 1},
        "streaming": False,
    }
    response = client.put(
        WORKSPACE,
        json={
            "expected_row_version": current["row_version"],
            "preferences": current["preferences"],
            "channels": current["channels"],
        },
    )
    assert response.status_code == 200, response.text
    executor(runtime).execute(task["id"], 1)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert_frozen(runtime, request, task, contents, CAPABILITY)
    assert_wire(runtime, request, contents, True)


@pytest.mark.parametrize("failure", ["401", "disconnect"])
def test_chat_image_provider_failure_never_automatically_submits_again(text_image_runtime, failure):
    runtime = text_image_runtime
    runtime.state["mode"] = failure
    client, user, _, path, request, contents = text_seed(runtime)
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    if failure == "disconnect":
        runtime.state["hold"] = True
        with running(runtime, task):
            assert first_delta(client, task)["textDraft"] == FIRST
    else:
        executor(runtime).execute(task["id"], 1)
    result = client.get(f"{TASKS}/{task['id']}").json()
    assert result["status"] == "failed" and result["outputs"] == []
    if failure == "401":
        assert result["errorCode"] == "provider_auth"
    else:
        assert result["canRetry"] is False and result["canResume"] is False
        assert result["textDraft"] == FIRST
        replay = client.get(f"{TASKS}/{task['id']}/text-deltas").json()
        assert replay["complete"] and replay["textDraft"] == FIRST and not replay.get("finalText")
        with runtime.app[1]() as session:
            record = session.scalar(select(AIGenerationRecord))
            assert record.status == "unknown"
        assert client.post(f"{TASKS}/{task['id']}/resume").status_code == 409
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert client.get(path + "/my-document").json() == before
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert_frozen(runtime, request, task, contents, CAPABILITY)
    assert_wire(runtime, request, contents, True)


@pytest.mark.parametrize(
    "boundary,expected_code,status",
    [
        ("undeclared-vision", "reference_limit_exceeded", 422),
        ("image-count", "reference_limit_exceeded", 422),
        ("image-bytes", "reference_images_too_large", 422),
        ("prompt-chars", "canvas_text_prompt_too_long", 422),
        ("unsaved-image", "canvas_reference_not_saved", 422),
        ("responses-protocol", "canvas_generation_option_unsupported", 422),
        ("another-project", "not_found", 404),
        ("other-private-image", "not_found", 404),
    ],
)
def test_chat_image_admission_boundaries_leave_no_task_or_supplier_call(
    text_image_runtime, boundary, expected_code, status
):
    runtime = text_image_runtime
    capability = deepcopy(CAPABILITY)
    protocol = "openai-response" if boundary == "responses-protocol" else "chat-completion"
    if boundary == "undeclared-vision":
        capability = {}
    elif boundary == "image-bytes":
        capability["references"]["maxImageBytes"] = 1
    elif boundary == "prompt-chars":
        capability["references"]["promptMaxChars"] = 1
    client, user, project, path, request, _ = text_seed(
        runtime, capability=capability, protocol=protocol
    )
    if boundary == "image-count":
        extra, _ = saved_image(client, request["projectId"], "green")
        request["input"]["referenceImages"].append(extra)
    elif boundary == "unsaved-image":
        request["input"]["referenceImages"][0]["storageKey"] = "image:local-only"
    elif boundary == "another-project":
        _, _, document = canvas(client, "chat-image-other-project")
        reference, _ = saved_image(client, document["id"], "green")
        request["input"]["referenceImages"][0] = reference
    elif boundary == "other-private-image":
        member, other = account(runtime.app, "canvas_chat_image_private_member")
        join(runtime.app, client, member, project["id"], other["id"])
        reference, _ = saved_image(member, request["projectId"], "green")
        identifier = reference["storageKey"].removeprefix("resource:")
        with runtime.app[1]() as session:
            media = session.get(MediaFile, int(identifier))
            assert media.published_at is None and media.created_by == int(other["id"])
        assert client.get(f"/api/v1/canvas-runtime/resources/{identifier}/file").status_code == 404
        request["input"]["referenceImages"][0] = reference
    before = client.get(path + "/my-document").json()
    response = client.post(TASKS, json=request)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == expected_code
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before
    assert runtime.posts == []
    with runtime.app[1]() as session:
        assert list(session.scalars(select(AsyncTask))) == []
