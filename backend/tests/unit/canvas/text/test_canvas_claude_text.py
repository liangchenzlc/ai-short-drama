"""固定源普通 Claude 合同；合成凭据和受控响应不代表供应商验收。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai.adapters.results import parse_result
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.ai.gateway.client import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.service.ai_config.management.runtime import refresh_runtime_model

ADAPTER = "claude_messages.v1"
KEY = "synthetic-claude-test-key"


def snapshot(**patch):
    return {
        "base_url": "https://provider.example/proxy/v1",
        "model_key": "claude-fixture",
        "service_type": "text",
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_node",
        **patch,
    }


def request(*, options=None, config=None, stream=True, parameters=None):
    return {
        "source": {"scene": "canvas_node"},
        "input": {
            "messages": [
                {"role": "system", "content": "历史系统消息不拼入指令"},
                {"role": "user", "content": "上一次问题"},
                {"role": "assistant", "content": "上一次回答"},
                {"role": "user", "content": "当前问题"},
            ]
        },
        "parameters": parameters or {},
        "canvas_request": {
            "type": "canvas_text",
            "operation": "text",
            "prompt": "当前问题",
            "input": {
                "mode": "text",
                "config": config or {},
                "textOptions": {"stream": stream, "thinking": False},
                "metadata": {"providerOptions": {"claude-api": options or {}}},
            },
        },
        "canvas_text_references": {"mode": "text", "references": []},
    }


def event(value, *, multiline=False):
    raw = json.dumps(value, ensure_ascii=False)
    if multiline:
        raw = raw.replace(', "delta":', ',\r\ndata: "delta":')
    return ("data: " + raw + "\r\n\r\n").encode()


def gateway(payload, *, content_type="text/event-stream", credentials=None):
    instance = GenerationGateway(SimpleNamespace())
    calls, deltas = [], []

    def exchange(method, url, **kwargs):
        calls.append((method, url, kwargs))
        headers = {"Content-Type": content_type}
        if "on_headers" in kwargs:
            kwargs["on_headers"](200, headers)
            for byte in payload:
                kwargs["on_chunk"](bytes([byte]))
        return 200, headers, payload

    instance.transport = SimpleNamespace(request=exchange)
    credential = credentials or CanvasCredentials(apiKey=KEY)
    result = instance.submit(
        snapshot(), request(), credential, ADAPTER, on_text_delta=deltas.append
    )
    return result, calls, deltas


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://provider.example", "https://provider.example/v1/messages"),
        ("https://provider.example/v1", "https://provider.example/v1/messages"),
        ("https://provider.example/proxy/v1", "https://provider.example/proxy/v1/messages"),
        (
            "https://provider.example/proxy/v1/messages",
            "https://provider.example/proxy/v1/messages",
        ),
    ],
)
def test_plain_claude_preserves_proxy_path_and_uses_top_level_system(base, expected):
    data = request(config={"systemPrompt": "  本次指令  "}, stream=False)
    before = deepcopy(data)
    url, headers, body = build_submission(snapshot(base_url=base), data, ADAPTER)
    assert url == expected
    assert headers == {"anthropic-version": "2023-06-01"}
    assert body == {
        "model": "claude-fixture",
        "max_tokens": 4096,
        "system": "本次指令",
        "messages": data["input"]["messages"][1:],
        "stream": False,
    }
    assert data == before


@pytest.mark.parametrize(
    ("defaults", "options", "parameters", "expected"),
    [
        ({}, {}, {}, 4096),
        ({"max_tokens": 321}, {}, {}, 321),
        ({"max_tokens": 321}, {"max_tokens": 654}, {}, 654),
        ({"max_tokens": 321}, {"max_tokens": 654}, {"max_output_tokens": 987}, 987),
    ],
)
def test_saved_defaults_request_options_and_explicit_output_limit_have_source_priority(
    defaults, options, parameters, expected
):
    _, _, body = build_submission(
        snapshot(canvas_text_defaults=defaults),
        request(options=options, parameters=parameters),
        ADAPTER,
    )
    assert body["max_tokens"] == expected
    assert "thinking" not in body and "tools" not in body


def test_source_provider_options_are_mapped_without_reintroducing_system_history():
    options = {
        "system": "默认指令",
        "temperature": 0.4,
        "top_p": 0.8,
        "top_k": 20,
        "stop_sequences": ["停止"],
        "metadata": {"user_id": "fixture"},
        "service_tier": "auto",
        "anthropic-version": "2024-01-01",
        "anthropic-beta": "fixture",
    }
    _, headers, body = build_submission(snapshot(), request(options=options), ADAPTER)
    assert headers == {"anthropic-version": "2024-01-01", "anthropic-beta": "fixture"}
    assert body["system"] == "默认指令"
    assert all(message["role"] != "system" for message in body["messages"])
    for key in ("temperature", "top_p", "top_k", "stop_sequences", "metadata", "service_tier"):
        assert body[key] == options[key]


def test_claude_auth_uses_x_api_key_and_preserves_explicit_version_without_case_duplicates():
    credential = CanvasCredentials(
        apiKey=KEY,
        headers=[
            {"name": "Anthropic-Version", "value": "2025-01-01"},
            {"name": "X-Api-Key", "value": "synthetic-header-key"},
        ],
    )
    result, calls, deltas = gateway(
        json.dumps({"content": [{"type": "text", "text": "正文"}]}).encode(),
        content_type="application/json",
        credentials=credential,
    )
    headers = calls[0][2]["headers"]
    assert [key for key in headers if key.lower() == "anthropic-version"] == ["anthropic-version"]
    assert headers["anthropic-version"] == "2025-01-01"
    assert headers["x-api-key"] == KEY
    assert all(key.lower() != "authorization" for key in headers)
    assert result.text == "正文" and deltas == [] and len(calls) == 1


def test_claude_json_keeps_only_trimmed_text_blocks_and_separate_reasoning():
    result = parse_result(
        {
            "content": [
                {"type": "text", "text": "  第一段  "},
                {"type": "thinking", "thinking": " 私人推理 "},
                {"type": "tool_use", "name": "ignored", "input": {"text": "不能混进正文"}},
                {"type": "text", "text": "\n第二段\n"},
            ],
            "usage": {"input_tokens": 2, "output_tokens": 3},
            "stop_reason": "end_turn",
        },
        ADAPTER,
        submitted=True,
    )
    assert result.text == "第一段第二段" and result.reasoning == "私人推理"
    assert result.usage == {"input_tokens": 2, "output_tokens": 3}
    assert result.finish_reason == "stop"


def test_claude_stream_accepts_valid_text_at_eof_and_keeps_thinking_out_of_deltas():
    data = b"\xef\xbb\xbf: keepalive\r\n\r\n" + b"".join(
        [
            event({"type": "message_start", "message": {"usage": {"input_tokens": 2}}}),
            event(
                {"type": "content_block_start", "content_block": {"type": "text", "text": " 起始"}}
            ),
            event(
                {
                    "type": "content_block_start",
                    "content_block": {"type": "thinking", "thinking": "私有"},
                }
            ),
            event(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "摘要"},
                }
            ),
            event(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "中文🌈结尾 "},
                },
                multiline=True,
            ),
            event(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 3},
                }
            ),
        ]
    ).rstrip(b"\r\n")
    result, calls, deltas = gateway(data)
    assert result.text == " 起始中文🌈结尾 "
    assert result.reasoning == "私有摘要"
    assert "".join(deltas) == result.text
    assert result.usage == {"input_tokens": 2, "output_tokens": 3}
    assert len(calls) == 1


def test_claude_stream_redacts_credentials_across_text_and_reasoning_events():
    data = b"".join(
        [
            event(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "正文synthetic-"},
                }
            ),
            event(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "claude-test-key结尾"},
                }
            ),
            event(
                {
                    "type": "content_block_start",
                    "content_block": {"type": "thinking", "thinking": KEY},
                }
            ),
        ]
    )
    result, _, deltas = gateway(data)
    assert "".join(deltas) == "正文[redacted]结尾"
    assert result.reasoning == "[redacted]" and KEY not in repr(result)


@pytest.mark.parametrize(
    "payload",
    [
        b"data: {bad}\n\n",
        b"data: \xff\n\n",
        event(
            {
                "type": "content_block_start",
                "content_block": {"type": "thinking", "thinking": "只有推理"},
            }
        ),
        event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": 3}}),
    ],
)
def test_malformed_or_empty_claude_stream_remains_unknown(payload):
    with pytest.raises(GenerationError) as caught:
        gateway(payload)
    assert caught.value.code == "invalid_response" and caught.value.accepted_unknown


def test_explicit_supplier_error_is_failed_even_after_a_text_delta():
    data = event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "已输出"}})
    data += event({"type": "error", "error": {"type": "overloaded_error", "message": KEY}})
    with pytest.raises(GenerationError) as caught:
        gateway(data)
    assert caught.value.code == "provider_rejected" and KEY not in str(caught.value)


@pytest.mark.parametrize(
    "patch",
    [
        {"source": {"scene": "novel_script"}},
        {"canvas_request": None},
        {
            "input": {
                "messages": [{"role": "user", "content": "问题"}],
                "reference_media_ids": ["1"],
            }
        },
    ],
)
def test_claude_plain_recipe_cannot_change_standard_or_multimodal_paths(patch):
    with pytest.raises(GenerationError):
        build_submission(snapshot(), {**request(), **patch}, ADAPTER)


def test_saved_claude_runtime_cache_refreshes_from_profile_without_changing_identity():
    config = SimpleNamespace(
        id=17,
        apikey="synthetic-encrypted",
        runtime_credentials_cipher=None,
        base_url="https://provider.example",
        model_key="claude-fixture",
        service_type="text",
        capability_cache=None,
        runtime_profile={
            "version": 1,
            "api_format": "claude",
            "protocol": "claude-api",
            "default_options": {"max_tokens": 345},
            "capability_config": {"text": {"streaming": True}},
        },
    )
    before = deepcopy(vars(config))
    assert refresh_runtime_model(config, "host-17") is True
    assert config.capability_cache["adapter"] == ADAPTER
    assert config.capability_cache["canvas_text_defaults"] == {"max_tokens": 345}
    assert config.capability_cache["canvas_text_capability"] == {"streaming": True}
    assert refresh_runtime_model(config, "host-17") is False
    assert all(
        getattr(config, key) == value for key, value in before.items() if key != "capability_cache"
    )
