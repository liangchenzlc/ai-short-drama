"""固定源 hosted Gemini 普通文字合同；合成响应不代表真实供应商验收。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.adapters.results import parse_result
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.ai.gateway.client import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.service.ai_config.management.runtime import refresh_runtime_model

ADAPTER = "gemini_generate_content.v1"
KEY = "synthetic-gemini-text-key"


def snapshot(**patch):
    return {
        "base_url": "https://provider.example/proxy/v1",
        "model_key": "gemini/fixture:雪 ?#",
        "service_type": "text",
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_node",
        **patch,
    }


def request(*, options=None, stream=True):
    return {
        "source": {"scene": "canvas_node"},
        "input": {
            "messages": [
                {"role": "system", "content": "历史系统消息"},
                {"role": "user", "content": "历史问题"},
                {"role": "assistant", "content": "历史回答"},
                {"role": "user", "content": "当前问题🌈"},
            ]
        },
        "parameters": {},
        "canvas_request": {
            "type": "canvas_text",
            "operation": "text",
            "prompt": "当前问题🌈",
            "input": {
                "mode": "text",
                "config": {"systemPrompt": "  本次指令  "},
                "textOptions": {"stream": stream, "thinking": False},
                "metadata": {"providerOptions": {"gemini-generate-content": options or {}}},
            },
        },
        "canvas_text_references": {"mode": "text", "references": []},
    }


@pytest.mark.parametrize("protocol", ["gemini-generate-content", "google-gemini-generate-content"])
def test_saved_hosted_protocol_derives_native_adapter_but_custom_gemini_format_keeps_gate(protocol):
    defaults = {"generationConfig": {"maxOutputTokens": 321, "temperature": 0.3}}
    config = SimpleNamespace(
        base_url="https://provider.example",
        model_key="gemini-fixture",
        service_type="text",
        apikey="encrypted-test-key",
        runtime_credentials_cipher=None,
        runtime_profile={
            "version": 1,
            "api_format": "openai",
            "protocol": protocol,
            "default_options": defaults,
            "capability_config": {"text": {"streaming": True}},
        },
        capability_cache=None,
    )
    assert refresh_runtime_model(config)
    assert config.capability_cache["adapter"] == ADAPTER
    assert config.capability_cache["canvas_text_defaults"] == defaults
    assert config.capability_cache["canvas_text_defaults"] is not defaults
    assert config.capability_cache["canvas_text_capability"] == {"streaming": True}
    assert not refresh_runtime_model(config)
    before = deepcopy(config.capability_cache)
    config.runtime_profile["api_format"] = "gemini"
    with pytest.raises(GenerationError) as error:
        refresh_runtime_model(config)
    assert error.value.code == "unsupported_protocol" and config.capability_cache == before


@pytest.mark.parametrize(
    ("base", "path"),
    [
        ("https://provider.example", "/v1beta/models/"),
        ("https://provider.example/v1beta", "/v1beta/models/"),
        ("https://provider.example/proxy/v1beta", "/proxy/v1beta/models/"),
        ("https://provider.example/proxy/v1", "/proxy/v1beta/models/"),
        ("https://provider.example/proxy/api/plan/v3", "/proxy/v1beta/models/"),
        ("https://provider.example/proxy", "/proxy/v1beta/models/"),
    ],
)
def test_fixed_manifest_escapes_model_and_preserves_proxy_without_duplicate_version(base, path):
    data = request()
    before = deepcopy(data)
    url, headers, body = build_submission(snapshot(base_url=base), data, ADAPTER)
    assert (
        url
        == "https://provider.example" + path + "gemini%2Ffixture:%E9%9B%AA%20%3F%23:generateContent"
    )
    assert headers == {}
    assert body == {
        "contents": [
            {"role": "user", "parts": [{"text": "历史问题"}]},
            {"role": "model", "parts": [{"text": "历史回答"}]},
            {"role": "user", "parts": [{"text": "当前问题🌈"}]},
        ],
        "systemInstruction": {"parts": [{"text": "本次指令"}]},
    }
    assert data == before


def test_empty_system_remains_null_and_source_nonstream_does_not_send_openai_stream_fields():
    data = request()
    data["canvas_request"]["input"]["config"]["systemPrompt"] = ""
    _, _, body = build_submission(snapshot(), data, ADAPTER)
    assert body["systemInstruction"] is None
    assert "stream" not in body and "stream_options" not in body and "model" not in body


def test_saved_generation_config_and_explicit_provider_namespace_keep_source_mapping():
    defaults = {
        "generationConfig": {"maxOutputTokens": 321},
        "cachedContent": "cachedContents/base",
    }
    options = {
        "generationConfig": {"maxOutputTokens": 654, "temperature": 0.4, "stopSequences": ["结束"]},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
        ],
    }
    frozen = snapshot(canvas_text_defaults=defaults)
    before = deepcopy(frozen)
    _, _, body = build_submission(frozen, request(options=options, stream=False), ADAPTER)
    assert body["generationConfig"] == options["generationConfig"]
    assert body["safetySettings"] == options["safetySettings"]
    assert body["cachedContent"] == defaults["cachedContent"]
    assert frozen == before


@pytest.mark.parametrize(
    "change", ["standard", "agent", "references", "tools", "thinking", "invalid-option"]
)
def test_incomplete_or_other_scene_parameters_are_rejected_before_transport(change):
    data = request()
    if change in {"standard", "agent"}:
        data["source"]["scene"] = change
    elif change == "references":
        data["canvas_text_references"]["references"] = [{"media_id": "1"}]
    elif change == "tools":
        data["canvas_request"]["input"]["metadata"]["providerOptions"][
            "gemini-generate-content"
        ] = {"tools": [{}]}
    elif change == "thinking":
        data["canvas_request"]["input"]["textOptions"]["thinking"] = True
    else:
        data["canvas_request"]["input"]["metadata"]["providerOptions"][
            "gemini-generate-content"
        ] = {"generationConfig": {"maxOutputTokens": True}}
    with pytest.raises(GenerationError) as error:
        build_submission(snapshot(), data, ADAPTER)
    assert error.value.code == (
        "unsupported_protocol" if change in {"standard", "agent"} else "unsupported_parameters"
    )


def test_native_google_header_never_sends_bearer_or_query_key():
    gateway = GenerationGateway(SimpleNamespace())
    calls, deltas = [], []

    def exchange(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert kwargs.get("on_chunk") is None and kwargs.get("on_headers") is None
        return (
            200,
            {"Content-Type": "application/json"},
            json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "  真实文本🌈  "}]}}]}
            ).encode(),
        )

    gateway.transport = SimpleNamespace(request=exchange)
    credential = CanvasCredentials(
        apiKey=KEY,
        headers=[{"name": "X-Custom", "value": "saved-header"}],
    )
    result = gateway.submit(snapshot(), request(), credential, ADAPTER, on_text_delta=deltas.append)
    assert result.text == "真实文本🌈" and result.adapter == ADAPTER
    assert deltas == [] and len(calls) == 1
    _, url, transport = calls[0]
    assert "?" not in url and KEY not in url
    headers = transport["headers"]
    assert {k.lower(): v for k, v in headers.items()}.get("x-goog-api-key") == KEY
    assert len([k for k in headers if k.lower() == "x-goog-api-key"]) == 1
    assert not any(k.lower() == "authorization" for k in headers)
    assert headers.get("X-Custom") == "saved-header"


def test_first_candidate_text_is_trimmed_and_joined_without_nontext_parts_or_later_candidates():
    result = parse_result(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": " 首段 "}, {"functionCall": {}}, {"text": "\n尾段\n"}]
                    },
                    "finishReason": "STOP",
                },
                {"content": {"parts": [{"text": "另一候选不被拼入"}]}},
            ],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
        },
        ADAPTER,
        submitted=True,
    )
    assert result.text == "首段尾段" and result.reasoning is None
    assert result.usage == {"promptTokenCount": 2, "candidatesTokenCount": 3}


@pytest.mark.parametrize(
    "body", [{}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": 1}]}}]}]
)
def test_malformed_or_empty_native_result_remains_unknown_after_post(body):
    with pytest.raises(GenerationError) as error:
        parse_result(body, ADAPTER, submitted=True)
    assert error.value.code == "invalid_response" and error.value.accepted_unknown
    assert not error.value.retryable


def test_explicit_supplier_error_is_failure_and_output_limit_is_not_a_valid_complete_text():
    failed = parse_result({"error": {"code": 400, "message": KEY}}, ADAPTER, submitted=True)
    assert failed.status == "failed" and failed.error["code"] == "provider_rejected"
    assert KEY not in json.dumps(failed.error)
    truncated = parse_result(
        {
            "candidates": [
                {"content": {"parts": [{"text": "未完成"}]}, "finishReason": "MAX_TOKENS"}
            ]
        },
        ADAPTER,
        submitted=True,
    )
    assert truncated.finish_reason == "max_output_tokens"


def test_unrequested_supplier_thought_is_private_and_never_mixed_into_work_text():
    result = parse_result(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "仅本人思考", "thought": True},
                            {"text": "作品正文", "thought": False},
                        ]
                    }
                }
            ]
        },
        ADAPTER,
        submitted=True,
    )
    assert result.text == "作品正文" and result.reasoning == "仅本人思考"


def test_native_provider_is_never_selected_by_model_name_without_saved_protocol():
    assert select_adapter(snapshot(base_url="https://provider.example")) == "openai_chat.v1"
