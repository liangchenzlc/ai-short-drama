"""分镜准入与模型结构必须在服务器验证，回放不重建镜头身份。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload
from short_drama.service.canvas_task_outputs import durable_output, generation_metadata


def request_body():
    return {
        "projectId": "canvas-key",
        "type": "canvas_text",
        "operation": "storyboard",
        "prompt": "在车站道别。",
        "logicalModelId": "17",
        "input": {
            "mode": "text",
            "prompt": "在车站道别。",
            "metadata": {
                "nodeId": "script",
                "sourceNodeId": "script",
                "clientOperationId": "storyboard:one",
            },
            "canvasAssets": [],
            "requirements": "输出可编辑分镜。",
            "projectStyle": {"presetId": "cinematic", "title": "电影", "prompt": "低饱和"},
            "characters": [],
            "shotCount": 2,
            "shotDurationSeconds": 5,
        },
    }


def reply_body():
    return {
        "title": "车站道别",
        "rows": [
            {"shotNumber": i, "durationSeconds": 5, "plotDescription": text, "characters": []}
            for i, text in enumerate(("列车进站。", "朋友挥手。"), 1)
        ],
    }


def test_storyboard_admission_compiles_frozen_style_and_json_contract():
    request = CanvasRuntimeTaskCreate.model_validate(request_body())
    payload = generation_payload(request, 2, "openai_chat.v1")
    messages = payload["input"]["messages"]
    assert messages[-1]["content"] == request.prompt
    assert any("低饱和" in item["content"] and '"rows"' in item["content"] for item in messages)
    assert request.input.shot_count == 2 and request.input.shot_duration_seconds == 5


def test_storyboard_prompt_binding_example_is_valid_and_keeps_frozen_character_identity():
    from short_drama.service.canvas_storyboard import (
        canvas_storyboard_result,
        storyboard_system_prompt,
    )

    request = request_body()
    request["input"]["characters"] = [
        {"assetId": "31", "versionId": "41", "name": "阿青", "definition": {}}
    ]
    request["input"]["canvasAssets"] = [
        {
            "id": "character-one",
            "title": "阿青",
            "type": "character",
            "tags": [],
            "prompt": "蓝色连衣裙",
            "characterAssetId": "31",
            "characterVersionId": "41",
        }
    ]
    prompt = storyboard_system_prompt(request)
    example = next(
        line.removeprefix("assetBindings对象示例：")
        for line in prompt.splitlines()
        if line.startswith("assetBindings对象示例：")
    )
    binding = json.loads(example)["assetBindings"][0]
    binding["nodeId"] = request["input"]["canvasAssets"][0]["id"]
    reply = reply_body()
    for row in reply["rows"]:
        row["characters"] = ["阿青"]
        row["assetBindings"] = [deepcopy(binding)]
    result = canvas_storyboard_result(json.dumps(reply), request, "100")
    for row in result["rows"]:
        assert row["assetBindings"] == [binding]
        assert row["characters"] == [
            {"characterName": "阿青", "characterAssetId": "31", "characterVersionId": "41"}
        ]


@pytest.mark.parametrize(
    "binding",
    [
        "character-one",
        {"nodeId": "character-one"},
        {"nodeId": "character-one", "role": "person"},
        {"nodeId": "character-one", "role": "character", "priority": True},
        {"nodeId": "character-one", "role": "character", "priority": 101},
    ],
)
def test_storyboard_result_rejects_malformed_binding_for_known_asset(binding):
    from short_drama.ai.types import GenerationError
    from short_drama.service.canvas_storyboard import canvas_storyboard_result

    request = request_body()
    request["input"]["canvasAssets"] = [{"id": "character-one"}]
    reply = reply_body()
    reply["rows"][0]["assetBindings"] = [binding]
    with pytest.raises(GenerationError, match="invalid_storyboard_result"):
        canvas_storyboard_result(json.dumps(reply), request, "100")


@pytest.mark.parametrize(
    "change",
    [
        {"shotCount": True},
        {"shotCount": 101},
        {"shotDurationSeconds": 61},
        {"projectStyle": None},
        {"referenceImages": [{"storageKey": "resource:31"}]},
        {"textHistory": [{"role": "user", "content": "another"}]},
    ],
)
def test_storyboard_input_rejects_incoherent_context(change):
    body = request_body()
    body["input"].update(change)
    with pytest.raises(ValidationError):
        CanvasRuntimeTaskCreate.model_validate(body)


def test_plain_text_cannot_smuggle_storyboard_context():
    body = request_body()
    body["operation"] = "text"
    with pytest.raises(ValidationError):
        CanvasRuntimeTaskCreate.model_validate(body)


def test_structured_delivery_keeps_task_derived_ids_and_character_objects():
    record = SimpleNamespace(
        text_content=json.dumps(reply_body(), ensure_ascii=False),
        request_data={"canvas_request": request_body()},
    )
    args = (None, None, SimpleNamespace(id=18446744073709551614, service_type="text"), record, 0)
    first = durable_output(*args)
    assert first == durable_output(*args)
    assert first[0] == "text" and first[2] is None
    rows = first[1]["storyboard"]["rows"]
    assert [row["id"] for row in rows] == [
        "shot:18446744073709551614:1",
        "shot:18446744073709551614:2",
    ]
    assert all(row["characters"] == [] and row["status"] == "idle" for row in rows)
    original = {"storyboard": {"rows": [], "visibleColumns": ["camera"], "referenceNodeIds": []}}
    result = SimpleNamespace(kind="text", content_json=first[1])
    metadata = generation_metadata(original, result, "18446744073709551614", "attach-one")
    assert metadata["storyboard"]["rows"] == rows
    assert "camera" in metadata["storyboard"]["visibleColumns"]
    assert original["storyboard"]["rows"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "count",
        "duration",
        "shot_number",
        "character",
        "binding",
        "media",
        "boolean",
        "blank_title",
        "blank_description",
    ],
)
def test_storyboard_result_rejects_untrusted_or_incomplete_rows(mutation):
    from short_drama.service.canvas_storyboard import canvas_storyboard_result

    body = deepcopy(reply_body())
    if mutation == "empty":
        body["rows"] = []
    elif mutation == "count":
        body["rows"].pop()
    elif mutation == "duration":
        body["rows"][0]["durationSeconds"] = 6
    elif mutation == "shot_number":
        body["rows"][0]["shotNumber"] = 2
    elif mutation == "character":
        body["rows"][0]["characters"] = ["陌生角色"]
    elif mutation == "binding":
        body["rows"][0]["assetBindings"] = [{"nodeId": "other-project", "role": "prop"}]
    elif mutation == "media":
        body["rows"][0]["imageNodeId"] = "forged"
    elif mutation == "blank_title":
        body["title"] = "  "
    elif mutation == "blank_description":
        body["rows"][0]["plotDescription"] = "\n "
    else:
        body["rows"][0]["durationSeconds"] = True
    from short_drama.ai.types import GenerationError

    with pytest.raises(GenerationError, match="invalid_storyboard_result"):
        canvas_storyboard_result(json.dumps(body), request_body(), "17")


def test_storyboard_result_does_not_accept_markdown_or_explanatory_text():
    from short_drama.ai.types import GenerationError
    from short_drama.service.canvas_storyboard import canvas_storyboard_result

    for text in (
        "```json\n" + json.dumps(reply_body()) + "\n```",
        "answer: " + json.dumps(reply_body()),
    ):
        with pytest.raises(GenerationError, match="invalid_storyboard_result"):
            canvas_storyboard_result(text, request_body(), "17")
