"""固定源 thinking 与 Claude 图片合同；无真实供应商请求。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from short_drama.ai.adapters import build_submission
from short_drama.ai.types import GenerationError
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.schemas.model_runtime_profile import ModelRuntimeProfile
from short_drama.service.canvas_generation_inputs import generation_payload
from tests.unit.canvas.text.test_canvas_claude_text import request as claude_request
from tests.unit.canvas.text.test_canvas_claude_text import snapshot as claude_snapshot
from tests.unit.canvas.text.test_canvas_text_references import (
    prepared_text,
    text_request,
    text_snapshot,
)


@pytest.mark.parametrize("adapter", ["openai_chat.v1", "openai_responses.v1", "claude_messages.v1"])
def test_declared_thinking_uses_fixed_source_wire_and_preserves_request(adapter):
    snapshot = claude_snapshot() if adapter == "claude_messages.v1" else text_snapshot()
    data = claude_request() if adapter == "claude_messages.v1" else prepared_text()
    snapshot.setdefault("canvas_text_capability", {})["thinking"] = True
    data["canvas_request"]["input"]["textOptions"] = {"stream": False, "thinking": True}
    before = deepcopy(data)
    _, _, body = build_submission(snapshot, data, adapter)
    if adapter == "openai_chat.v1":
        assert body["reasoning_effort"] == "medium"
    elif adapter == "openai_responses.v1":
        assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    else:
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert body["stream"] is False and data == before


@pytest.mark.parametrize("declared", [None, False, "true", 1])
@pytest.mark.parametrize("adapter", ["openai_chat.v1", "openai_responses.v1", "claude_messages.v1"])
def test_thinking_cannot_be_enabled_without_saved_boolean_capability(declared, adapter):
    snapshot = claude_snapshot() if adapter == "claude_messages.v1" else text_snapshot()
    data = claude_request() if adapter == "claude_messages.v1" else prepared_text()
    snapshot.setdefault("canvas_text_capability", {})["thinking"] = declared
    data["canvas_request"]["input"]["textOptions"] = {"stream": True, "thinking": True}
    with pytest.raises(GenerationError):
        build_submission(snapshot, data, adapter)


@pytest.mark.parametrize("value", ["true", "false", 0, 1, None])
def test_thinking_request_requires_a_real_boolean(value):
    data = text_request()
    data["input"]["textOptions"] = {"thinking": value}
    with pytest.raises(ValidationError):
        CanvasRuntimeTaskCreate.model_validate(data)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_thinking_capability_requires_a_real_boolean(value):
    with pytest.raises(ValidationError):
        ModelRuntimeProfile.model_validate(
            {"protocol": "chat-completion", "capability_config": {"text": {"thinking": value}}}
        )


def test_claude_images_use_frozen_url_blocks_in_the_saved_reference_order():
    data, snapshot = prepared_text(), text_snapshot()
    before = deepcopy(data)
    _, _, body = build_submission(snapshot, data, "claude_messages.v1")
    assert body["system"] == "请按顺序说明图片"
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": data["canvas_request"]["prompt"]},
                *[
                    {"type": "image", "source": {"type": "url", "url": url}}
                    for url in data["input"]["reference_urls"]
                ],
            ],
        }
    ]
    assert data == before


def test_claude_admission_accepts_stable_images_and_thinking_requires_saved_capability():
    data = text_request()
    request = CanvasRuntimeTaskCreate.model_validate(data)
    payload = generation_payload(request, 1, "claude_messages.v1")
    assert payload["input"]["messages"][-1]["content"] == data["prompt"]
    request.input.text_options.thinking = True
    payload = generation_payload(
        request, 1, "claude_messages.v1", {"canvas_text_capability": {"thinking": True}}
    )
    assert payload["input"]["messages"][-1]["content"] == data["prompt"]


@pytest.mark.parametrize("adapter", ["openai_chat.v1", "openai_responses.v1", "claude_messages.v1"])
def test_editor_tool_thinking_keeps_source_wire_and_saved_capability(adapter):
    from tests.unit.canvas.tools.test_canvas_tool_admission import tool_request
    from tests.unit.canvas.tools.test_canvas_tool_gateway import frozen_request, snapshot

    data, saved = frozen_request(), snapshot("https://provider.example/v1")
    saved["canvas_text_capability"]["thinking"] = True
    data["canvas_request"]["input"]["textOptions"]["thinking"] = True
    before = deepcopy(data)
    _, _, body = build_submission(saved, data, adapter)
    if adapter == "openai_chat.v1":
        assert body["reasoning_effort"] == "medium" and "tool_choice" not in body
    elif adapter == "openai_responses.v1":
        assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
        assert body["tool_choice"] == {"type": "function", "name": "optimize_prompt"}
    else:
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert body["tool_choice"] == {"type": "tool", "name": "optimize_prompt"}
    assert body["stream"] is False and data == before
    request = tool_request()
    request["input"]["textOptions"] = {"thinking": True}
    parsed = CanvasRuntimeTaskCreate.model_validate(request)
    assert generation_payload(
        parsed, 1, adapter, {"canvas_text_capability": saved["canvas_text_capability"]}
    )


@pytest.mark.parametrize(
    "adapter",
    ["openai_chat.v1", "openai_responses.v1", "claude_messages.v1", "gemini_generate_content.v1"],
)
def test_editor_tool_thinking_cannot_bypass_saved_capability(adapter):
    from tests.unit.canvas.tools.test_canvas_tool_gateway import frozen_request, snapshot

    data, saved = frozen_request(), snapshot("https://provider.example/v1")
    data["canvas_request"]["input"]["textOptions"]["thinking"] = True
    with pytest.raises(GenerationError):
        build_submission(saved, data, adapter)


def test_claude_video_is_rejected_before_reference_hydration(monkeypatch):
    from short_drama.core.exceptions import WorkflowError
    from short_drama.service.canvas_text_admission import freeze_text_references
    from tests.unit.canvas.video.test_canvas_text_video_references import video_admission

    generation, _, payload, cache = video_admission(monkeypatch)
    before = deepcopy(payload)
    with pytest.raises(WorkflowError, match="尚未支持"):
        freeze_text_references(generation, payload, 1, "claude_messages.v1", cache)
    assert payload == before


@pytest.mark.parametrize("tokens", [1, 1024])
def test_claude_thinking_rejects_an_insufficient_saved_output_budget(tokens):
    data, saved = claude_request(), claude_snapshot()
    data["canvas_request"]["input"]["textOptions"]["thinking"] = True
    saved["canvas_text_capability"] = {"thinking": True}
    saved["canvas_text_defaults"] = {"max_tokens": tokens}
    with pytest.raises(GenerationError):
        build_submission(saved, data, "claude_messages.v1")
