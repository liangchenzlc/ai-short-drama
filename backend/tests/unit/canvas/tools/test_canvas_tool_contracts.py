"""固定源编辑器工具合同；不经由旧助手或浏览器供应商请求。"""

import importlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.ai.types import GenerationError

DATA = Path(__file__).parents[4] / "src/short_drama/ai/data/beeftv_tool_contracts.json"
TOOLS = {item["function"]["name"]: item for item in json.loads(DATA.read_text("utf-8"))["tools"]}
ADAPTERS = (
    "openai_chat.v1",
    "openai_responses.v1",
    "claude_messages.v1",
    "gemini_generate_content.v1",
)


def schema():
    return importlib.import_module("short_drama.schemas.canvas_tool_contracts")


def adapters():
    return importlib.import_module("short_drama.ai.canvas_tool_adapters")


def canonical(name="optimize_prompt", *, image=False):
    content = "请优化这条提示词"
    if image:
        content = [
            {"type": "text", "text": content},
            {"type": "image_url", "image_url": {"url": "resource:9007199254740997"}},
        ]
    return {
        "messages": [{"role": "user", "content": content}],
        "tools": [deepcopy(TOOLS[name])],
        "toolChoice": {"type": "function", "name": name},
        "systemPrompt": "宿主系统提示词",
    }


def valid_arguments(name="optimize_prompt"):
    if name.startswith("review_art_"):
        return {"candidates": []}
    return {
        "optimizedPrompt": "人物在窗边阅读",
        "negativePrompt": "",
        "changes": ["补齐动作"],
        "assumptions": [],
        "variants": [{"label": "暖光", "prompt": "人物在暖光窗边阅读"}],
    }


def payload(adapter, *, arguments=None, name="optimize_prompt"):
    arguments = valid_arguments(name) if arguments is None else arguments
    raw = json.dumps(arguments, ensure_ascii=False)
    if adapter == "openai_chat.v1":
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": "私人思考",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": raw,
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    if adapter == "openai_responses.v1":
        return {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "私人思考"}]},
                {"type": "function_call", "call_id": "call-1", "name": name, "arguments": raw},
            ],
        }
    if adapter == "claude_messages.v1":
        return {
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "私人思考"},
                {"type": "tool_use", "id": "call-1", "name": name, "input": arguments},
            ],
        }
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "私人思考", "thought": True},
                        {
                            "functionCall": {"id": "call-1", "name": name, "args": arguments},
                            "thoughtSignature": "private-signature",
                        },
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }


def test_builtin_snapshot_keeps_fixed_source_identity_and_ten_tools():
    data = json.loads(DATA.read_text("utf-8"))
    assert data["commit"] == "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c"
    assert len(TOOLS) == 10
    assert [item["sha256"] for item in data["sources"]] == [
        "40da45c47aa44384d69ad0c44ed2e7f318881c673eff95aee9f72d8ffe11b197",
        "3d817e75622678693872873bcec439ab2a0b783427a0b977fe21982aafcaf980",
    ]
    contract = schema().CanvasToolCanonical.model_validate(canonical(image=True))
    assert contract.image_keys() == ["resource:9007199254740997"]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_protocol_body_uses_only_hydrated_images_and_fixed_named_tool(adapter):
    source = canonical(image=True)
    before = deepcopy(source)
    body = adapters().build_canvas_tool_body(
        source,
        adapter,
        image_references={
            "resource:9007199254740997": {
                "url": "https://media.example/image.png?signature=execution-only",
                "mime_type": "image/png",
            }
        },
    )
    assert source == before
    encoded = json.dumps(body)
    assert "resource:" not in encoded
    assert "signature=execution-only" in encoded
    if adapter == "openai_chat.v1":
        assert body["tool_choice"] == {"type": "function", "function": {"name": "optimize_prompt"}}
        assert body["messages"][0] == {"role": "system", "content": "宿主系统提示词"}
        assert body["parallel_tool_calls"] is False
    elif adapter == "openai_responses.v1":
        assert body["tool_choice"] == {"type": "function", "name": "optimize_prompt"}
        assert body["input"][1]["content"][1]["type"] == "input_image"
        assert body["tools"][0]["parameters"] == TOOLS["optimize_prompt"]["function"]["parameters"]
    elif adapter == "claude_messages.v1":
        assert body["system"] == [
            {
                "type": "text",
                "text": "宿主系统提示词",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}
        assert (
            body["tools"][0]["input_schema"] == TOOLS["optimize_prompt"]["function"]["parameters"]
        )
        assert body["messages"][0]["content"][1]["source"]["type"] == "url"
    else:
        assert body["toolConfig"]["functionCallingConfig"] == {
            "mode": "ANY",
            "allowedFunctionNames": ["optimize_prompt"],
        }
        assert body["contents"][0]["parts"][1]["fileData"]["mimeType"] == "image/png"


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_tool_only_reply_is_valid_and_reasoning_stays_separate(adapter):
    body = payload(adapter)
    before = deepcopy(body)
    result = adapters().parse_canvas_tool_result(body, adapter, canonical(), submitted=True)
    assert body == before
    assert result.content == ""
    assert result.reasoning == "私人思考"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "call-1"
    assert call.function.name == "optimize_prompt"
    assert json.loads(call.function.arguments) == valid_arguments()
    if adapter == "gemini_generate_content.v1":
        assert call.thought_signature == "private-signature"


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(
    "bad_arguments",
    [
        {"optimizedPrompt": "缺少必要字段"},
        {**valid_arguments(), "variants": [{"label": "缺少prompt"}]},
        {**valid_arguments(), "changes": [123]},
        {**valid_arguments(), "extra": "不属于合同"},
    ],
)
def test_provider_tool_arguments_are_validated_on_server(adapter, bad_arguments):
    with pytest.raises(GenerationError) as caught:
        adapters().parse_canvas_tool_result(
            payload(adapter, arguments=bad_arguments),
            adapter,
            canonical(),
            submitted=True,
        )
    assert caught.value.code == "invalid_response"
    assert caught.value.accepted_unknown


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_provider_cannot_substitute_an_unrequested_tool(adapter):
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(
            payload(adapter, name="review_art_color"),
            adapter,
            canonical(),
            submitted=True,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseUrl", "https://other.example"),
        ("apiKey", "must-not-be-a-client-key"),
        ("chatCompletion", {"messages": []}),
    ],
)
def test_canonical_cannot_carry_client_transport_or_protocol_bodies(field, value):
    source = {**canonical(), field: value}
    with pytest.raises(ValidationError):
        schema().CanvasToolCanonical.model_validate(source)


@pytest.mark.parametrize(
    "value",
    [
        "https://media.example/image.png",
        "data:image/png;base64,YQ==",
        "blob:local-image",
        "resource:0",
        "resource:18446744073709551616",
    ],
)
def test_canonical_images_require_permanent_decimal_resource_identity(value):
    source = canonical(image=True)
    source["messages"][0]["content"][1]["image_url"]["url"] = value
    with pytest.raises(ValidationError):
        schema().CanvasToolCanonical.model_validate(source)


@pytest.mark.parametrize("mutation", ["weaken", "ref", "name", "choice"])
def test_client_cannot_replace_builtin_schema_or_tool_choice(mutation):
    source = canonical()
    if mutation == "weaken":
        source["tools"][0]["function"]["parameters"] = {"type": "object"}
    elif mutation == "ref":
        source["tools"][0]["function"]["parameters"]["$ref"] = "https://other.example/schema"
    elif mutation == "name":
        source["tools"][0]["function"]["name"] = "canvas_apply_ops"
    else:
        source["toolChoice"]["name"] = "review_art_color"
    with pytest.raises(ValidationError):
        schema().CanvasToolCanonical.model_validate(source)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_missing_hydration_never_sends_resource_key_to_provider(adapter):
    with pytest.raises(GenerationError, match="unresolved_media_reference"):
        adapters().build_canvas_tool_body(canonical(image=True), adapter)


@pytest.mark.parametrize(
    "name", ["review_art_color", "review_art_lighting", "review_art_structure"]
)
def test_empty_reviewer_candidates_are_a_valid_analysis_result(name):
    result = adapters().parse_canvas_tool_result(
        payload("openai_chat.v1", name=name),
        "openai_chat.v1",
        canonical(name),
        submitted=True,
    )
    assert json.loads(result.tool_calls[0].function.arguments) == {"candidates": []}


def test_reply_rejects_truncated_or_duplicate_calls_and_nonfinite_json():
    body = payload("openai_chat.v1")
    body["choices"][0]["finish_reason"] = "length"
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(body, "openai_chat.v1", canonical(), submitted=True)
    body["choices"][0]["finish_reason"] = "tool_calls"
    body["choices"][0]["message"]["tool_calls"] *= 2
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(body, "openai_chat.v1", canonical(), submitted=True)
    body = payload("openai_chat.v1")
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"x":NaN}'
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(body, "openai_chat.v1", canonical(), submitted=True)


def test_schema_numeric_bounds_cannot_be_replaced_with_equal_booleans():
    source = canonical("ground_art_critique_issues")
    properties = source["tools"][0]["function"]["parameters"]["properties"]
    properties["targets"]["items"]["properties"]["groundingConfidence"]["minimum"] = False
    with pytest.raises(ValidationError):
        schema().CanvasToolCanonical.model_validate(source)


def test_non_boolean_thought_flag_cannot_publish_private_reasoning_as_content():
    body = payload("gemini_generate_content.v1")
    body["candidates"][0]["content"]["parts"][0]["thought"] = "true"
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(
            body, "gemini_generate_content.v1", canonical(), submitted=True
        )


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_text_json_fallback_remains_supported_but_schema_invalid_text_is_rejected(adapter):
    value = "```json\n" + json.dumps(valid_arguments(), ensure_ascii=False) + "\n```"
    if adapter == "openai_chat.v1":
        body = {"choices": [{"message": {"content": value}, "finish_reason": "stop"}]}
    elif adapter == "openai_responses.v1":
        body = {"status": "completed", "output_text": value}
    elif adapter == "claude_messages.v1":
        body = {"content": [{"type": "text", "text": value}], "stop_reason": "end_turn"}
    else:
        body = {"candidates": [{"content": {"parts": [{"text": value}]}, "finishReason": "STOP"}]}
    result = adapters().parse_canvas_tool_result(body, adapter, canonical(), submitted=True)
    assert result.content == value
    assert result.tool_calls == []
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result({}, adapter, canonical(), submitted=False)


@pytest.mark.parametrize(
    "name,arguments",
    [
        (
            "aggregate_art_critique",
            {"summary": "没有明确问题", "strengths": [], "issues": [], "options": []},
        ),
        ("ground_art_critique_issues", {"targets": []}),
        ("generate_art_edit_prompts", {"prompts": []}),
        ("verify_art_critique", {"decisions": []}),
        (
            "analyze_art_scene",
            {
                "scene": {
                    "imageType": "portrait",
                    "style": [],
                    "subjects": [],
                    "intendedFocus": "人物",
                    "compositionType": [],
                    "lightingType": [],
                    "mood": "平静",
                    "estimatedIntent": "未确定",
                    "sceneDepth": "shallow",
                }
            },
        ),
    ],
)
def test_other_critique_stages_validate_their_own_source_schema(name, arguments):
    result = adapters().parse_canvas_tool_result(
        payload("openai_chat.v1", name=name, arguments=arguments),
        "openai_chat.v1",
        canonical(name),
        submitted=True,
    )
    assert json.loads(result.tool_calls[0].function.arguments) == arguments
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(
            payload("openai_chat.v1", name=name, arguments={"wrong": []}),
            "openai_chat.v1",
            canonical(name),
            submitted=True,
        )


def test_canonical_request_and_reply_enforce_byte_budgets_before_publication():
    source = canonical()
    source["messages"] = [{"role": "user", "content": "中" * 128000}] * 2
    with pytest.raises(ValidationError, match="byte limit"):
        schema().CanvasToolCanonical.model_validate(source)
    body = payload("openai_chat.v1")
    body["choices"][0]["message"]["reasoning_content"] = "中" * (512 * 1024 // 3 + 1)
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(body, "openai_chat.v1", canonical(), submitted=True)


def test_duplicate_json_fields_are_rejected_and_builtin_copies_are_independent():
    contracts = schema()
    changed = contracts.builtin_tool("optimize_prompt")
    changed["function"]["parameters"]["required"].clear()
    assert contracts.builtin_tool("optimize_prompt") == TOOLS["optimize_prompt"]
    body = payload("openai_chat.v1")
    raw = json.dumps(valid_arguments())[:-1] + ',"optimizedPrompt":"重复"}'
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = raw
    with pytest.raises(GenerationError, match="invalid_response"):
        adapters().parse_canvas_tool_result(body, "openai_chat.v1", canonical(), submitted=True)
