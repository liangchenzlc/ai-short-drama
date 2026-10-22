"""固定源 hosted Gemini 和 custom gate 的隔离 HTTP/MySQL 证明。"""

import json
import os
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace

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
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    admit,
    bind,
    records,
    seed,
)
from tests.integration.canvas.image.test_canvas_text_image_runtime import executor, running
from tests.integration.canvas.models.test_canvas_model_tests import TESTS, create
from tests.integration.canvas.models.test_canvas_model_tests import request as model_test_request
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated Gemini HTTP/MySQL verification",
    ),
]
WORKSPACE = "/api/v1/canvas-runtime/workspace/model-config"
KEY = "canvas-gemini-synthetic-api-key"
HEADER_VALUE = "canvas-gemini-synthetic-private-header"
FIRST, SECOND = "真实Gemini首段🌈", "，第二段正文。"
REASONING = "仅本人可见的供应商thought"
MODEL = "gemini/fixture:雪 ?#"
WIRE_PATH = "/proxy/v1beta/models/gemini%2Ffixture:%E9%9B%AA%20%3F%23:generateContent"
DEFAULTS = {"generationConfig": {"maxOutputTokens": 456, "temperature": 0.2}}


@pytest.fixture
def gemini_runtime(resource_app):
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
                        "authenticated": self.headers.get("x-goog-api-key") == KEY,
                        "key_count": len(self.headers.get_all("x-goog-api-key", [])),
                        "bearer_absent": self.headers.get("Authorization") is None,
                        "custom_header": self.headers.get("X-Gemini-Fixture") == HEADER_VALUE,
                    }
                )
                first_sent.set()
                if state["hold"] and not finish_allowed.wait(60):
                    raise TimeoutError("Gemini fixture release was not observed")
                if state["mode"] == "401":
                    self.json_response(
                        401, {"error": {"code": 401, "message": "controlled auth failure"}}
                    )
                    return
                if state["mode"] == "supplier-error":
                    self.json_response(
                        200, {"error": {"code": 400, "message": "controlled rejection"}}
                    )
                    return
                if state["mode"] == "safety":
                    self.json_response(200, {"promptFeedback": {"blockReason": "SAFETY"}})
                    return
                if state["mode"] == "invalid-json":
                    self.respond(200, b'{"candidates":')
                    return
                parts = [{"text": REASONING, "thought": True}]
                if state["mode"] != "reasoning-only":
                    parts.extend(
                        [
                            {"text": "  " + FIRST + "  "},
                            {"functionCall": {"name": "unrequested_tool", "args": {}}},
                            {"text": "\n" + SECOND + "\n"},
                        ]
                    )
                self.json_response(
                    200,
                    {
                        "candidates": [
                            {
                                "content": {"role": "model", "parts": parts},
                                "finishReason": "MAX_TOKENS"
                                if state["mode"] == "truncated"
                                else "STOP",
                            },
                            {"content": {"parts": [{"text": "第二候选不能拼入"}]}},
                        ],
                        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
                    },
                )
            except BaseException as error:
                supplier_errors.append(repr(error))
                raise

        def respond(self, status, raw):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

        def json_response(self, status, body):
            self.respond(status, json.dumps(body, ensure_ascii=False).encode())

        def log_message(self, *args):
            return

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
        assert not thread.is_alive() and supplier_errors == [], supplier_errors


def save_gemini_model(
    runtime, client, *, streaming=True, api_format="openai", protocol="gemini-generate-content"
):
    response = client.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "固定源hosted Gemini验证",
            "service_type": "text",
            "provider": "受控Gemini供应商",
            "model_key": MODEL,
            "base_url": runtime.origin + "/proxy/v1",
            "apikey": KEY,
            "headers": [{"name": "X-Gemini-Fixture", "value": HEADER_VALUE}],
            "runtime_profile": {
                "version": 1,
                "api_format": api_format,
                "protocol": protocol,
                "default_options": deepcopy(DEFAULTS),
                "capability_config": {"text": {"streaming": streaming}},
            },
        },
    )
    assert response.status_code == 201, response.text
    model = response.json()
    current = client.get(WORKSPACE).json()
    selection = f"host-{model['id']}::{model['model_key']}"
    selected = client.put(
        WORKSPACE,
        json={
            "expected_row_version": current["row_version"],
            "preferences": {**current["preferences"], "model": selection, "textModel": selection},
        },
    )
    assert selected.status_code == 200, selected.text
    return model


def gemini_seed(
    runtime, *, streaming=True, api_format="openai", protocol="gemini-generate-content"
):
    client, user, project, path, request = seed(runtime.app, "canvas_gemini_author")
    model = save_gemini_model(
        runtime, client, streaming=streaming, api_format=api_format, protocol=protocol
    )
    request["logicalModelId"], request["model"] = model["id"], model["model_key"]
    request["input"]["textHistory"].extend(
        [
            {"role": "system", "content": "历史system不得替代顶层指令"},
            {"role": "assistant", "content": "历史回答"},
        ]
    )
    return client, user, project, path, request


def assert_wire(runtime, request, *, generation_config=None):
    assert len(runtime.posts) == 1
    wire = runtime.posts[0]
    assert wire["path"] == WIRE_PATH
    assert wire["authenticated"] and wire["key_count"] == 1 and wire["bearer_absent"]
    assert wire["custom_header"]
    instructions = request["input"]["config"].get("systemPrompt", "").strip()
    assert wire["body"] == {
        "contents": [
            {
                "role": "model" if item["role"] == "assistant" else "user",
                "parts": [{"text": item["content"]}],
            }
            for item in [
                *request["input"]["textHistory"],
                {"role": "user", "content": request["prompt"]},
            ]
            if item["role"] != "system"
        ],
        "systemInstruction": {"parts": [{"text": instructions}]} if instructions else None,
        "generationConfig": generation_config or DEFAULTS["generationConfig"],
    }


@pytest.mark.parametrize("variant", ["stream-requested", "nonstream", "channel-nonstream"])
def test_hosted_gemini_nonstream_wire_private_result_and_explicit_shared_bind(
    gemini_runtime, variant
):
    runtime = gemini_runtime
    client, user, project, path, request = gemini_seed(
        runtime, streaming=variant != "channel-nonstream"
    )
    request["input"]["textOptions"]["stream"] = variant != "nonstream"
    member, member_user = account(runtime.app, "canvas_gemini_reader")
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    if variant == "stream-requested":
        runtime.state["hold"] = True
        with running(runtime, task):
            assert runtime.first_sent.wait(5), "Actual generateContent request was not sent"
            loading = client.get(f"{TASKS}/{task['id']}/text-deltas").json()
            assert loading["status"] == "running" and not loading["complete"]
            assert (
                loading["textDraft"] == "" and loading["deltas"] == [] and not loading["finalText"]
            )
            assert member.get(f"{TASKS}/{task['id']}/text-deltas").status_code == 404
            assert client.get(path + "/my-document").json() == before
    else:
        executor(runtime).execute(task["id"], 1)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded" and final["resultState"] == "READY", final
    assert json.loads(final["resultJson"]) == {
        "mode": "text",
        "text": FIRST + SECOND,
        "reasoning": REASONING,
    }
    assert client.get(path + "/my-document").json() == before
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert (
            record.adapter == "gemini_generate_content.v1" and record.text_content == FIRST + SECOND
        )
        assert record.config_snapshot["canvas_text_defaults"] == DEFAULTS
        assert record.response_data["canvas_reasoning"] == REASONING
        assert record.response_data["usage"] == {"promptTokenCount": 2, "candidatesTokenCount": 3}
        stored = json.dumps(
            [record.request_data, record.config_snapshot, record.response_data], ensure_ascii=False
        )
        assert KEY not in stored and HEADER_VALUE not in stored
        assert list(session.scalars(select(CanvasTaskTextDelta))) == []
        assert session.scalar(select(CanvasResult)).content_json == {"content": FIRST + SECOND}
    bound = bind(client, request, task)
    assert bound["result"]["node"]["metadata"]["content"] == FIRST + SECOND
    assert member.get(path).status_code == 404
    shared = client.get(path).json()
    assert shared["source_document"]["nodes"][0]["metadata"]["content"] == FIRST + SECOND
    assert REASONING not in json.dumps(shared, ensure_ascii=False)
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert_wire(runtime, request)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("401", "provider_auth"),
        ("supplier-error", "provider_rejected"),
        ("safety", "provider_rejected"),
        ("invalid-json", "invalid_response"),
        ("reasoning-only", "invalid_response"),
        ("truncated", "text_truncated"),
    ],
)
def test_native_gemini_failure_keeps_graph_and_never_reposts(gemini_runtime, failure, code):
    runtime = gemini_runtime
    runtime.state["mode"] = failure
    client, user, _, path, request = gemini_seed(runtime)
    task = admit(client, request)
    before = client.get(path).json()
    executor(runtime).execute(task["id"], 1)
    final = client.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "failed" and final["errorCode"] == code, final
    assert final["outputs"] == [] and not final.get("resultJson")
    assert client.get(path).json() == before
    if failure in {"invalid-json", "reasoning-only"}:
        assert final["canRetry"] is False and final["canResume"] is False
    assert KEY not in json.dumps(final) and REASONING not in (final["textDraft"] or "")
    executor(runtime).execute(task["id"], 1)
    assert len(runtime.posts) == 1 and records(runtime.app, user) == {
        "tasks": 1,
        "records": 1,
        "bindings": 1,
    }


def test_queued_native_gemini_freezes_defaults_and_credentials(gemini_runtime):
    runtime = gemini_runtime
    client, _, _, _, request = gemini_seed(runtime)
    request["input"]["metadata"]["providerOptions"] = {
        "gemini-generate-content": {"generationConfig": {"maxOutputTokens": 789}}
    }
    task = admit(client, request)
    path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    current = client.get(path).json()
    profile = deepcopy(current["runtime_profile"])
    profile["default_options"] = {"generationConfig": {"maxOutputTokens": 100}}
    response = client.patch(
        path,
        json={
            "row_version": current["row_version"],
            "runtime_profile": profile,
            "apikey": "changed-gemini-synthetic-key",
        },
    )
    assert response.status_code == 200, response.text
    executor(runtime).execute(task["id"], 1)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert_wire(runtime, request, generation_config={"maxOutputTokens": 789})
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.config_snapshot["canvas_text_defaults"] == DEFAULTS


@pytest.mark.parametrize("protocol", ["gemini-generate-content", "google-gemini-generate-content"])
def test_custom_gemini_format_keeps_fixed_source_gate_without_task_or_post(
    gemini_runtime, protocol
):
    runtime = gemini_runtime
    client, _, _, _, request = gemini_seed(runtime, api_format="gemini", protocol=protocol)
    rejected = client.post(TASKS, json=request)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_generation_protocol_unsupported"
    assert runtime.posts == []
    with runtime.app[1]() as session:
        assert list(session.scalars(select(AsyncTask))) == []
        assert list(session.scalars(select(AIGenerationRecord))) == []
        assert list(session.scalars(select(CanvasTaskBinding))) == []
        assert session.get(AIModelConfig, int(request["logicalModelId"])).capability_cache is None


@pytest.mark.parametrize("api_format", ["openai", "gemini"])
def test_saved_native_gemini_model_test_uses_server_format_and_never_browser_transport(
    gemini_runtime, api_format
):
    runtime = gemini_runtime
    client, _ = account(runtime.app, "gemini_saved_test")
    model = save_gemini_model(runtime, client, api_format=api_format)
    body = model_test_request(model=MODEL)
    body["channel"].update(
        id=f"host-{model['id']}",
        baseUrl=f"/api/v1/canvas-runtime/ai/models/{model['id']}",
        apiKey="forged-gemini-browser-key",
        apiFormat="gemini" if api_format == "openai" else "openai",
        models=[MODEL],
        modelProfiles=[{"model": MODEL, "capability": "text", "protocol": "plugin/forged"}],
    )
    accepted = create(client, body)
    if api_format == "gemini":
        assert accepted.status_code == 400, accepted.text
        assert accepted.json()["error"]["code"] == "generation_unsupported_protocol"
        assert runtime.posts == []
        return
    assert accepted.status_code == 202, accepted.text
    task_id = accepted.json()["id"]
    other, _ = account(runtime.app, "gemini_test_other")
    assert other.get(f"{TESTS}/{task_id}").status_code == 404
    executor(runtime).execute(task_id, 1)
    final = client.get(f"{TESTS}/{task_id}").json()
    assert final["status"] == "succeeded" and final["result"] == {
        "mode": "text",
        "text": FIRST + SECOND,
    }
    assert create(client, body).json()["id"] == task_id
    assert_wire(runtime, {"input": {"config": {}, "textHistory": []}, "prompt": body["prompt"]})
    with runtime.app[1]() as session:
        assert list(session.scalars(select(CanvasTaskBinding))) == []
        original = session.get(AIModelConfig, int(model["id"]))
        assert (
            original.capability_cache is None and str(original.row_version) == model["row_version"]
        )
        record = session.scalar(select(AIGenerationRecord))
        assert record.adapter == "gemini_generate_content.v1" and record.config_id != original.id
        assert record.response_data["canvas_reasoning"] == REASONING
