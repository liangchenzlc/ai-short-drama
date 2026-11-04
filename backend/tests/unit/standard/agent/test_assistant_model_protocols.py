"""The unique framework model factory preserves wire and accounting contracts."""

import asyncio
import base64
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRetryMiddleware,
    SummarizationMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langchain_core.tools import tool
from langgraph.errors import NodeCancelledError
from pydantic import BaseModel

from short_drama.agent.gateway.contracts import AgentGatewayError, ModelCallbacks
from short_drama.agent.gateway.langchain import LangChainGateway, build_chat_model
from short_drama.agent.resources.contracts import ResourceRef, assert_reference_payload
from short_drama.agent.resources.graph import GraphResources
from short_drama.agent.resources.resolver import HydratedResource, image_block
from short_drama.agent.runtime.agent import assistant_middleware
from tests.support.assistant_http import provider as provider
from tests.support.assistant_http import response, snapshot, sse_response
from tests.support.assistant_model import ScriptedModel

SETTINGS = SimpleNamespace(
    generation_allowed_hosts=["127.0.0.1"], generation_max_response_bytes=65536
)
TOOL = {
    "name": "lookup",
    "description": "Look up facts",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def test_plaintext_http_rejection_keeps_provider_classification(provider):
    base, state = provider
    state["responses"] = [(415, b"Unsupported media type", "text/plain")]
    errors, raw = [], []

    async def save_raw(payload):
        raw.append(payload)

    async def error(payload):
        errors.append(payload)

    model = build_chat_model(
        configured(base, "chat"),
        "",
        callbacks=ModelCallbacks(
            settings=SETTINGS,
            save_raw=save_raw,
            error=error,
        ),
    )
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(model.ainvoke([HumanMessage(content="检查明确拒绝")]))
    assert caught.value.code == "agent_provider_rejected" and not caught.value.accepted_unknown
    assert len(state["calls"]) == 1
    assert raw[0]["status_code"] == 415 and raw[0]["body_b64"] == ""
    assert errors[0]["not_accepted"] is True


def assistant_retry(model, tools=()):
    middleware = assistant_middleware(
        model=model,
        tools=tools,
        snapshot={
            "assistant_settings": {"tool_selector_prompt": "Select tools", "model_call_limit": 9}
        },
    )
    return next(item for item in middleware if isinstance(item, ModelRetryMiddleware))


class ModelAttempts(AgentMiddleware):
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    async def awrap_model_call(self, request, handler):
        self.calls += 1
        if self.calls == 1 and self.error is not None:
            raise self.error
        return await handler(request)


class RetryWireTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, *, body, on_send, on_headers, on_chunk, **kwargs):
        assert method == "POST" and url == "https://fixture.invalid/v1/chat/completions"
        on_send()
        self.calls.append(deepcopy(body))
        content = self.replies.pop(0)
        headers = {"Content-Type": "text/event-stream"}
        on_headers(200, headers)
        on_chunk(content)
        return 200, headers, content


@pytest.mark.parametrize("invalid_count", [1, 3])
def test_official_retry_regenerates_invalid_arguments_with_a_bounded_request_count(
    invalid_count, record_property
):
    executed = []

    @tool
    def lookup(value: str) -> str:
        """Record only a valid regenerated call."""
        executed.append(value)
        return value

    invalid = sse_response("chat", calls=[("bad", "lookup", {})]).replace(
        json.dumps("{}").encode(), json.dumps('{"value":"must-not-run" "extra":true}').encode()
    )
    replies = [invalid] * invalid_count + [
        sse_response("chat", calls=[("valid", "lookup", {"value": "accepted"})]),
        sse_response("chat", text="Complete"),
    ]
    transport = RetryWireTransport(
        reply.replace(b"chatcmpl-local", f"chatcmpl-{index}".encode())
        for index, reply in enumerate(replies)
    )
    admitted, raw, usage, errors = [], [], [], []

    async def before_send(value):
        admitted.append(value)

    async def save_raw(value):
        raw.append(value)

    async def record_usage(value):
        usage.append(value)

    async def record_error(value):
        errors.append(value)

    config = {**configured("https://fixture.invalid/v1", "chat"), "provider": "deepseek"}
    callbacks = ModelCallbacks(
        settings=SETTINGS,
        before_send=before_send,
        save_raw=save_raw,
        usage=record_usage,
        error=record_error,
    )
    model = build_chat_model(config, "fixture-key", callbacks=callbacks, transport=transport)
    graph = create_agent(model, tools=[lookup], middleware=[assistant_retry(model, [lookup])])
    inputs = {"messages": [HumanMessage(content="Record the valid value")]}
    if invalid_count == 3:
        with pytest.raises(AgentGatewayError) as failure:
            asyncio.run(graph.ainvoke(inputs))
        assert failure.value.code == "invalid_agent_tool_calls"
        assert failure.value.requests == 1 and not failure.value.accepted_unknown
        assert executed == [] and len(transport.replies) == 2
    else:
        result = asyncio.run(graph.ainvoke(inputs))
        assert executed == ["accepted"]
        assert result["messages"][-1].content == "Complete"
        assert [
            item.tool_call_id for item in result["messages"] if isinstance(item, ToolMessage)
        ] == ["valid"]
        assert transport.calls[0]["messages"] == transport.calls[1]["messages"]
    assert len(transport.calls) == len(admitted) == len(raw) == len(usage) == 3
    assert len(errors) == invalid_count
    identities = [item["model_call_id"] for item in admitted]
    assert len(set(identities)) == 3
    assert [item["model_call_id"] for item in usage] == identities
    assert [item["model_call_id"] for item in raw] == identities
    assert [item["model_call_id"] for item in errors] == identities[:invalid_count]
    assert all(
        item["input_tokens"] == 7
        and item["output_tokens"] == 3
        and item["external_requests"] == 1
        and item["usage_reported"]
        for item in usage
    )
    assert all(
        item["code"] == "invalid_agent_tool_calls"
        and item["requests"] == 1
        and not item["accepted_unknown"]
        and not item["not_accepted"]
        for item in errors
    )
    record_property(
        "safe_model_receipts",
        json.dumps({"model_call_ids": identities, "usage": usage, "errors": errors}),
    )
    # The same bad saved response remains a replay error, never a fresh generation.
    replay = build_chat_model(
        config,
        "fixture-key",
        callbacks=replace(callbacks, request=admitted[0], raw=raw[0]),
        transport=transport,
    )
    attempts = ModelAttempts()
    replay_graph = create_agent(
        replay, tools=[lookup], middleware=[assistant_retry(replay, [lookup]), attempts]
    )
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(replay_graph.ainvoke(inputs))
    assert failure.value.code == "invalid_agent_tool_calls" and failure.value.requests == 0
    assert attempts.calls == 1
    assert len(transport.calls) == len(admitted) == len(raw) == len(usage) == 3
    assert len(errors) == invalid_count


@pytest.mark.parametrize(
    "error,expected_calls",
    [
        (ValueError("Model selected invalid tools: ['missing']"), 2),
        (AgentGatewayError("invalid_agent_tool_calls", requests=1, accepted_unknown=True), 1),
        (AgentGatewayError("invalid_agent_tool_calls", requests=0), 1),
        (AgentGatewayError("agent_context_budget_exhausted", requests=1), 1),
        (AgentGatewayError("agent_provider_authentication", requests=1), 1),
        (AgentGatewayError("agent_response_truncated", requests=1), 1),
        (AgentGatewayError("agent_stream_cancelled", requests=1), 1),
        (asyncio.CancelledError(), 1),
        (ValueError("unrelated"), 1),
    ],
)
def test_official_retry_preserves_selector_and_nonrecoverable_error_boundaries(
    error, expected_calls
):
    model = ScriptedModel(replies=[AIMessage(content="Complete")])
    attempts = ModelAttempts(error)
    graph = create_agent(model, middleware=[assistant_retry(model), attempts])
    invoke = graph.ainvoke({"messages": [HumanMessage(content="Proceed")]})
    if expected_calls == 1:
        cancelled = isinstance(error, asyncio.CancelledError)
        with pytest.raises(NodeCancelledError if cancelled else type(error)) as failure:
            asyncio.run(invoke)
        assert (failure.value.__cause__ if cancelled else failure.value) is error
        assert model.requests == []
    else:
        assert asyncio.run(invoke)["messages"][-1].content == "Complete"
        assert len(model.requests) == 1
    assert attempts.calls == expected_calls


def configured(base, protocol):
    config = snapshot(base, protocol)
    if protocol == "claude":
        config.update(
            base_url=base, runtime_profile={"api_format": "claude", "protocol": "messages"}
        )
    return config


def claude_response():
    return {
        "id": "local",
        "type": "message",
        "role": "assistant",
        "model": "fixture",
        "content": [{"type": "text", "text": "understood"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_factory_hydrates_wire_but_persists_only_resource_references(provider, protocol):
    base, state = provider
    state["responses"] = [
        (200, claude_response() if protocol == "claude" else response(protocol, text="understood"))
    ]
    pixels = base64.b64encode(b"managed-test-pixels").decode()
    resource = HydratedResource(
        ResourceRef(
            kind="image",
            ref_id="view:1",
            uri="minio://test/processed.jpg",
            source="hydrated_image",
            checksum="a" * 64,
            metadata={"detail": "low"},
        ),
        pixels,
        "image/jpeg",
        "tool_result",
        "ephemeral",
    )
    native_protocol = {
        "chat": "openai_chat.v1",
        "responses": "openai_responses.v1",
        "claude": "claude_messages.v1",
    }[protocol]
    block = image_block(resource, native_protocol)
    messages = [
        HumanMessage(content="Inspect"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "image"}]),
    ]
    messages += (
        [
            ToolMessage(content="image_ref:view:1", tool_call_id="image"),
            HumanMessage(content=[block]),
        ]
        if protocol == "chat"
        else [ToolMessage(content=[block], tool_call_id="image")]
    )
    messages.extend(
        [
            AIMessage(
                content='{"task_working_notes":{"items":[]}}',
                additional_kwargs={"task_context_kind": "working_notes"},
            ),
            HumanMessage(content='{"task_user_sources":{"items":[{"content":"Inspect"}]}}'),
        ]
    )
    saved, receipts = [], []

    async def capture(value):
        saved.append(value)

    async def usage(value):
        receipts.append(value)

    model = build_chat_model(
        configured(base, protocol),
        "fixture-key",
        callbacks=ModelCallbacks(
            settings=SETTINGS,
            before_send=capture,
            usage=usage,
            resources=(resource,),
            max_output_tokens=64,
            stream=False,
        ),
    )
    result = asyncio.run(model.bind_tools([TOOL]).ainvoke(messages))
    assert result.text == "understood"
    assert len(state["calls"]) == len(receipts) == 1
    assert pixels in json.dumps(state["calls"][0][2])
    assert pixels not in json.dumps(saved) and "image_ref" in json.dumps(saved)
    assert_reference_payload(saved)
    wire = state["calls"][0][2]
    serialized = json.dumps(wire)
    assert "task_working_notes" in serialized and "task_user_sources" in serialized
    assert not wire.get("continue_final_message")
    if protocol == "claude":
        assert wire["messages"][-1]["role"] == "user"
        native = next(
            part
            for message in wire["messages"]
            for part in message["content"]
            if isinstance(part, dict) and part["type"] == "tool_result"
        )
        assert native["type"] == "tool_result"
        assert native["content"][0]["source"]["type"] == "base64"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_factory_stream_records_actual_usage(provider, protocol):
    base, state = provider
    state["responses"] = [(200, sse_response(protocol, text="step|done"), "text/event-stream")]
    receipts = []

    async def usage(value):
        receipts.append(value)

    model = build_chat_model(
        configured(base, protocol),
        "fixture-key",
        callbacks=ModelCallbacks(settings=SETTINGS, usage=usage, max_output_tokens=64),
    )
    assert asyncio.run(model.ainvoke([HumanMessage(content="Proceed")])).text == "stepdone"
    assert len(state["calls"]) == len(receipts) == 1
    assert receipts[0]["output_tokens"] == 3


@pytest.mark.parametrize("stream", [False, True])
def test_factory_admission_rejection_never_sends(provider, stream):
    base, state = provider

    async def reject(_request):
        raise AgentGatewayError("agent_context_budget_exhausted")

    model = build_chat_model(
        configured(base, "chat"),
        "fixture-key",
        callbacks=ModelCallbacks(settings=SETTINGS, before_send=reject, stream=stream),
    )
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(model.ainvoke([HumanMessage(content="Proceed")]))
    assert failure.value.code == "agent_context_budget_exhausted"
    assert failure.value.requests == 0 and not failure.value.accepted_unknown
    assert not state["calls"]


@pytest.mark.parametrize("status", [400, 401, 429, 500])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider_name", ["compatible", "deepseek"])
def test_factory_http_failures_preserve_safe_acceptance_facts(
    provider, status, stream, provider_name
):
    base, state = provider
    state["responses"] = [(status, {"error": {"message": "private provider detail"}})]
    model = build_chat_model(
        {**configured(base, "chat"), "provider": provider_name},
        "fixture-key",
        callbacks=ModelCallbacks(settings=SETTINGS, stream=stream),
    )
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(model.ainvoke([HumanMessage(content="Proceed")]))
    assert (
        failure.value.code
        == {
            400: "agent_provider_rejected",
            401: "agent_provider_authentication",
            429: "agent_provider_rate_limited",
            500: "agent_provider_unknown",
        }[status]
    )
    assert failure.value.http_status == status
    assert failure.value.accepted_unknown == (status >= 500)
    assert "private provider detail" not in str(failure.value)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_capability_probe_uses_real_framework_continuation(provider, protocol):
    base, state = provider
    # Responses 的消息 ID 参与 graph 去重，两次真实响应不会复用同一身份。
    final = (
        sse_response(protocol, text="AGENT_CAPABILITY_OK")
        .replace(b"resp_local", b"resp_final")
        .replace(b"msg_local", b"msg_final")
    )
    state["responses"] = [
        (
            200,
            sse_response(
                protocol, calls=[("probe", "verify_agent_echo", {"value": "agent-capability-v1"})]
            ),
            "text/event-stream",
        ),
        (200, final, "text/event-stream"),
    ]
    evidence = asyncio.run(
        LangChainGateway(SETTINGS).validate_capability(
            snapshot(base, protocol), "fixture-key", conversation_id="local-probe"
        )
    )
    assert evidence.tool_calling and evidence.tool_result_continuation
    assert evidence.requests == len(state["calls"]) == 2


@pytest.mark.parametrize("stream", [False, True])
def test_deepseek_structured_selector_disables_thinking_only_for_its_request(provider, stream):
    class Selection(BaseModel):
        tools: list[str]

    base, state = provider
    replies = [
        {"calls": [("selector", "Selection", {"tools": ["lookup"]})]},
        {"text": "继续创作"},
    ]
    state["responses"] = [
        (200, sse_response("chat", **reply), "text/event-stream")
        if stream
        else (200, response("chat", **reply))
        for reply in replies
    ]
    admitted, usage = [], []

    async def admit(payload):
        admitted.append(payload)

    async def record_usage(payload):
        usage.append(payload)

    model = build_chat_model(
        {**configured(base, "chat"), "provider": "deepseek"},
        "fixture-key",
        callbacks=ModelCallbacks(
            settings=SETTINGS, stream=stream, before_send=admit, usage=record_usage
        ),
    )

    async def invoke():
        selected = await model.with_structured_output(Selection).ainvoke(
            [HumanMessage(content="选择工具")],
            config={"metadata": {"lc_source": "tool_selection"}},
        )
        assert selected.tools == ["lookup"]
        return await model.bind_tools([TOOL], tool_choice="auto").ainvoke(
            [HumanMessage(content="继续")]
        )

    assert asyncio.run(invoke()).text == "继续创作"
    selector, main = [call[2] for call in state["calls"]]
    assert selector["tool_choice"] == "required"
    assert selector["thinking"] == {"type": "disabled"}
    assert main["tool_choice"] == "auto" and "thinking" not in main
    assert admitted[0]["body"]["thinking"] == {"type": "disabled"}
    assert [item["source"] for item in usage] == ["tool_selection", "assistant"]
    assert len(state["calls"]) == len(admitted) == len(usage) == 2


@pytest.mark.parametrize(
    "provider_name,protocol,stream",
    [
        ("deepseek", "chat", False),
        ("deepseek", "chat", True),
        ("compatible", "chat", False),
        ("compatible", "chat", True),
        ("deepseek", "responses", False),
        ("deepseek", "responses", True),
        ("anthropic", "claude", False),
    ],
)
def test_factory_disables_summary_thinking_only_for_deepseek_chat(
    provider, provider_name, protocol, stream
):
    base, state = provider
    reply = (
        (200, sse_response(protocol, text="understood"), "text/event-stream")
        if stream
        else (
            200,
            claude_response() if protocol == "claude" else response(protocol, text="understood"),
        )
    )
    state["responses"] = [reply, reply]
    admitted, raw, usage = [], [], []

    async def admit(payload):
        admitted.append(payload)

    async def save_raw(payload):
        raw.append(payload)

    async def record_usage(payload):
        usage.append(payload)

    config = {**configured(base, protocol), "provider": provider_name}
    callbacks = ModelCallbacks(
        settings=SETTINGS,
        stream=stream,
        before_send=admit,
        save_raw=save_raw,
        usage=record_usage,
        max_output_tokens=64,
    )
    model = build_chat_model(config, "fixture-key", callbacks=callbacks)
    messages = [HumanMessage(content="总结已有内容")]
    summary_config = {"metadata": {"lc_source": "summarization"}}
    assert asyncio.run(model.ainvoke(messages, config=summary_config)).text == "understood"
    assert asyncio.run(model.ainvoke(messages)).text == "understood"
    summary, main = [call[2] for call in state["calls"]]
    if provider_name == "deepseek" and protocol == "chat":
        assert summary.get("thinking") == {"type": "disabled"}
        assert admitted[0]["body"]["thinking"] == {"type": "disabled"}
    else:
        assert "thinking" not in summary
    assert "thinking" not in main
    assert [item["source"] for item in admitted] == ["summarization", "assistant"]
    assert [item["source"] for item in usage] == ["summarization", "assistant"]
    replay = build_chat_model(
        config,
        "fixture-key",
        callbacks=replace(callbacks, request=admitted[0], raw=raw[0]),
    )
    assert asyncio.run(replay.ainvoke(messages, config=summary_config)).text == "understood"
    assert len(state["calls"]) == len(admitted) == len(raw) == len(usage) == 2


@pytest.mark.parametrize(
    "provider_name,protocol,stream",
    [
        ("deepseek", "chat", False),
        ("deepseek", "chat", True),
        ("compatible", "chat", False),
        ("deepseek", "responses", False),
        ("anthropic", "claude", False),
    ],
)
def test_context_projection_counts_the_reasoning_sent_by_the_actual_sdk(
    provider, provider_name, protocol, stream
):
    base, state = provider
    state["responses"] = [
        (200, sse_response(protocol, text="understood"), "text/event-stream")
        if stream
        else (
            200,
            claude_response() if protocol == "claude" else response(protocol, text="understood"),
        )
    ]
    messages = [HumanMessage(content="核对历史")]
    for index, reasoning in enumerate(["第一项推理", "", None, "最后一项推理"]):
        call_id = f"history-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": call_id, "name": "lookup", "args": {}}],
                    additional_kwargs={"reasoning_content": reasoning}
                    if reasoning is not None
                    else {},
                ),
                ToolMessage(content=f"结果{index}", tool_call_id=call_id),
            ]
        )
    original = messages_to_dict(messages)
    config = {**configured(base, protocol), "provider": provider_name}
    resources = GraphResources(SimpleNamespace(settings=SETTINGS), config, None)
    projected = resources.project_input(messages)
    model = build_chat_model(
        config,
        "fixture-key",
        callbacks=ModelCallbacks(settings=SETTINGS, hydrate=resources.hydrate, stream=stream),
    )
    assert asyncio.run(model.bind_tools([TOOL]).ainvoke(messages)).text == "understood"
    wire = state["calls"][0][2]
    if provider_name == "deepseek" and protocol == "chat":
        actual = [item for item in wire["messages"] if item["role"] == "assistant"]
        counted = [item for item in projected if item["role"] == "assistant"]
        assert [item.get("reasoning_content") for item in actual] == [
            "第一项推理",
            "",
            None,
            "最后一项推理",
        ]
        assert [item.get("reasoning_content") for item in counted] == [
            "第一项推理",
            "",
            None,
            "最后一项推理",
        ]
        assert "reasoning_content" in counted[1] and "reasoning_content" not in counted[2]
    else:
        assert "reasoning_content" not in json.dumps(wire)
        assert all("reasoning_content" not in item for item in projected)
    assert messages_to_dict(messages) == original


@pytest.mark.parametrize(
    "provider_name,protocol,stream",
    [
        ("deepseek", "chat", False),
        ("deepseek", "chat", True),
        ("compatible", "chat", False),
        ("deepseek", "responses", False),
        ("anthropic", "claude", False),
    ],
)
def test_official_summary_fallback_recognizes_the_protected_sdk_provider(
    provider, provider_name, protocol, stream
):
    base, state = provider
    state["responses"] = [
        (200, sse_response(protocol, text="understood"), "text/event-stream")
        if stream
        else (
            200,
            claude_response() if protocol == "claude" else response(protocol, text="understood"),
        )
    ]
    model = build_chat_model(
        {**configured(base, protocol), "provider": provider_name},
        "fixture-key",
        callbacks=ModelCallbacks(settings=SETTINGS, stream=stream),
    )
    message = asyncio.run(model.ainvoke([HumanMessage(content="核对报告用量")]))
    summary = SummarizationMiddleware(
        model=model, trigger=("tokens", 9), keep=("messages", 1), token_counter=lambda _: 0
    )
    assert message.usage_metadata["total_tokens"] == 10
    assert summary._should_summarize([message], 0)
    wrong = message.model_copy(
        update={"response_metadata": {**message.response_metadata, "model_provider": "other"}}
    )
    assert not summary._should_summarize([wrong], 0)
    params = model._get_ls_params(stop=["END"], model="named-model", max_tokens=37)
    assert params["ls_provider"] == message.response_metadata["model_provider"]
    assert params["ls_stop"] == ["END"]
    assert params["ls_model_name"] == "named-model" and params["ls_max_tokens"] == 37


@pytest.mark.parametrize(
    "provider_name,protocol,choice,expected_choice,forced",
    [
        ("deepseek", "chat", "required", "required", True),
        (
            "deepseek",
            "chat",
            "lookup",
            {"type": "function", "function": {"name": "lookup"}},
            True,
        ),
        (
            "deepseek",
            "chat",
            {"type": "function", "function": {"name": "lookup"}},
            {"type": "function", "function": {"name": "lookup"}},
            True,
        ),
        ("deepseek", "chat", "auto", "auto", False),
        ("deepseek", "chat", "none", "none", False),
        ("deepseek", "chat", None, None, False),
        ("compatible", "chat", "required", "required", False),
        ("deepseek", "responses", "required", "required", True),
        ("anthropic", "claude", None, None, False),
    ],
)
def test_factory_limits_thinking_override_to_forced_deepseek_tools(
    provider, provider_name, protocol, choice, expected_choice, forced
):
    base, state = provider
    state["responses"] = [
        (200, claude_response() if protocol == "claude" else response(protocol, text="understood"))
    ]
    options = {} if choice is None else {"tool_choice": choice}
    if forced:
        options["extra_body"] = {
            "thinking": {"type": "enabled"},
            "fixture_option": {"value": "preserved"},
        }
    original = deepcopy(options)
    model = build_chat_model(
        {**configured(base, protocol), "provider": provider_name},
        "fixture-key",
        callbacks=ModelCallbacks(settings=SETTINGS, stream=False),
    )
    result = asyncio.run(
        model.bind_tools([TOOL], **options).ainvoke([HumanMessage(content="读取")])
    )
    assert result.text == "understood" and len(state["calls"]) == 1
    wire = state["calls"][0][2]
    assert wire.get("tool_choice") == expected_choice
    if forced:
        assert wire["thinking"] == {"type": "disabled"}
        assert wire["fixture_option"] == {"value": "preserved"}
    else:
        assert "thinking" not in wire
    assert options == original


def deepseek_reasoning_response(*, stream, identity, reasoning, call_id):
    calls = [(call_id, "lookup", {})]
    if not stream:
        reply = response("chat", calls=calls)
        reply["id"] = identity
        reply["choices"][0]["message"]["reasoning_content"] = reasoning
        return 200, reply
    reasoning_chunk = {
        "id": identity,
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "reasoning_content": reasoning},
                "finish_reason": None,
            }
        ],
    }
    body = b"data: " + json.dumps(reasoning_chunk).encode() + b"\n\n"
    body += sse_response("chat", calls=calls).replace(b"chatcmpl-local", identity.encode())
    return 200, body, "text/event-stream"


@pytest.mark.parametrize("stream", [False, True])
def test_deepseek_reasoning_survives_typed_restore_continuation_and_raw_replay(provider, stream):
    from short_drama.agent.runtime.checkpointer import MySQLCheckpointSaver

    base, state = provider
    state["responses"] = [
        deepseek_reasoning_response(
            stream=stream,
            identity="chatcmpl-first",
            reasoning="先核验 fixture-key。",
            call_id="first",
        ),
        deepseek_reasoning_response(
            stream=stream,
            identity="chatcmpl-second",
            reasoning="再确认工具结果。",
            call_id="second",
        ),
        (200, sse_response("chat", text="完成"), "text/event-stream")
        if stream
        else (200, response("chat", text="完成")),
    ]
    admitted, raw, usage, events = [], [], [], []

    async def admit(payload):
        admitted.append(payload)

    async def save_raw(payload):
        raw.append(payload)

    async def record_usage(payload):
        usage.append(payload)

    async def on_model_content(payload):
        events.append(payload)

    callbacks = ModelCallbacks(
        settings=SETTINGS,
        stream=stream,
        before_send=admit,
        save_raw=save_raw,
        usage=record_usage,
        on_model_content=on_model_content,
    )
    config = {**configured(base, "chat"), "provider": "deepseek"}
    model = build_chat_model(config, "fixture-key", callbacks=callbacks)
    saver = MySQLCheckpointSaver(SimpleNamespace(owner_id=1, run_id=2))

    async def invoke():
        messages = [HumanMessage(content="核验两项")]
        first = await model.bind_tools([TOOL]).ainvoke(messages)
        assert first.additional_kwargs.get("reasoning_content") == "先核验 [redacted]。"
        messages.extend(
            [
                first,
                ToolMessage(content="第一项结果", tool_call_id="first"),
                AIMessage(content="继续核验", additional_kwargs={"visible": "unchanged"}),
                HumanMessage(content="第二项"),
            ]
        )
        restored = saver._decode(*saver._encode({"messages": messages}))["messages"]
        assert restored[1].additional_kwargs["reasoning_content"] == "先核验 [redacted]。"
        original = [message.model_dump() for message in restored]
        second = await model.bind_tools([TOOL]).ainvoke(restored)
        assert second.additional_kwargs.get("reasoning_content") == "再确认工具结果。"
        assert [message.model_dump() for message in restored] == original
        continued = restored + [second, ToolMessage(content="第二项结果", tool_call_id="second")]
        final_input = saver._decode(*saver._encode({"messages": continued}))["messages"]
        original = [message.model_dump() for message in final_input]
        assert (await model.bind_tools([TOOL]).ainvoke(final_input)).text == "完成"
        assert [message.model_dump() for message in final_input] == original
        recorded_counts = (len(admitted), len(raw), len(usage), len(events))
        replay = build_chat_model(
            config,
            "fixture-key",
            callbacks=replace(callbacks, request=admitted[1], raw=raw[1]),
        )
        replayed = await replay.bind_tools([TOOL]).ainvoke(restored)
        assert replayed.additional_kwargs["reasoning_content"] == "再确认工具结果。"
        assert (len(admitted), len(raw), len(usage), len(events)) == recorded_counts

    asyncio.run(invoke())
    assert len(state["calls"]) == len(admitted) == len(raw) == len(usage) == 3
    assistants = [item for item in state["calls"][2][2]["messages"] if item["role"] == "assistant"]
    assert [item.get("reasoning_content") for item in assistants] == [
        "先核验 [redacted]。",
        None,
        "再确认工具结果。",
    ]
    assert assistants[0]["tool_calls"][0]["id"] == "first"
    assert assistants[1]["content"] == "继续核验" and "reasoning_content" not in assistants[1]
    assert assistants[2]["tool_calls"][0]["id"] == "second"
    assert "".join(event["delta"] for event in events if event["channel"] == "reasoning") == (
        "先核验 [redacted]。再确认工具结果。"
    )
    assert "".join(event["delta"] for event in events if event["channel"] == "text") == "完成"
    assert all("fixture-key" not in base64.b64decode(item["body_b64"]).decode() for item in raw)
    assert all("thinking" not in call[2] for call in state["calls"])
