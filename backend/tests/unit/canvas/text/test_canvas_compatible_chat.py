"""固定源十八个相同Chat合同；真实本机HTTP，不调用供应商。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.types import GenerationError
from short_drama.service.ai_config.management.runtime import refresh_runtime_model
from short_drama.service.canvas.tools.plugin_catalog import (
    bundled_manifest_snapshot,
    provider_catalog,
)
from tests.unit.canvas.text.test_canvas_text_references import prepared_text, text_snapshot
from tests.unit.canvas.tools.test_canvas_tool_gateway import frozen_request
from tests.unit.canvas.tools.test_canvas_tool_gateway import snapshot as tool_snapshot
from tests.unit.shared.generation.test_generation_adapters import gateway as gateway
from tests.unit.shared.generation.test_generation_adapters import provider as provider

PROTOCOLS = (
    "atlascloud-chat",
    "baichuan-chat",
    "deepseek-chat",
    "fireworks-chat",
    "groq-chat",
    "kimi-chat",
    "litellm-proxy-chat",
    "localai-chat",
    "mistral-chat",
    "newapi-chat",
    "nvidia-nim-chat",
    "openrouter-chat",
    "siliconflow-chat",
    "together-chat",
    "vllm-chat",
    "xai-grok-chat",
    "yi-chat",
    "zhipu-glm-chat",
)


def test_fixed_source_eighteen_full_contracts_match_except_identity_and_default_origin():
    contracts = {}
    for package in bundled_manifest_snapshot()["packages"]:
        for definition in json.loads(package["manifestText"])["contributes"].get("providers", []):
            if definition["id"] in PROTOCOLS:
                value = json.loads(json.dumps(definition).replace(definition["id"], "$id"))
                value.pop("baseUrl")
                value.pop("label")
                contracts[definition["id"]] = value
    assert set(contracts) == set(PROTOCOLS)
    reference = contracts[PROTOCOLS[0]]
    assert all(value == reference for value in contracts.values())
    assert reference["auth"] == {"type": "bearer", "field": "apiKey"}
    assert reference["create"]["path"] == "/v1/chat/completions"
    assert reference["response"]["textPaths"] == ["choices.0.message.content", "choices.0.text"]


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_saved_alias_refresh_and_catalog_expose_the_real_frozen_protocol(protocol):
    config = SimpleNamespace(
        runtime_profile={
            "version": 1,
            "api_format": "openai",
            "protocol": protocol,
            "default_options": {"top_p": 0.7},
        },
        apikey="encrypted-fixture",
        base_url="https://provider.example/v1",
        model_key="saved-model",
        service_type="text",
        capability_cache=None,
    )
    assert refresh_runtime_model(config)
    assert config.capability_cache["adapter"] == "openai_chat.v1"
    assert config.capability_cache["canvas_text_protocol"] == protocol
    assert config.capability_cache["canvas_text_defaults"] == {"top_p": 0.7}
    assert not refresh_runtime_model(config)
    catalog = {item["id"]: item for item in provider_catalog()}
    assert catalog[protocol]["enabled"] and catalog[protocol]["executionSupported"]


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_alias_preserves_saved_options_namespace_and_frozen_images(protocol):
    snapshot, request = text_snapshot(), prepared_text()
    snapshot.update(
        canvas_text_protocol=protocol,
        canvas_text_defaults={
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 42,
            "provider": {"order": ["fixture"]},
        },
    )
    request["parameters"] = {"max_output_tokens": 51}
    request["canvas_request"]["input"]["metadata"]["providerOptions"] = {
        protocol: {"top_p": 0.6, "extra_body": {"fixture": True}}
    }
    before = deepcopy((snapshot, request))
    url, headers, body = build_submission(snapshot, request, "openai_chat.v1")
    assert url == "https://provider.example/v1/chat/completions" and headers == {}
    assert body["model"] == "saved-model" and body["max_tokens"] == 51
    assert body["temperature"] == 0.2 and body["top_p"] == 0.6
    assert body["provider"] == {"order": ["fixture"]}
    assert body["extra_body"] == {"fixture": True}
    assert (
        body["messages"][-1]["content"][1]["image_url"]["url"]
        == request["input"]["reference_urls"][0]
    )
    assert (snapshot, request) == before


@pytest.mark.parametrize(
    "defaults",
    [
        {"model": "forged"},
        {"base_url": "https://elsewhere.invalid"},
        {"api_key": "synthetic-private"},
        {"stream": "false"},
        {"max_tokens": True},
    ],
)
def test_alias_rejects_unsafe_or_malformed_provider_options(defaults):
    saved, request = text_snapshot(), prepared_text()
    saved.update(canvas_text_protocol="deepseek-chat", canvas_text_defaults=defaults)
    with pytest.raises(GenerationError):
        build_submission(saved, request, "openai_chat.v1")


def test_alias_tools_preserve_trusted_tool_and_model_with_saved_sampling_options():
    saved, request = tool_snapshot("https://provider.example/v1"), frozen_request()
    saved.update(
        canvas_text_protocol="deepseek-chat",
        canvas_text_defaults={"top_p": 0.6, "provider": {"order": ["fixture"]}},
    )
    _, _, body = build_submission(saved, request, "openai_chat.v1")
    assert body["top_p"] == 0.6 and body["provider"] == {"order": ["fixture"]}
    assert body["tools"][0]["function"]["name"] == "optimize_prompt"
    assert body["tool_choice"] == {"type": "function", "function": {"name": "optimize_prompt"}}


@pytest.mark.parametrize("fallback", [False, True])
def test_alias_http_text_fallback_reasoning_and_credentials_are_private(
    gateway, provider, fallback
):
    base, state, calls = provider
    state["body"] = {
        "choices": [
            {"message": {"reasoning_content": "private saved-secret"}, "finish_reason": "stop"}
        ],
        "usage": {"total_tokens": 3},
    }
    if fallback:
        state["body"]["choices"][0]["text"] = "compatible text"
    else:
        state["body"]["choices"][0]["message"]["content"] = "compatible text"
    saved, request = text_snapshot(), prepared_text()
    saved.update(base_url=base, canvas_text_protocol="deepseek-chat", budget_seconds=5)
    request["canvas_request"]["input"]["textOptions"] = {"stream": False}
    result = gateway.submit(saved, request, "saved-secret", "openai_chat.v1")
    assert result.text == "compatible text" and result.reasoning == "private [redacted]"
    assert result.usage == {"total_tokens": 3} and len(calls) == 1
    assert calls[0][1] == "/v1/chat/completions"
    assert calls[0][2]["Authorization"] == "Bearer saved-secret"


def test_alias_stream_collects_reasoning_and_keeps_truncated_reply_unknown(gateway, provider):
    base, state, calls = provider
    frames = [
        {"choices": [{"delta": {"reasoning_content": "private"}}]},
        {"choices": [{"delta": {"content": "正文"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    state["headers"] = {"Content-Type": "text/event-stream"}
    state["raw"] = (
        "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
    ).encode()
    saved, request = text_snapshot(), prepared_text()
    saved.update(base_url=base, canvas_text_protocol="deepseek-chat", budget_seconds=5)
    result = gateway.submit(saved, request, "saved-secret", "openai_chat.v1")
    assert result.text == "正文" and result.reasoning == "private" and len(calls) == 1
    state["raw"] = state["raw"].split(b"data: [DONE]")[0]
    with pytest.raises(GenerationError) as error:
        gateway.submit(saved, request, "saved-secret", "openai_chat.v1")
    assert error.value.accepted_unknown and len(calls) == 2


@pytest.mark.parametrize("protocol", [True, {}, [], 42, "uninstalled"])
def test_invalid_frozen_protocol_is_a_safe_generation_error(protocol):
    saved, request = text_snapshot(), prepared_text()
    saved["canvas_text_protocol"] = protocol
    with pytest.raises(GenerationError):
        build_submission(saved, request, "openai_chat.v1")


@pytest.mark.parametrize("value", [False, [], 0, "false"])
def test_empty_malformed_namespaces_are_rejected_instead_of_silently_ignored(value):
    saved, request = text_snapshot(), prepared_text()
    saved["canvas_text_protocol"] = "deepseek-chat"
    request["canvas_request"]["input"]["metadata"]["providerOptions"] = value
    with pytest.raises(GenerationError):
        build_submission(saved, request, "openai_chat.v1")


@pytest.mark.parametrize("suffix", ["", "/v1", "/v1/chat/completions", "/gateway", "/gateway/v1"])
def test_alias_endpoint_handles_root_version_full_endpoint_and_gateway_prefix(suffix):
    saved, request = text_snapshot(), prepared_text()
    saved.update(base_url="https://provider.example" + suffix, canvas_text_protocol="deepseek-chat")
    url, _, body = build_submission(saved, request, "openai_chat.v1")
    expected = (
        "/gateway/v1/chat/completions" if suffix.startswith("/gateway") else "/v1/chat/completions"
    )
    assert url == "https://provider.example" + expected
    assert body["model"] == saved["model_key"]


def test_alias_definitive_json_error_is_failed_and_never_resubmitted(gateway, provider):
    base, state, calls = provider
    state["body"] = {"error": {"code": "controlled", "message": "private upstream"}}
    saved, request = text_snapshot(), prepared_text()
    saved.update(base_url=base, canvas_text_protocol="deepseek-chat")
    request["canvas_request"]["input"]["textOptions"] = {"stream": False}
    result = gateway.submit(saved, request, "saved-secret", "openai_chat.v1")
    assert result.status == "failed" and result.error["code"] == "provider_failed"
    assert "private upstream" not in str(result) and len(calls) == 1
