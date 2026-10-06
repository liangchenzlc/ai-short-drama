"""画布文字图片准入与源 Chat 消息合同；标准文本保持闭合。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_canvas_generation_runtime_contract import request
from test_generation_adapters import gateway as gateway
from test_generation_adapters import provider as provider

from short_drama.ai import GenerationError
from short_drama.ai.adapters import build_submission
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload
from short_drama.service.canvas_text_admission import freeze_text_references


def text_request():
    body = request(config={"systemPrompt": "请按顺序说明图片"})
    body["input"]["referenceImages"] = [
        {"storageKey": "resource:9007199254740997", "type": "image/png"},
        {"storageKey": "resource:9007199254740999", "type": "image/jpeg"},
    ]
    return body


def prepared_text():
    body = text_request()
    return {
        "input": {
            "messages": [
                {"role": "system", "content": body["input"]["config"]["systemPrompt"]},
                {"role": "user", "content": body["prompt"]},
            ],
            "reference_media_ids": ["9007199254740997", "9007199254740999"],
            "reference_urls": [
                "https://media.example/first.png?signature=execution-only",
                "https://media.example/second.jpg?signature=execution-only",
            ],
        },
        "source": {"scene": "canvas_node", "project_id": "1"},
        "canvas_request": body,
        "canvas_text_references": {
            "mode": "text",
            "references": [
                {"media_id": "9007199254740997", "mime_type": "image/png", "byte_size": 90},
                {"media_id": "9007199254740999", "mime_type": "image/jpeg", "byte_size": 130},
            ],
        },
    }


def text_snapshot():
    return {
        "service_type": "text",
        "base_url": "https://provider.example/v1",
        "model_key": "saved-model",
        "canvas_text_capability": {
            "references": {"maxImages": 2, "maxImageBytes": 1024, "promptMaxChars": 32000}
        },
    }


def test_saved_chat_images_use_standard_string_placeholder_before_trusted_transform():
    body = text_request()
    before = deepcopy(body)
    payload = generation_payload(CanvasRuntimeTaskCreate.model_validate(body), 1, "openai_chat.v1")
    assert payload["input"] == {
        "messages": [
            {"role": "system", "content": "请按顺序说明图片"},
            {"role": "user", "content": body["prompt"]},
        ]
    }
    assert body == before


def test_chat_recipe_keeps_system_text_first_and_all_images_in_source_order():
    snapshot, payload = text_snapshot(), prepared_text()
    before = deepcopy(payload)
    url, _, body = build_submission(snapshot, payload, "openai_chat.v1")
    assert url == "https://provider.example/v1/chat/completions"
    assert body["messages"] == [
        {"role": "system", "content": "请按顺序说明图片"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": payload["canvas_request"]["prompt"]},
                *[
                    {"type": "image_url", "image_url": {"url": value}}
                    for value in payload["input"]["reference_urls"]
                ],
            ],
        },
    ]
    assert body["stream"] is True and payload == before
    assert body["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize("adapter", [None, "openai_responses.v1"])
def test_unimplemented_text_image_protocols_are_rejected_before_admission(adapter):
    with pytest.raises(WorkflowError):
        generation_payload(CanvasRuntimeTaskCreate.model_validate(text_request()), 1, adapter)


def test_standard_text_cannot_accept_reference_ids_or_arbitrary_multimodal_json():
    for inputs in (
        {"messages": [{"role": "user", "content": "正文"}], "reference_media_ids": ["1"]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "正文"}]}]},
    ):
        with pytest.raises(GenerationError, match="unsupported_parameters"):
            build_submission(text_snapshot(), {"input": inputs}, "openai_chat.v1")


@pytest.mark.parametrize("gate", ["source", "canvas_request", "canvas_text_references"])
def test_missing_trusted_canvas_gate_never_enables_multimodal_input(gate):
    payload = prepared_text()
    payload.pop(gate)
    with pytest.raises(GenerationError):
        build_submission(text_snapshot(), payload, "openai_chat.v1")


@pytest.mark.parametrize("field", ["canvas_request", "source"])
def test_malformed_frozen_gate_remains_a_controlled_rejection(field):
    payload = prepared_text()
    payload[field] = ["malformed"]
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        build_submission(text_snapshot(), payload, "openai_chat.v1")


@pytest.mark.parametrize("mutation", ["ids", "urls", "declared", "prompt", "envelope", "mime"])
def test_frozen_reference_order_identity_and_closed_metadata_are_enforced(mutation):
    payload = prepared_text()
    if mutation == "ids":
        payload["input"]["reference_media_ids"].reverse()
    elif mutation == "urls":
        payload["input"]["reference_urls"].pop()
    elif mutation == "declared":
        payload["canvas_request"]["input"]["referenceImages"].reverse()
    elif mutation == "prompt":
        payload["input"]["messages"][-1]["content"] = "不同的提示词"
    elif mutation == "envelope":
        payload["canvas_text_references"]["authorization"] = "forged"
    else:
        payload["canvas_text_references"]["references"][0]["mime_type"] = "video/mp4"
    before = deepcopy(payload)
    with pytest.raises(GenerationError):
        build_submission(text_snapshot(), payload, "openai_chat.v1")
    assert payload == before


@pytest.mark.parametrize(
    "capability",
    [
        None,
        {"references": {"maxImages": 1, "maxImageBytes": 1024}},
        {"references": {"maxImages": 2, "maxImageBytes": 100}},
    ],
)
def test_only_frozen_saved_limits_allow_the_recipe(capability):
    snapshot = text_snapshot()
    snapshot["canvas_text_capability"] = capability
    with pytest.raises(GenerationError, match="reference_images_too_large"):
        build_submission(snapshot, prepared_text(), "openai_chat.v1")


@pytest.mark.parametrize("stream", [False, True])
def test_real_http_receives_source_image_url_wire_without_mutating_archive(
    gateway, provider, stream
):
    base, state, calls = provider
    state["body"] = {"choices": [{"message": {"content": "按原顺序描述"}, "finish_reason": "stop"}]}
    snapshot, payload = text_snapshot(), prepared_text()
    snapshot["base_url"] = base + "/v1"
    payload["canvas_request"]["input"]["textOptions"] = {"stream": stream, "thinking": False}
    before = deepcopy(payload)
    result = gateway.submit(snapshot, payload, "controlled-text-reference-key")
    assert result.text == "按原顺序描述" and len(calls) == 1
    assert calls[0][0:2] == ("POST", "/v1/chat/completions")
    body = calls[0][3]
    assert body["stream"] is stream
    assert [item["image_url"]["url"] for item in body["messages"][-1]["content"][1:]] == payload[
        "input"
    ]["reference_urls"]
    assert payload == before


def test_saved_streaming_false_matches_source_canvas_stream_gate():
    snapshot = text_snapshot()
    snapshot["canvas_text_capability"]["streaming"] = False
    _, _, body = build_submission(snapshot, prepared_text(), "openai_chat.v1")
    assert body["stream"] is False


def admission_fixture(monkeypatch):
    from short_drama.service import canvas_text_admission

    media = {
        "9007199254740997": SimpleNamespace(
            storage_locator="minio://private/first.png",
            format_code="image/png",
            byte_size=90,
            width=37,
            height=19,
            project_id=1,
        ),
        "9007199254740999": SimpleNamespace(
            storage_locator="minio://private/second.jpg",
            format_code="image/jpeg",
            byte_size=130,
            width=41,
            height=23,
            project_id=1,
        ),
    }
    generation = SimpleNamespace(
        session=object(), _validate_media=lambda value, _kind: media[value]
    )
    monkeypatch.setattr(
        canvas_text_admission, "scope_of", lambda _session, value: (None, value.project_id)
    )
    payload = prepared_text()
    payload.pop("canvas_text_references")
    payload["input"].pop("reference_media_ids")
    payload["input"].pop("reference_urls")
    cache = {"canvas_text_capability": text_snapshot()["canvas_text_capability"]}
    return generation, media, payload, cache


def test_admission_freezes_actual_metadata_and_removes_client_transient_urls(monkeypatch):
    generation, _, payload, cache = admission_fixture(monkeypatch)
    payload["canvas_request"]["input"]["referenceImages"][0].update(
        type="video/mp4", bytes=1, width=1, height=1, url="https://untrusted.example/image"
    )
    freeze_text_references(generation, payload, 1, "openai_chat.v1", cache)
    assert payload["canvas_text_references"] == prepared_text()["canvas_text_references"]
    reference = payload["canvas_request"]["input"]["referenceImages"][0]
    assert reference["type"] == "image/png" and reference["bytes"] == 90
    assert (reference["width"], reference["height"]) == (37, 19) and "url" not in reference
    assert "reference_urls" not in payload["input"]


@pytest.mark.parametrize("failure", ["scope", "locator", "size", "mime", "count", "unsaved"])
def test_failed_admission_never_partially_rewrites_the_draft(monkeypatch, failure):
    generation, media, payload, cache = admission_fixture(monkeypatch)
    second = media["9007199254740999"]
    if failure == "scope":
        second.project_id = 2
    elif failure == "locator":
        second.storage_locator = "https://untrusted.example/image"
    elif failure == "size":
        second.byte_size = 1025
    elif failure == "mime":
        second.format_code = "image/svg+xml"
    elif failure == "count":
        cache["canvas_text_capability"]["references"]["maxImages"] = 1
    else:
        payload["canvas_request"]["input"]["referenceImages"][1]["storageKey"] = "blob:unsaved"
    before = deepcopy(payload)
    with pytest.raises(NotFound if failure == "scope" else WorkflowError):
        freeze_text_references(generation, payload, 1, "openai_chat.v1", cache)
    assert payload == before


def test_overflow_resource_identity_is_rejected_before_any_database_read(monkeypatch):
    generation, _, payload, cache = admission_fixture(monkeypatch)
    payload["canvas_request"]["input"]["referenceImages"] = [{"storageKey": f"resource:{2**64}"}]
    generation._validate_media = lambda *_args: pytest.fail("无效 ID 不能触发数据库读取")
    with pytest.raises(WorkflowError) as caught:
        freeze_text_references(generation, payload, 1, "openai_chat.v1", cache)
    assert caught.value.code == "canvas_reference_not_saved"
