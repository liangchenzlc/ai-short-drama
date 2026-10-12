"""普通 Claude 真实本机 HTTP/MySQL 任务和本人隔离；不请求收费模型。"""

import json
import os
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskTextDelta,
)
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, records, seed
from tests.integration.test_canvas_model_tests import TESTS, create
from tests.integration.test_canvas_model_tests import request as model_test_request
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_text_image_runtime import executor, running
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated Claude HTTP/MySQL and canvas runtime verification",
    ),
]
WORKSPACE = "/api/v1/canvas-runtime/workspace/model-config"
KEY = "canvas-claude-synthetic-api-key"
HEADER_VALUE = "canvas-claude-synthetic-private-header"
FIRST, SECOND = "真实Claude首段🌈", "，第二段正文。"
REASONING = "仅本人可见的Claude推理"
DEFAULTS = {"max_tokens": 456, "temperature": 0.2, "system": "供应商默认系统指令"}


@pytest.fixture
def claude_runtime(resource_app):
    app, factory, original_settings = resource_app
    state = {"mode": "success", "hold": False}
    posts, supplier_errors = [], []
    first_sent, finish_allowed = Event(), Event()

    class Supplier(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                posts.append(
                    {
                        "path": self.path,
                        "body": deepcopy(body),
                        "authenticated": self.headers.get("x-api-key") == KEY,
                        "bearer_absent": self.headers.get("Authorization") is None,
                        "version": self.headers.get("anthropic-version"),
                        "version_count": len(self.headers.get_all("anthropic-version", [])),
                        "custom_header": self.headers.get("X-Claude-Fixture") == HEADER_VALUE,
                    }
                )
                if state["mode"] == "401":
                    self.json_response(401, {"error": {"message": "controlled auth failure"}})
                    return
                if not body["stream"] or state["mode"] == "json_fallback":
                    self.json_response(
                        200,
                        {
                            "content": [
                                {"type": "text", "text": "  " + FIRST + "  "},
                                {"type": "thinking", "thinking": REASONING},
                                {"type": "text", "text": "\n" + SECOND + "\n"},
                            ],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 2, "output_tokens": 3},
                        },
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                self.chunk({"type": "message_start", "message": {"usage": {"input_tokens": 2}}})
                self.chunk(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "thinking", "thinking": REASONING},
                    }
                )
                if state["mode"] == "reasoning_only":
                    self.finish_stream()
                    return
                self.chunk(
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {"type": "text", "text": ""},
                    }
                )
                self.chunk(
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "text_delta", "text": FIRST},
                    },
                    split_utf8=True,
                )
                first_sent.set()
                if state["hold"]:
                    assert finish_allowed.wait(25), "Test did not release the Claude stream"
                if state["mode"] == "invalid_sse":
                    self.raw_chunk(b"data: {bad}\n\n")
                    self.finish_stream()
                    return
                if state["mode"] == "sse_error":
                    self.chunk(
                        {"type": "error", "error": {"type": "overloaded_error", "message": KEY}}
                    )
                    self.finish_stream()
                    return
                Event().wait(0.14)
                self.chunk(
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "text_delta", "text": SECOND},
                    }
                )
                self.chunk(
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": "max_tokens"
                            if state["mode"] == "truncated"
                            else "end_turn"
                        },
                        "usage": {"output_tokens": 3},
                    }
                )
                # 固定源允许有效正文 EOF，不人为增加 message_stop 前提。
                self.finish_stream()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
            except BaseException as error:
                supplier_errors.append(type(error).__name__)
                self.close_connection = True

        def json_response(self, status, body):
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(payload)

        def raw_chunk(self, payload):
            self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def chunk(self, body, *, split_utf8=False):
            payload = ("data: " + json.dumps(body, ensure_ascii=False) + "\r\n\r\n").encode()
            if split_utf8:
                point = payload.index("真".encode()) + 1
                self.raw_chunk(payload[:point])
                self.raw_chunk(payload[point:])
            else:
                self.raw_chunk(payload)

        def finish_stream(self):
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = original_settings.model_copy(update={"generation_allowed_hosts": ["127.0.0.1"]})
    app.state.settings = settings
    runtime = SimpleNamespace(
        app=(app, factory, settings),
        origin=f"http://127.0.0.1:{server.server_port}",
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
        thread.join(5)
        assert not thread.is_alive(), "Task-owned Claude server did not stop"
        assert supplier_errors == [], supplier_errors


def save_claude_model(runtime, client, *, streaming=True):
    response = client.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "普通Claude协议验证",
            "service_type": "text",
            "provider": "受控Claude供应商",
            "model_key": "claude-fixture",
            "base_url": runtime.origin + "/proxy/v1",
            "apikey": KEY,
            "headers": [
                {"name": "X-Claude-Fixture", "value": HEADER_VALUE},
                {"name": "Anthropic-Version", "value": "2024-03-01"},
            ],
            "runtime_profile": {
                "version": 1,
                "api_format": "claude",
                "protocol": "claude-api",
                "default_options": deepcopy(DEFAULTS),
                "capability_config": {"text": {"streaming": streaming}},
            },
        },
    )
    assert response.status_code == 201, response.text
    model = response.json()
    current = client.get(WORKSPACE).json()
    selection = f"host-{model['id']}::{model['model_key']}"
    response = client.put(
        WORKSPACE,
        json={
            "expected_row_version": current["row_version"],
            "preferences": {
                **current["preferences"],
                "model": selection,
                "textModel": selection,
            },
        },
    )
    assert response.status_code == 200, response.text
    return model


def claude_seed(runtime, *, streaming=True):
    client, user, project, path, request = seed(runtime.app, "canvas_claude_author")
    model = save_claude_model(runtime, client, streaming=streaming)
    request["logicalModelId"], request["model"] = model["id"], model["model_key"]
    request["input"]["textHistory"].extend(
        [
            {"role": "system", "content": "历史系统内容不拼进独立指令"},
            {"role": "assistant", "content": "上一条回答"},
        ]
    )
    request["input"]["textOptions"]["stream"] = True
    request["input"]["metadata"]["providerOptions"] = {"claude-api": {"max_tokens": 789}}
    return client, user, project, path, request


def wait_first_delta(client, task):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"{TASKS}/{task['id']}/text-deltas")
        assert response.status_code == 200, response.text
        if response.json()["textDraft"] == FIRST:
            return response.json()
        Event().wait(0.03)
    pytest.fail("Actual Claude first chunk was not persisted before terminal response")


def assert_claude_wire(runtime, request, *, stream):
    assert len(runtime.posts) == 1
    evidence = runtime.posts[0]
    assert evidence["path"] == "/proxy/v1/messages"
    assert evidence["authenticated"] and evidence["bearer_absent"] and evidence["custom_header"]
    assert evidence["version"] == "2024-03-01" and evidence["version_count"] == 1
    body = evidence["body"]
    assert body["stream"] is stream and body["model"] == "claude-fixture"
    assert body["max_tokens"] == 789 and body["temperature"] == 0.2
    assert body["system"] == request["input"]["config"]["systemPrompt"]
    assert body["messages"] == [
        *[item for item in request["input"]["textHistory"] if item["role"] != "system"],
        {"role": "user", "content": request["prompt"]},
    ]
    assert "thinking" not in body and "tools" not in body and "stream_options" not in body


@pytest.mark.parametrize("variant", ["stream", "nonstream", "channel-nonstream", "json-fallback"])
def test_plain_claude_wire_private_delta_reasoning_and_explicit_shared_bind(
    claude_runtime, variant
):
    runtime = claude_runtime
    client, user, project, path, request = claude_seed(
        runtime, streaming=variant != "channel-nonstream"
    )
    request["input"]["textOptions"]["stream"] = variant != "nonstream"
    if variant == "json-fallback":
        runtime.state["mode"] = "json_fallback"
    member, member_user = account(runtime.app, "canvas_claude_reader")
    join(runtime.app, client, member, project["id"], member_user["id"])
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    if variant == "stream":
        runtime.state["hold"] = True
        with running(runtime, task):
            first = wait_first_delta(client, task)
            assert first["status"] == "running" and first["complete"] is False
            assert not first["finalText"] and REASONING not in json.dumps(first, ensure_ascii=False)
            assert member.get(f"{TASKS}/{task['id']}/text-deltas").status_code == 404
            assert client.get(path + "/my-document").json() == before
    else:
        executor(runtime).execute(task["id"], 1)
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    final = response.json()
    assert final["status"] == "succeeded" and final["resultState"] == "READY"
    assert json.loads(final["resultJson"]) == {
        "mode": "text",
        "text": FIRST + SECOND,
        "reasoning": REASONING,
    }
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert client.get(path + "/my-document").json() == before
    assert REASONING not in json.dumps(member.get(path).json(), ensure_ascii=False)
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.adapter == "claude_messages.v1" and record.text_content == FIRST + SECOND
        assert record.response_data["canvas_reasoning"] == REASONING
        assert record.config_snapshot["canvas_text_defaults"] == DEFAULTS
        assert record.response_data["usage"] == {"input_tokens": 2, "output_tokens": 3}
        serialized = json.dumps(
            [record.request_data, record.config_snapshot, record.response_data], ensure_ascii=False
        )
        assert KEY not in serialized and HEADER_VALUE not in serialized
        result = session.scalar(select(CanvasResult))
        assert (
            result.content_json == {"content": FIRST + SECOND}
            and result.attachment_status == "detached"
        )
        deltas = list(
            session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
        )
        if variant == "stream":
            assert "".join(item.content for item in deltas) == FIRST + SECOND
            assert all(item.created_by == int(user["id"]) for item in deltas)
        elif variant in {"nonstream", "channel-nonstream", "json-fallback"}:
            assert deltas == []
    bound = bind(client, request, task)
    assert bound["result"]["node"]["metadata"]["content"] == FIRST + SECOND
    shared = member.get(path).json()
    assert shared["source_document"]["nodes"][0]["metadata"]["content"] == FIRST + SECOND
    assert REASONING not in json.dumps(shared, ensure_ascii=False)
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert_claude_wire(runtime, request, stream=variant not in {"nonstream", "channel-nonstream"})


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("401", "provider_auth"),
        ("invalid_sse", "invalid_response"),
        ("sse_error", "provider_rejected"),
        ("reasoning_only", "invalid_response"),
        ("truncated", "text_truncated"),
    ],
)
def test_claude_failure_preserves_private_draft_and_never_resubmits_automatically(
    claude_runtime, failure, code
):
    runtime = claude_runtime
    runtime.state["mode"] = failure
    client, user, _, path, request = claude_seed(runtime)
    task = admit(client, request)
    shared_before = client.get(path).json()
    executor(runtime).execute(task["id"], 1)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "failed" and final["errorCode"] == code
    assert final["outputs"] == [] and not final.get("resultJson")
    assert client.get(path).json() == shared_before
    if failure in {"invalid_sse", "reasoning_only"}:
        assert final["canRetry"] is False and final["canResume"] is False
    if failure in {"invalid_sse", "sse_error", "truncated"}:
        assert final["textDraft"].startswith(FIRST)
    assert KEY not in json.dumps(final) and REASONING not in (final["textDraft"] or "")
    executor(runtime).execute(task["id"], 1)
    assert len(runtime.posts) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


def test_queued_claude_freezes_old_defaults_capability_and_credentials_after_model_change(
    claude_runtime,
):
    runtime = claude_runtime
    client, _, _, _, request = claude_seed(runtime)
    task = admit(client, request)
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    current = client.get(model_path).json()
    changed = deepcopy(current["runtime_profile"])
    changed["default_options"] = {"max_tokens": 100, "temperature": 0.9}
    changed["capability_config"]["text"]["streaming"] = False
    response = client.patch(
        model_path,
        json={
            "row_version": current["row_version"],
            "runtime_profile": changed,
            "apikey": "changed-claude-synthetic-key",
            "headers": [{"name": "X-Claude-Fixture", "value": "changed-synthetic-header"}],
        },
    )
    assert response.status_code == 200, response.text
    executor(runtime).execute(task["id"], 1)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded", final
    assert_claude_wire(runtime, request, stream=True)
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.config_snapshot["canvas_text_defaults"] == DEFAULTS
        assert record.config_snapshot["canvas_text_capability"]["streaming"] is True


@pytest.mark.parametrize("mode", ["nonstream", "channel-nonstream", "401"])
def test_saved_host_claude_model_test_uses_frozen_private_config_without_canvas_binding(
    claude_runtime, mode
):
    runtime = claude_runtime
    client, user = account(runtime.app, "claude_saved_test")
    model = save_claude_model(runtime, client, streaming=mode != "channel-nonstream")
    before = client.get(WORKSPACE).json()
    body = model_test_request(model=model["model_key"])
    body["channel"].update(
        id=f"host-{model['id']}",
        baseUrl=f"/api/v1/canvas-runtime/ai/models/{model['id']}",
        apiKey="forged-browser-claude-key",
        apiFormat="openai",
        headers=[{"name": "X-Claude-Fixture", "value": "forged-browser-header"}],
        models=[model["model_key"]],
        modelProfiles=[
            {"model": model["model_key"], "capability": "text", "protocol": "plugin/forged"}
        ],
    )
    body["config"] = {"systemPrompt": "独立测试本次系统指令"}
    if mode == "nonstream":
        streaming_request = deepcopy(body)
        streaming_request["textOptions"]["stream"] = True
        streaming_request["clientOperationId"] = uuid4().hex
        rejected = create(client, streaming_request)
        assert rejected.status_code == 422, rejected.text
        assert runtime.posts == []
    body["textOptions"]["stream"] = False
    task = create(client, body)
    assert task.status_code == 202, task.text
    identifier = task.json()["id"]
    other, _ = account(runtime.app, "claude_saved_test_other")
    assert other.get(f"{TESTS}/{identifier}").status_code == 404
    foreign_body = deepcopy(body)
    foreign_body["clientOperationId"] = uuid4().hex
    assert create(other, foreign_body).status_code == 404
    if mode == "401":
        runtime.state["mode"] = "401"
    executor(runtime).execute(identifier, 1)
    final = client.get(f"{TESTS}/{identifier}")
    assert final.status_code == 200, final.text
    if mode == "401":
        assert final.json()["status"] == "failed"
        assert final.json()["errorCode"] == "provider_auth"
        assert isinstance(final.json()["error"], str) and final.json()["error"]
        assert final.json().get("result") is None
    else:
        assert final.json()["status"] == "succeeded", final.text
        assert final.json()["result"] == {"mode": "text", "text": FIRST + SECOND}
    assert create(client, body).json()["id"] == identifier
    executor(runtime).execute(identifier, 1)
    assert len(runtime.posts) == 1
    wire = runtime.posts[0]
    assert wire["path"] == "/proxy/v1/messages"
    assert wire["authenticated"] and wire["custom_header"] and wire["bearer_absent"]
    assert wire["version"] == "2024-03-01" and wire["version_count"] == 1
    assert wire["body"] == {
        "model": model["model_key"],
        "messages": [{"role": "user", "content": body["prompt"]}],
        "system": body["config"]["systemPrompt"],
        "max_tokens": DEFAULTS["max_tokens"],
        "temperature": DEFAULTS["temperature"],
        "stream": False,
    }
    assert client.get(WORKSPACE).json() == before
    with runtime.app[1]() as session:
        original = session.get(AIModelConfig, int(model["id"]))
        assert original.enabled and not original.is_deleted and original.capability_cache is None
        assert str(original.row_version) == model["row_version"]
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(identifier))
        )
        task_row = session.get(AsyncTask, int(identifier))
        assert task_row.project_id is None and task_row.initiated_by == int(user["id"])
        assert record.config_id != original.id and record.adapter == "claude_messages.v1"
        shadow = session.get(AIModelConfig, record.config_id)
        assert shadow.is_deleted and not shadow.enabled
        assert record.config_snapshot["canvas_text_defaults"] == DEFAULTS
        assert record.config_snapshot["canvas_auth_scene"] == "canvas_model_test"
        if mode != "401":
            assert record.response_data["canvas_reasoning"] == REASONING
        assert list(session.scalars(select(CanvasTaskBinding))) == []
        assert list(session.scalars(select(CanvasResult))) == []
        stored = json.dumps(
            [record.request_data, record.config_snapshot, record.response_data], ensure_ascii=False
        )
        assert KEY not in stored and HEADER_VALUE not in stored and "forged-browser" not in stored
