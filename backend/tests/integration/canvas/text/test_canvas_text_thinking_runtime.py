"""thinking正式准入、冻结与本机wire；随机MySQL/MinIO，不调用真实模型。"""

import json
import os
from copy import deepcopy

import httpx
import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, CanvasResult
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, admit, records
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    executor,
    saved_image,
    text_seed,
)
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    text_image_runtime as text_image_runtime,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.text.test_canvas_claude_runtime import (
    claude_runtime as claude_runtime,
)
from tests.integration.canvas.text.test_canvas_claude_runtime import claude_seed
from tests.integration.canvas.tools.test_canvas_editor_tools import execute, tool_seed
from tests.integration.canvas.tools.test_canvas_editor_tools import tool_runtime as tool_runtime
from tests.integration.canvas.tools.test_canvas_tool_runs import (
    RUNS,
    approve,
    create_and_claim,
    prepare,
    stage_request,
)
from tests.integration.canvas.tools.test_canvas_tool_runs import art_scope as art_scope
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="显式启用隔离thinking HTTP/MySQL/MinIO",
    ),
]


def set_thinking(client, model_id, enabled, *, tokens=None):
    path = "/api/v1/ai-model-configs/" + model_id
    current = client.get(path).json()
    profile = deepcopy(current["runtime_profile"])
    profile.setdefault("capability_config", {}).setdefault("text", {})["thinking"] = enabled
    if tokens is not None:
        profile["default_options"] = {"max_tokens": tokens}
    changed = client.patch(
        path, json={"row_version": current["row_version"], "runtime_profile": profile}
    )
    assert changed.status_code == 200, changed.text


def test_text_thinking_freezes_saved_capability_before_later_config_change(text_image_runtime):
    runtime = text_image_runtime
    owner, user, _, path, request, _ = text_seed(
        runtime, capability={"thinking": True, "streaming": False}, images=False
    )
    request["input"]["textOptions"] = {"stream": False, "thinking": True}
    task = admit(owner, request)
    before = owner.get(path).json()
    set_thinking(owner, request["logicalModelId"], False)
    executor(runtime).execute(task["id"], 1)
    final = owner.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded" and len(runtime.posts) == 1
    wire = runtime.posts[0]["body"]
    if runtime.protocol == "chat-completion":
        assert wire["reasoning_effort"] == "medium"
    else:
        assert wire["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert owner.get(path).json() == before
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.config_snapshot["canvas_text_capability"]["thinking"] is True
        assert record.request_data["canvas_request"]["input"]["textOptions"]["thinking"] is True
    assert owner.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert len(runtime.posts) == 1 and records(runtime.app, user) == {
        "tasks": 1,
        "records": 1,
        "bindings": 1,
    }


def test_undeclared_text_thinking_rolls_back_every_admission_row(text_image_runtime):
    runtime = text_image_runtime
    owner, user, _, path, request, _ = text_seed(runtime, capability={}, images=False)
    before = owner.get(path + "/my-document").json()
    request["input"]["textOptions"]["thinking"] = True
    rejected = owner.post(TASKS, json=request)
    assert rejected.status_code == 422 and "canvas_generation_option_unsupported" in rejected.text
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert owner.get(path + "/my-document").json() == before and runtime.posts == []


def test_claude_images_and_thinking_freeze_and_deliver_actual_resource_bytes(claude_runtime):
    runtime = claude_runtime
    owner, _, _, _, request = claude_seed(runtime, streaming=False)
    set_thinking(owner, request["logicalModelId"], True, tokens=2048)
    current = owner.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    profile = current["runtime_profile"]
    profile["capability_config"]["text"]["references"] = {
        "maxImages": 2,
        "maxImageBytes": 1024,
        "promptMaxChars": 32000,
    }
    changed = owner.patch(
        "/api/v1/ai-model-configs/" + request["logicalModelId"],
        json={"row_version": current["row_version"], "runtime_profile": profile},
    )
    assert changed.status_code == 200, changed.text
    request["input"]["metadata"]["providerOptions"]["claude-api"]["max_tokens"] = 2048
    request["input"]["textOptions"] = {"stream": False, "thinking": True}
    pairs = [saved_image(owner, request["projectId"], color) for color in ("red", "blue")]
    request["input"]["referenceImages"] = [item[0] for item in pairs]
    task = admit(owner, request)
    set_thinking(owner, request["logicalModelId"], False, tokens=512)
    executor(runtime).execute(task["id"], 1)
    assert owner.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert len(runtime.posts) == 1
    body = runtime.posts[0]["body"]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert body["max_tokens"] == 2048
    content = body["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": request["prompt"]}
    assert len(content) == 3
    with httpx.Client(trust_env=False, timeout=5) as reader:
        for block, (_, expected) in zip(content[1:], pairs, strict=True):
            assert block["type"] == "image" and block["source"]["type"] == "url"
            actual = reader.get(block["source"]["url"])
            assert actual.status_code == 200 and actual.content == expected
            block["source"]["url"] = "verified-resource"
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        frozen = record.request_data
        assert frozen["canvas_text_references"]["references"][0]["mime_type"] == "image/png"
        assert "X-Amz-Signature" not in json.dumps(frozen)
        assert all(
            "url" not in item for item in frozen["canvas_request"]["input"]["referenceImages"]
        )


def test_editor_tool_thinking_uses_saved_protocol_and_keeps_private_results(tool_runtime):
    runtime = tool_runtime
    owner, user, _, path, request, _ = tool_seed(runtime)
    set_thinking(owner, request["logicalModelId"], True)
    request["input"]["textOptions"] = {"stream": False, "thinking": True}
    before = owner.get(path + "/my-document").json()
    if runtime.protocol == "gemini":
        rejected = owner.post(TASKS, json=request)
        assert rejected.status_code == 422 and runtime.posts == []
        assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
        return
    task = admit(owner, request)
    set_thinking(owner, request["logicalModelId"], False)
    execute(runtime, task)
    final = owner.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded" and len(runtime.posts) == 1
    wire = runtime.posts[0]["body"]
    if runtime.protocol == "chat-completion":
        assert wire["reasoning_effort"] == "medium" and "tool_choice" not in wire
    elif runtime.protocol == "openai-response":
        assert wire["reasoning"] == {"effort": "medium", "summary": "auto"}
    else:
        assert wire["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert owner.get(path + "/my-document").json() == before
    with runtime.app[1]() as session:
        assert session.scalar(select(CanvasResult)) is None
    assert owner.post(TASKS, json=request).json()["id"] == task["id"]
    execute(runtime, task)
    assert len(runtime.posts) == 1


@pytest.mark.parametrize("tool_runtime", ["chat-completion"], indirect=True)
def test_thinking_art_stage_prepare_approve_and_execute_use_the_same_capability(art_scope):
    runtime, owner, _, _, _, request = art_scope[:6]
    set_thinking(owner, request["logicalModelId"], True)
    run, guard = create_and_claim(art_scope)
    command = stage_request(art_scope, run)
    command["input"]["textOptions"]["thinking"] = True
    submission = prepare(art_scope, run, guard, request=command)
    assert runtime.posts == []
    approve(art_scope, run, guard, submission)
    accepted = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": submission["id"]}
    )
    assert accepted.status_code == 202, accepted.text
    task = accepted.json()
    execute(runtime, task)
    assert owner.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert runtime.posts[0]["body"]["reasoning_effort"] == "medium"
    assert "tool_choice" not in runtime.posts[0]["body"]
