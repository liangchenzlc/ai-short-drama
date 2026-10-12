"""受控真实HTTP验证工具任务；不调用付费模型。"""

import json
from copy import deepcopy

import pytest
from test_canvas_tool_contracts import ADAPTERS, canonical, payload, valid_arguments
from test_generation_adapters import gateway as gateway
from test_generation_adapters import provider as provider

from short_drama.ai import GenerationError
from short_drama.ai.adapters import build_submission, validate_request
from short_drama.ai.canvas_credentials import CanvasCredentials


def frozen_request(*, image=False, stream=False):
    source = canonical(image=image)
    return {
        "input": {"messages": [{"role": "user", "content": "优化提示词"}]},
        "source": {
            "scene": "canvas_tool",
            "project_id": "1",
            "canvas_id": "2",
            "node_key": "node-1",
        },
        "canvas_request": {
            "type": "canvas_text",
            "operation": "text",
            "prompt": "优化提示词",
            "input": {
                "mode": "text",
                "config": {},
                "metadata": {"source": "prompt-optimizer"},
                "agentRequests": {"canonical": deepcopy(source)},
                "textOptions": {"stream": stream, "thinking": False},
                "referenceImages": [{"storageKey": "resource:9007199254740997"}] if image else [],
            },
        },
        "canvas_tool_canonical": source,
        "canvas_text_references": {
            "mode": "text",
            "references": [
                {"media_id": "9007199254740997", "mime_type": "image/png", "byte_size": 90},
            ]
            if image
            else [],
        },
    }


def snapshot(base):
    return {
        "service_type": "text",
        "base_url": base,
        "model_key": "saved-model",
        "budget_seconds": 5,
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_tool",
        "canvas_text_capability": {
            "references": {
                "maxImages": 2,
                "maxImageBytes": 1024,
                "promptMaxChars": 32000,
            }
        },
    }


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_tool_json_http_uses_saved_credentials_and_preserves_tool_only_reply(
    gateway, provider, adapter
):
    base, state, calls = provider
    state["body"] = payload(adapter)
    request = frozen_request()
    before = deepcopy(request)
    result = gateway.submit(
        snapshot(base), request, CanvasCredentials(apiKey="saved-tool-secret"), adapter
    )
    assert request == before
    assert result.status == "succeeded"
    assert result.text == ""
    assert result.reasoning == "私人思考"
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == valid_arguments()
    assert len(calls) == 1
    headers = {key.lower(): value for key, value in calls[0][2].items()}
    if adapter == "claude_messages.v1":
        assert headers["x-api-key"] == "saved-tool-secret"
        assert headers["anthropic-version"] == "2023-06-01"
    elif adapter == "gemini_generate_content.v1":
        assert headers["x-goog-api-key"] == "saved-tool-secret"
        assert calls[0][1] == "/v1beta/models/saved-model:generateContent"
    else:
        assert headers["authorization"] == "Bearer saved-tool-secret"
    assert "saved-tool-secret" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_admission_hydrates_only_placeholders_without_mutating_frozen_media_identity(adapter):
    request = frozen_request(image=True)
    request["input"]["reference_media_ids"] = ["9007199254740997"]
    before = deepcopy(request)
    result = validate_request(snapshot("https://provider.example"), request, adapter)
    assert result["adapter"] == adapter
    assert request == before
    assert "reference.invalid" not in json.dumps(result)
    with pytest.raises(GenerationError, match="unresolved_media_reference"):
        build_submission(snapshot("https://provider.example"), request, adapter)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_tool_http_disconnect_is_unknown_and_never_submits_a_second_post(
    gateway, provider, adapter
):
    base, state, calls = provider
    state["disconnect"] = True
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base), frozen_request(), CanvasCredentials(apiKey="saved-tool-secret"), adapter
        )
    assert caught.value.accepted_unknown
    assert len(calls) == 1


def chat_stream(*, terminal=True):
    arguments = json.dumps(valid_arguments(), ensure_ascii=False)
    split = len(arguments) // 2
    events = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "实际正文",
                        "reasoning_content": "私人思考",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "optimize_prompt",
                                    "arguments": arguments[:split],
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": arguments[split:]}}]
                    },
                    "finish_reason": None,
                }
            ]
        },
    ]
    if terminal:
        events.extend(
            [
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"total_tokens": 20}},
            ]
        )
    return (
        "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)
        + ("data: [DONE]\n\n" if terminal else "")
    ).encode()


def test_chat_stream_assembles_function_arguments_and_observes_only_real_text(gateway, provider):
    base, state, calls = provider
    state.update(headers={"Content-Type": "text/event-stream"}, raw=chat_stream())
    observed = []
    result = gateway.submit(
        snapshot(base),
        frozen_request(stream=True),
        CanvasCredentials(apiKey="saved-tool-secret"),
        "openai_chat.v1",
        on_text_delta=observed.append,
    )
    assert result.text == "实际正文"
    assert result.reasoning == "私人思考"
    assert observed == ["实际正文"]
    assert result.usage == {"total_tokens": 20}
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == valid_arguments()
    assert calls[0][3]["stream_options"] == {"include_usage": True}


def test_incomplete_chat_tool_stream_is_unknown_even_when_arguments_parse(gateway, provider):
    base, state, calls = provider
    state.update(headers={"Content-Type": "text/event-stream"}, raw=chat_stream(terminal=False))
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base),
            frozen_request(stream=True),
            CanvasCredentials(apiKey="saved-tool-secret"),
            "openai_chat.v1",
        )
    assert caught.value.accepted_unknown
    assert len(calls) == 1


def test_responses_tool_stream_uses_terminal_reply_and_keeps_partial_calls_private(
    gateway, provider
):
    base, state, calls = provider
    completed = payload("openai_responses.v1")
    completed["usage"] = {"output_tokens": 12}
    state.update(
        headers={"Content-Type": "text/event-stream"},
        raw=(
            'data: {"type":"response.function_call_arguments.delta","delta":"{partial"}\n\n'
            + "data: "
            + json.dumps({"type": "response.completed", "response": completed}, ensure_ascii=False)
            + "\n\n"
        ).encode(),
    )
    observed = []
    result = gateway.submit(
        snapshot(base),
        frozen_request(stream=True),
        CanvasCredentials(apiKey="saved-tool-secret"),
        "openai_responses.v1",
        on_text_delta=observed.append,
    )
    assert result.text == ""
    assert result.usage == {"output_tokens": 12}
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == valid_arguments()
    assert observed == []
    assert len(calls) == 1


def test_secret_echo_in_tool_arguments_is_redacted_after_json_decoding(gateway, provider):
    base, state, _calls = provider
    arguments = valid_arguments()
    arguments["optimizedPrompt"] = 'quoted"tool-secret and saved-tool-secret'
    state["body"] = payload("openai_chat.v1", arguments=arguments)
    result = gateway.submit(
        snapshot(base),
        frozen_request(),
        CanvasCredentials(apiKey='quoted"tool-secret'),
        "openai_chat.v1",
    )
    assert 'quoted"tool-secret' not in result.tool_calls[0]["function"]["arguments"]
    assert (
        json.loads(result.tool_calls[0]["function"]["arguments"])["optimizedPrompt"]
        == "[redacted] and saved-tool-secret"
    )


def test_secret_redaction_cannot_persist_a_reply_that_violates_the_fixed_schema(gateway, provider):
    base, state, calls = provider
    name = "review_art_composition"
    request = frozen_request()
    source = canonical(name)
    request["canvas_tool_canonical"] = source
    request["canvas_request"]["input"]["agentRequests"]["canonical"] = deepcopy(source)
    request["canvas_request"]["input"]["metadata"] = {"source": "art-critique", "stage": name}
    arguments = {
        "candidates": [
            {
                "id": "issue-1",
                "checkId": "check-1",
                "kind": "issue",
                "category": "composition",
                "title": "主体位置",
                "observation": "主体偏离",
                "reason": "影响观看",
                "evidence": [],
                "severity": 0.5,
                "confidence": 0.7,
                "targetDescription": "主体",
            }
        ]
    }
    state["body"] = payload("openai_chat.v1", name=name, arguments=arguments)
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base), request, CanvasCredentials(apiKey="composition"), "openai_chat.v1"
        )
    assert caught.value.code == "invalid_response"
    assert caught.value.accepted_unknown
    assert len(calls) == 1


@pytest.mark.parametrize("scene", ["canvas_node", "canvas_model_test", "novel_script"])
def test_tool_branch_is_unavailable_to_other_scenes(gateway, provider, scene):
    base, _state, calls = provider
    request = frozen_request()
    request["source"]["scene"] = scene
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        gateway.submit(
            snapshot(base), request, CanvasCredentials(apiKey="saved-tool-secret"), "openai_chat.v1"
        )
    assert calls == []


def claude_stream(*, closed=True, stopped=True):
    arguments = json.dumps(valid_arguments(), ensure_ascii=False)
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "私人思考"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "实际正文"},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "call-1",
                "name": "optimize_prompt",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": arguments[:30]},
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": arguments[30:]},
        },
    ]
    if closed:
        events.append({"type": "content_block_stop", "index": 2})
    if stopped:
        events.extend(
            [
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 12},
                },
                {"type": "message_stop"},
            ]
        )
    return "".join(
        "data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events
    ).encode()


@pytest.mark.parametrize("stopped", [True, False])
def test_claude_stream_assembles_tool_json_and_preserves_closed_block_eof(
    gateway, provider, stopped
):
    base, state, calls = provider
    state.update(headers={"Content-Type": "text/event-stream"}, raw=claude_stream(stopped=stopped))
    observed = []
    result = gateway.submit(
        snapshot(base),
        frozen_request(stream=True),
        CanvasCredentials(apiKey="saved-tool-secret"),
        "claude_messages.v1",
        on_text_delta=observed.append,
    )
    assert result.text == "实际正文"
    assert result.reasoning == "私人思考"
    assert observed == ["实际正文"]
    assert result.usage["input_tokens"] == 4
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == valid_arguments()
    assert len(calls) == 1


def test_open_claude_tool_block_is_unknown_even_with_parseable_arguments(gateway, provider):
    base, state, calls = provider
    state.update(
        headers={"Content-Type": "text/event-stream"},
        raw=claude_stream(closed=False, stopped=False),
    )
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base),
            frozen_request(stream=True),
            CanvasCredentials(apiKey="saved-tool-secret"),
            "claude_messages.v1",
        )
    assert caught.value.accepted_unknown
    assert len(calls) == 1


def test_gemini_tool_task_ignores_stream_request_and_encodes_model_as_one_segment(
    gateway, provider
):
    base, state, calls = provider
    state["body"] = payload("gemini_generate_content.v1")
    config = {**snapshot(base + "/proxy/api/plan/v3"), "model_key": "model/with#?suffix"}
    result = gateway.submit(
        config,
        frozen_request(stream=True),
        CanvasCredentials(apiKey="saved-tool-secret"),
        "gemini_generate_content.v1",
    )
    assert result.tool_calls
    assert calls[0][1] == "/proxy/v1beta/models/model%2Fwith%23%3Fsuffix:generateContent"
    assert "stream" not in calls[0][3]
    assert "reference.invalid" not in json.dumps(result.resolved_parameters)


def test_confirmed_chat_tool_choice_rejection_uses_auto_then_omits_choice(
    gateway, provider, monkeypatch
):
    base, state, calls = provider
    send = gateway.transport.request
    deadlines = []

    def controlled_response(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        if "tool_choice" in kwargs["body"]:
            state.update(
                status=400,
                body={"error": {"message": "tool_choice must be omitted in thinking mode"}},
            )
        else:
            state.update(status=200, body=payload("openai_chat.v1"))
        return send(*args, **kwargs)

    monkeypatch.setattr(gateway.transport, "request", controlled_response)
    request = frozen_request()
    before = deepcopy(request)
    result = gateway.submit(
        snapshot(base), request, CanvasCredentials(apiKey="saved-tool-secret"), "openai_chat.v1"
    )
    assert result.status == "succeeded"
    assert request == before
    assert len(calls) == 3
    assert len(set(deadlines)) == 1
    assert isinstance(calls[0][3]["tool_choice"], dict)
    assert calls[1][3]["tool_choice"] == "auto"
    assert "tool_choice" not in calls[2][3]
    assert all(call[3]["tools"][0]["function"]["name"] == "optimize_prompt" for call in calls)


def test_auto_tool_choice_422_rejection_only_omits_choice(gateway, provider, monkeypatch):
    base, state, calls = provider
    send = gateway.transport.request

    def controlled_response(*args, **kwargs):
        if "tool_choice" in kwargs["body"]:
            state.update(
                status=422, body={"error": {"param": "tool_choice", "message": "unsupported"}}
            )
        else:
            state.update(status=200, body=payload("openai_chat.v1"))
        return send(*args, **kwargs)

    monkeypatch.setattr(gateway.transport, "request", controlled_response)
    request = frozen_request()
    request["canvas_tool_canonical"]["toolChoice"] = "auto"
    request["canvas_request"]["input"]["agentRequests"]["canonical"]["toolChoice"] = "auto"
    result = gateway.submit(
        snapshot(base), request, CanvasCredentials(apiKey="saved-tool-secret"), "openai_chat.v1"
    )
    assert result.tool_calls
    assert len(calls) == 2
    assert calls[0][3]["tool_choice"] == "auto"
    assert "tool_choice" not in calls[1][3]


def test_tool_choice_rejections_stop_after_all_three_choices(gateway, provider):
    base, state, calls = provider
    state.update(status=400, body={"error": {"message": "tool-choice is unsupported"}})
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base),
            frozen_request(),
            CanvasCredentials(apiKey="saved-tool-secret"),
            "openai_chat.v1",
        )
    assert caught.value.code == "provider_rejected"
    assert not caught.value.accepted_unknown
    assert len(calls) == 3


@pytest.mark.parametrize(
    "adapter", ["openai_responses.v1", "claude_messages.v1", "gemini_generate_content.v1"]
)
def test_other_protocols_never_use_chat_tool_choice_fallback(gateway, provider, adapter):
    base, state, calls = provider
    state.update(status=400, body={"error": {"message": "tool_choice unsupported"}})
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base), frozen_request(), CanvasCredentials(apiKey="saved-tool-secret"), adapter
        )
    assert caught.value.code == "provider_rejected"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,message,unknown",
    [
        (500, "tool_choice unavailable", True),
        (400, "model is unavailable", False),
        (401, "tool_choice auth", False),
    ],
)
def test_generic_or_uncertain_http_error_never_uses_tool_choice_fallback(
    gateway, provider, status, message, unknown
):
    base, state, calls = provider
    state.update(status=status, body={"error": {"message": message}})
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            snapshot(base),
            frozen_request(),
            CanvasCredentials(apiKey="saved-tool-secret"),
            "openai_chat.v1",
        )
    assert caught.value.accepted_unknown is unknown
    assert message not in str(caught.value)
    assert len(calls) == 1


def test_client_tool_transport_or_frozen_canonical_mismatch_fails_before_http(gateway, provider):
    base, _state, calls = provider
    request = frozen_request()
    request["canvas_request"]["input"]["agentRequests"]["canonical"]["systemPrompt"] = "changed"
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        gateway.submit(
            snapshot(base), request, CanvasCredentials(apiKey="saved-tool-secret"), "openai_chat.v1"
        )
    assert calls == []
