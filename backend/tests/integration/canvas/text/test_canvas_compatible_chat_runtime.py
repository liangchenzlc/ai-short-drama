"""新增Chat协议的正式API、随机MySQL与MinIO冻结验证；供应商为本机HTTP。"""

import json
import os
from copy import deepcopy

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, admit
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    FIRST,
    SECOND,
    executor,
    text_seed,
)
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    text_image_runtime as text_image_runtime,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="显式启用隔离兼容Chat HTTP/MySQL/MinIO",
    ),
]


def update_defaults(client, model_id, defaults):
    path = "/api/v1/ai-model-configs/" + model_id
    current = client.get(path).json()
    profile = deepcopy(current["runtime_profile"])
    profile["default_options"] = defaults
    response = client.patch(
        path, json={"row_version": current["row_version"], "runtime_profile": profile}
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("text_image_runtime", ["chat-completion"], indirect=True)
@pytest.mark.parametrize("protocol", ["deepseek-chat", "openrouter-chat"])
def test_alias_freezes_real_saved_model_and_ordered_media_before_config_changes(
    text_image_runtime, protocol
):
    runtime = text_image_runtime
    owner, user, project, path, request, contents = text_seed(runtime, protocol=protocol)
    defaults = {
        "temperature": 0.2,
        "top_p": 0.8,
        "max_tokens": 99,
        "provider": {"order": ["fixture"]},
    }
    update_defaults(owner, request["logicalModelId"], defaults)
    request["input"]["textOptions"] = {"stream": False, "thinking": False}
    request["input"]["metadata"]["providerOptions"] = {protocol: {"top_p": 0.6}}
    shared_before = owner.get(path).json()
    task = admit(owner, request)
    before = owner.get(path + "/my-document").json()
    assert before["source_document"]["nodes"][0]["metadata"]["taskId"] == task["id"]
    update_defaults(owner, request["logicalModelId"], {"top_p": 0.1, "max_tokens": 7})
    executor(runtime).execute(task["id"], 1)
    final = owner.get(f"{TASKS}/{task['id']}").json()
    assert final["status"] == "succeeded" and len(runtime.posts) == 1
    wire = runtime.posts[0]
    assert wire["authenticated"] and wire["custom_header"]
    assert wire["body"]["top_p"] == 0.6 and wire["body"]["temperature"] == 0.2
    assert wire["body"]["max_tokens"] == 99
    assert wire["body"]["provider"] == {"order": ["fixture"]}
    assert [image["bytes"] for image in wire["images"]] == contents
    assert json.loads(final["resultJson"])["text"] == FIRST + SECOND
    assert owner.get(path).json() == shared_before
    assert owner.get(path + "/my-document").json() == before
    member, member_user = account(runtime.app, "compatible_chat_member")
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert member.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).status_code == 404
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.config_snapshot["canvas_text_protocol"] == protocol
        assert record.config_snapshot["canvas_text_defaults"] == defaults
        assert "X-Amz-Signature" not in json.dumps(record.request_data)
        assert "https://never-fetch.example" not in json.dumps(record.request_data)
    assert owner.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert len(runtime.posts) == 1
