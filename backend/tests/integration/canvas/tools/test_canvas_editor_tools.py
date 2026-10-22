"""工具本人准入与四协议真实HTTP/MySQL/MinIO；不调用收费供应商。"""

import json
import os
import socket
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationGateway
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskMediaReference,
)
from short_drama.schemas.canvas_tool_contracts import builtin_tool
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    admit,
    records,
    seed,
)
from tests.integration.canvas.resources.test_canvas_resources import png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated editor tool HTTP/MySQL/MinIO verification",
    ),
]
PROTOCOLS = ["chat-completion", "openai-response", "claude-api", "gemini"]
KEY = "canvas-editor-tool-fixture-not-a-real-key"
ARGUMENTS = {
    "optimizedPrompt": "窗边的人物阅读",
    "negativePrompt": "",
    "changes": ["明确主体动作"],
    "assumptions": [],
    "variants": [],
}


ART_STAGES = (
    "analyze_art_scene",
    "review_art_composition",
    "review_art_color",
    "review_art_lighting",
    "review_art_structure",
    "aggregate_art_critique",
    "ground_art_critique_issues",
    "generate_art_edit_prompts",
    "verify_art_critique",
)


def art_arguments(name):
    """固定合法回执只用于协议/恢复验证，不代表供应商审美质量。"""
    candidate = {
        "id": "composition-one",
        "checkId": "visual-balance",
        "kind": "issue",
        "category": "composition",
        "title": "右侧视觉重量偏高",
        "observation": "右侧高对比区域占据了较多注意力。",
        "reason": "会削弱中心主体的阅读顺序。",
        "evidence": ["右侧区域占画面宽度约三分之一"],
        "severity": 0.7,
        "confidence": 0.85,
        "targetDescription": "右侧区域",
    }
    if name == "analyze_art_scene":
        return {
            "scene": {
                "imageType": "still-life",
                "style": ["受控测试图片"],
                "subjects": [{"id": "main", "description": "中心主体", "importance": "primary"}],
                "intendedFocus": "中心主体",
                "compositionType": ["居中"],
                "lightingType": ["均匀"],
                "mood": "安静",
                "estimatedIntent": "展示主体",
                "sceneDepth": "flat",
            }
        }
    if name.startswith("review_art_"):
        return {"candidates": [candidate] if name == "review_art_composition" else []}
    if name == "aggregate_art_critique":
        return {
            "summary": "建议调整右侧视觉重量，保留原图主体。",
            "strengths": ["主体清楚"],
            "issues": [
                {
                    "id": "issue-one",
                    "category": "composition",
                    "title": candidate["title"],
                    "explanation": candidate["reason"],
                    "severity": "medium",
                    "confidence": 0.85,
                    "targetDescription": candidate["targetDescription"],
                    "suggestion": {
                        "goal": "平衡视觉重量",
                        "actions": ["降低右侧对比"],
                        "preserve": ["中心主体"],
                        "expectedEffect": "阅读顺序更清晰",
                    },
                    "sourceCandidateIds": [candidate["id"]],
                }
            ],
            "options": [],
        }
    if name == "ground_art_critique_issues":
        return {
            "targets": [
                {
                    "issueId": "issue-one",
                    "target": {
                        "type": "box",
                        "points": [{"x": 0.6, "y": 0.2}, {"x": 0.9, "y": 0.8}],
                    },
                    "groundingConfidence": 0.9,
                }
            ]
        }
    if name == "generate_art_edit_prompts":
        return {
            "prompts": [{"issueId": "issue-one", "editPrompt": "降低右侧区域对比，保留中心主体。"}]
        }
    assert name == "verify_art_critique", name
    return {
        "decisions": [
            {
                "issueId": "issue-one",
                "verdict": "confirmed",
                "confidence": 0.9,
                "reason": "位置与观察相符",
            }
        ]
    }


def wire_tool_name(protocol, body):
    tools = body["tools"]
    assert len(tools) == 1
    tool = tools[0]
    if protocol == "gemini":
        return tool["functionDeclarations"][0]["name"]
    return tool.get("function", tool)["name"]


def tool_payload(protocol, arguments, name="optimize_prompt"):
    function = {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}
    if protocol == "chat-completion":
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "本人的工具思考",
                        "tool_calls": [{"id": "tool-1", "type": "function", "function": function}],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    if protocol == "openai-response":
        return {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "本人的工具思考"}],
                },
                {"type": "function_call", "call_id": "tool-1", **function},
            ],
        }
    if protocol == "claude-api":
        return {
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "本人的工具思考"},
                {"type": "tool_use", "id": "tool-1", "name": name, "input": arguments},
            ],
        }
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"thought": True, "text": "本人的工具思考"},
                        {
                            "functionCall": {
                                "id": "tool-1",
                                "name": name,
                                "args": arguments,
                            }
                        },
                    ]
                },
            }
        ]
    }


@pytest.fixture(params=PROTOCOLS)
def tool_runtime(resource_app, request):
    app, factory, original_settings = resource_app
    protocol = request.param
    state = {"mode": "success"}
    posts, errors = [], []

    class Supplier(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                header = (
                    "x-api-key"
                    if protocol == "claude-api"
                    else "x-goog-api-key"
                    if protocol == "gemini"
                    else "Authorization"
                )
                expected = KEY if protocol in {"claude-api", "gemini"} else "Bearer " + KEY
                assert self.headers.get(header) == expected
                assert self.headers.get("X-Editor-Tool-Fixture") == "private-header-fixture"
                paths = {
                    "chat-completion": "/v1/chat/completions",
                    "openai-response": "/v1/responses",
                    "claude-api": "/v1/messages",
                    "gemini": "/v1beta/models/saved-model:generateContent",
                }
                assert self.path == paths[protocol]
                wire, images = deepcopy(body), []
                messages = wire.get("messages", wire.get("input", wire.get("contents", [])))
                with httpx.Client(timeout=5, trust_env=False) as client:
                    for message in messages:
                        for part in message.get("parts", message.get("content", [])):
                            if not isinstance(part, dict):
                                continue
                            location = (
                                part.get("image_url") or part.get("fileData") or part.get("source")
                            )
                            if part.get("type") == "input_image":
                                location, field = part, "image_url"
                            elif isinstance(location, dict):
                                field = "fileUri" if "fileUri" in location else "url"
                            else:
                                continue
                            response = client.get(location[field])
                            assert response.status_code == 200
                            images.append(response.content)
                            location[field] = "saved-image"
                name = wire_tool_name(protocol, body)
                posts.append({"path": self.path, "body": wire, "images": images, "name": name})
                if state["mode"] == "disconnect":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                arguments = ARGUMENTS if name == "optimize_prompt" else art_arguments(name)
                if state["mode"] == "invalid":
                    arguments = {**arguments, "invalidField": 7}
                payload = json.dumps(
                    tool_payload(protocol, arguments, name), ensure_ascii=False
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except BaseException as error:
                errors.append(type(error).__name__)
                self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = original_settings.model_copy(update={"generation_allowed_hosts": ["127.0.0.1"]})
    app.state.settings = settings
    runtime = SimpleNamespace(
        app=(app, factory, settings),
        protocol=protocol,
        state=state,
        posts=posts,
        origin=f"http://127.0.0.1:{server.server_port}",
        gateway=GenerationGateway(settings),
    )
    try:
        yield runtime
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        assert errors == [], errors


def tool_seed(runtime, *, image=False):
    client, user, project, path, request = seed(runtime.app, "editor_tool_author")
    saved = client.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "本人编辑器工具模型",
            "service_type": "text",
            "provider": "受控工具供应商",
            "model_key": "saved-model",
            "base_url": runtime.origin,
            "apikey": KEY,
            "headers": [{"name": "X-Editor-Tool-Fixture", "value": "private-header-fixture"}],
            "runtime_profile": {
                "version": 1,
                "api_format": "claude" if runtime.protocol == "claude-api" else "openai",
                "protocol": "gemini-generate-content"
                if runtime.protocol == "gemini"
                else runtime.protocol,
                "capability_config": {
                    "version": 1,
                    "text": {
                        "streaming": False,
                        "references": {
                            "maxImages": 2,
                            "maxImageBytes": 1024,
                            "promptMaxChars": 32000,
                        },
                    },
                },
            },
        },
    )
    assert saved.status_code == 201, saved.text
    request["logicalModelId"] = saved.json()["id"]
    request["input"]["config"] = {}
    request["input"]["textHistory"] = []
    request["input"]["metadata"]["source"] = "prompt-optimizer"
    content = [{"type": "text", "text": "优化当前提示词"}]
    image_bytes = None
    request["input"]["referenceImages"] = []
    if image:
        image_bytes = png("red")
        uploaded = upload(client, image_bytes, source=request["projectId"], key=uuid4().hex)
        assert uploaded.status_code == 200, uploaded.text
        key = "resource:" + uploaded.json()["resource"]["id"]
        request["input"]["referenceImages"].append({"storageKey": key})
        content.append({"type": "image_url", "image_url": {"url": key}})
    request["input"]["agentRequests"] = {
        "canonical": {
            "messages": [{"role": "user", "content": content}],
            "tools": [builtin_tool("optimize_prompt")],
            "toolChoice": {"type": "function", "name": "optimize_prompt"},
            "systemPrompt": "固定工具上下文",
        }
    }
    return client, user, project, path, request, image_bytes


def execute(runtime, task):
    return GenerationExecutionService(
        runtime.app[1], runtime.app[2], runtime.gateway, runtime.app[0].state.storage
    ).execute(task["id"], 1)


@pytest.mark.parametrize("image", [False, True])
def test_real_tool_only_results_remain_private_and_never_change_or_bind_the_source(
    tool_runtime, image
):
    runtime = tool_runtime
    owner, user, project, path, request, image_bytes = tool_seed(runtime, image=image)
    before = owner.get(path + "/my-document").json()
    task = admit(owner, request)
    assert owner.get(path + "/my-document").json() == before
    replay = owner.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    execute(runtime, task)
    completed = owner.get(f"{TASKS}/{task['id']}")
    assert completed.status_code == 200, completed.text
    completed = completed.json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    result = json.loads(completed["resultJson"])
    assert result["text"] == "" and result["reasoning"] == "本人的工具思考"
    assert json.loads(result["toolCalls"][0]["function"]["arguments"]) == ARGUMENTS
    assert completed["outputs"] == [] and len(runtime.posts) == 1
    assert runtime.posts[0]["images"] == ([image_bytes] if image else [])
    assert owner.get(path + "/my-document").json() == before
    with runtime.app[1]() as session:
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert call.status == "succeeded" and call.text_content == ""
        assert call.request_data["source"]["scene"] == "canvas_tool"
        assert call.config_snapshot["canvas_auth_scene"] == "canvas_tool"
        assert call.response_data["canvas_tool_reply"]["toolCalls"]
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasTaskMediaReference)) == int(
            image
        )
        frozen = json.dumps(call.request_data) + json.dumps(call.response_data)
        assert KEY not in frozen and "X-Amz-Signature" not in frozen
    operation = {
        "opId": "tool-bind-" + uuid4().hex,
        "params": {
            "canvasId": request["projectId"],
            "nodeId": "runtime-node",
            "taskId": task["id"],
            "outputIndex": 0,
        },
    }
    refused = owner.post("/api/v1/canvas-runtime/ops/canvas.task.bind", json=operation)
    assert refused.status_code == 422, refused.text
    assert "canvas_tool_binding_unsupported" in refused.text
    assert owner.get(path + "/my-document").json() == before
    member, member_user = account(runtime.app, "editor_tool_member")
    outsider, outsider_user = account(runtime.app, "editor_tool_outsider")
    for client, identity in ((member, member_user), (outsider, outsider_user)):
        client.headers["X-Canvas-Actor"] = identity["id"]
        assert client.get(f"{TASKS}/{task['id']}").status_code == 404
        assert client.get(f"{TASKS}/{task['id']}/logs").status_code == 404
        assert client.get(f"{TASKS}/{task['id']}/text-events").status_code == 404
    assert member.get(path).status_code == 404
    assert "本人的工具思考" not in member.get(path).text
    assert "窗边的人物阅读" not in member.get(path).text
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("mode", ["invalid", "disconnect"])
def test_invalid_or_uncertain_tool_reply_is_retained_unknown_without_resubmission(
    tool_runtime, mode
):
    runtime = tool_runtime
    owner, _user, _project, path, request, _image = tool_seed(runtime)
    before = owner.get(path + "/my-document").json()
    runtime.state["mode"] = mode
    task = admit(owner, request)
    execute(runtime, task)
    failed = owner.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and not failed["canRetry"] and not failed["canResume"]
    assert failed["resultState"] == "NOT_AVAILABLE" and len(runtime.posts) == 1
    assert owner.post(f"{TASKS}/{task['id']}/resume").status_code == 409
    assert owner.post(TASKS, json=request).json()["id"] == task["id"]
    execute(runtime, task)
    assert len(runtime.posts) == 1
    assert owner.get(path + "/my-document").json() == before
    with runtime.app[1]() as session:
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert call.status == "unknown"
        assert "canvas_tool_reply" not in (call.response_data or {})
        assert session.get(AsyncTask, int(task["id"])).status == "failed"


def test_art_critique_stage_cannot_bypass_the_persistent_approval_guard(tool_runtime):
    runtime = tool_runtime
    owner, user, _project, path, request, _image = tool_seed(runtime)
    before = owner.get(path + "/my-document").json()
    name = "review_art_composition"
    canonical = request["input"]["agentRequests"]["canonical"]
    canonical["tools"] = [builtin_tool(name)]
    canonical["toolChoice"]["name"] = name
    request["input"]["metadata"].update(source="art-critique", stage=name)
    refused = owner.post(TASKS, json=request)
    assert refused.status_code == 409 and "canvas_tool_approval_required" in refused.text
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert runtime.posts == [] and owner.get(path + "/my-document").json() == before
