"""编辑器工具准入的闭合身份与永久引用；普通文字多模态范围保持原边界。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_canvas_generation_runtime_contract import request
from test_canvas_text_references import admission_fixture
from test_canvas_tool_contracts import ADAPTERS, canonical

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload
from short_drama.service.canvas_text_admission import freeze_text_references


def tool_request(*, image=False):
    body = request()
    body["input"]["metadata"].update(source="prompt-optimizer", sourceNodeId="target")
    body["input"]["agentRequests"] = {"canonical": canonical(image=image)}
    if image:
        body["input"]["referenceImages"] = [{"storageKey": "resource:9007199254740997"}]
    return body


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("image", [False, True])
def test_tool_admission_uses_a_standard_placeholder_and_only_trusted_transform(adapter, image):
    body = tool_request(image=image)
    before = deepcopy(body)
    parsed = CanvasRuntimeTaskCreate.model_validate(body)
    result = generation_payload(parsed, 1, adapter)
    assert parsed.input.agent_requests.canonical.tools[0].function.name == "optimize_prompt"
    assert result["input"] == {"messages": [{"role": "user", "content": body["prompt"]}]}
    assert "canvas_tool_canonical" not in result
    assert body == before


@pytest.mark.parametrize(
    "change",
    [
        "media_mode",
        "operation",
        "history",
        "thinking",
        "source",
        "target",
        "transport",
        "schema",
        "missing_image",
        "extra_image",
        "image_order",
        "config",
    ],
)
def test_incoherent_tool_scope_or_client_transport_is_rejected_by_public_schema(change):
    body = tool_request(image=True)
    inputs = body["input"]
    if change == "media_mode":
        inputs["mode"] = "image"
        body["type"] = "canvas_image"
    elif change == "operation":
        body["operation"] = "edit"
    elif change == "history":
        inputs["textHistory"] = [{"role": "assistant", "content": "旧助手历史"}]
    elif change == "thinking":
        inputs["textOptions"] = {"thinking": "true"}
    elif change == "source":
        inputs["metadata"]["source"] = "canvas-online-agent"
    elif change == "target":
        inputs["metadata"]["sourceNodeId"] = "another-node"
    elif change == "transport":
        inputs["agentRequests"]["chatCompletion"] = {"baseUrl": "https://client.invalid"}
    elif change == "schema":
        inputs["agentRequests"]["canonical"]["tools"][0]["function"]["parameters"] = {}
    elif change == "missing_image":
        inputs["referenceImages"] = []
    elif change == "extra_image":
        inputs["referenceImages"].append({"storageKey": "resource:9007199254740999"})
    elif change == "image_order":
        inputs["agentRequests"]["canonical"]["messages"][0]["content"].append(
            {"type": "image_url", "image_url": {"url": "resource:9007199254740999"}}
        )
        inputs["referenceImages"].insert(0, {"storageKey": "resource:9007199254740999"})
    else:
        inputs["config"] = {"systemPrompt": "与冻结canonical不一致"}
    with pytest.raises(ValidationError):
        CanvasRuntimeTaskCreate.model_validate(body)


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("image", [False, True])
def test_tool_freeze_always_records_closed_envelope_and_authoritative_image_metadata(
    monkeypatch, adapter, image
):
    generation, _media, _prepared, _cache = admission_fixture(monkeypatch)
    body = tool_request(image=image)
    prepared = {"input": {}, "canvas_request": body}
    before = deepcopy(body)
    freeze_text_references(
        generation,
        prepared,
        1,
        adapter,
        {"canvas_text_capability": {"references": {"maxImages": 2, "maxImageBytes": 1024}}},
    )
    assert prepared["canvas_text_references"] == {
        "mode": "text",
        "references": [{"media_id": "9007199254740997", "mime_type": "image/png", "byte_size": 90}]
        if image
        else [],
    }
    assert prepared["input"]["reference_media_ids"] == (["9007199254740997"] if image else [])
    if image:
        frozen = prepared["canvas_request"]["input"]["referenceImages"][0]
        assert frozen["width"] == 37 and frozen["height"] == 19
        assert frozen["bytes"] == 90 and "url" not in frozen
    assert before["input"]["agentRequests"] == prepared["canvas_request"]["input"]["agentRequests"]


@pytest.mark.parametrize("adapter", [None, "unsupported.v1", "openai_images.v1"])
def test_tool_admission_requires_an_implemented_saved_text_protocol(adapter):
    with pytest.raises(WorkflowError):
        generation_payload(CanvasRuntimeTaskCreate.model_validate(tool_request()), 1, adapter)
