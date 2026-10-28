"""Real LangChain adapters against a local HTTP provider, never a paid model."""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from short_drama.agent.gateway.langchain import LangChainGateway
from short_drama.agent.gateway.protocol import AgentGatewayError
from short_drama.agent.resources.contracts import ResourceRef, assert_reference_payload
from short_drama.agent.resources.resolver import HydratedResource, image_block
from tests.unit.standard.agent.test_agent_model_gateway import provider as provider
from tests.unit.standard.agent.test_agent_model_gateway import response, snapshot, sse_response


def gateway():
    return LangChainGateway(
        SimpleNamespace(generation_allowed_hosts=["127.0.0.1"], generation_max_response_bytes=65536)
    )


def claude_response(*, calls=False):
    return {
        "id": "msg_local",
        "type": "message",
        "role": "assistant",
        "model": "fixture",
        "content": [{"type": "tool_use", "id": "call_local", "name": "lookup", "input": {}}]
        if calls
        else [{"type": "text", "text": "understood"}],
        "stop_reason": "tool_use" if calls else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }


TOOL = {
    "name": "lookup",
    "description": "Look up facts",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
@pytest.mark.parametrize("with_tools", [False, True])
def test_one_decision_and_offline_replay(provider, protocol, with_tools, monkeypatch):
    base, state = provider
    config = snapshot(base, protocol)
    if protocol == "claude":
        config.update(
            base_url=base, runtime_profile={"api_format": "claude", "protocol": "messages"}
        )
    answer = (
        claude_response(calls=with_tools)
        if protocol == "claude"
        else response(
            protocol,
            text="understood",
            calls=[("call_local", "lookup", {})] if with_tools else None,
        )
    )
    state["responses"] = [(200, answer)]
    saved = {}

    async def request(value):
        saved["request"] = value

    async def raw(value):
        saved["raw"] = value

    boundary = gateway()
    kwargs = dict(
        messages=[HumanMessage(content="Look up facts")],
        tools=[TOOL],
        resources=[],
        max_output_tokens=64,
        stream=False,
    )
    actual = asyncio.run(
        boundary.decide(config, "fixture-key", **kwargs, on_request=request, on_response=raw)
    )
    assert actual["output_kind"] == ("tool_requests" if with_tools else "text")
    assert actual["requests"] == 1
    assert len(state["calls"]) == 1
    assert_reference_payload(saved["request"])

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must not perform network I/O")

    monkeypatch.setattr(boundary.transport, "request", forbidden)
    replay = asyncio.run(boundary.decide(config, "fixture-key", **kwargs, **saved))
    assert replay["output"] == actual["output"]
    assert replay["requests"] == 0


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_visual_delivery_persists_only_references(provider, protocol):
    base, state = provider
    config = snapshot(base, protocol)
    if protocol == "claude":
        config.update(
            base_url=base, runtime_profile={"api_format": "claude", "protocol": "messages"}
        )
    state["responses"] = [
        (200, claude_response() if protocol == "claude" else response(protocol, text="understood"))
    ]
    pixels = base64.b64encode(b"managed-test-pixels").decode()
    ref = ResourceRef(
        kind="image",
        ref_id="view:1",
        uri="minio://test/processed.jpg",
        source="hydrated_image",
        checksum="a" * 64,
        metadata={"detail": "low"},
    )
    resource = HydratedResource(ref, pixels, "image/jpeg", "tool_result", "ephemeral")
    block = image_block(
        resource, config.get("runtime_profile") and "claude_messages.v1" or "openai_chat.v1"
    )
    messages = [
        HumanMessage(content="Inspect"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "call_image"}]),
    ]
    if protocol == "chat":
        messages += [
            ToolMessage(content="image_ref:view:1", tool_call_id="call_image"),
            HumanMessage(content=[block]),
        ]
    else:
        messages += [ToolMessage(content=[block], tool_call_id="call_image")]
    messages.append(HumanMessage(content="Check all pending requirements."))
    saved = {}

    async def capture(value):
        saved.update(value)

    asyncio.run(
        gateway().decide(
            config,
            "fixture-key",
            messages=messages,
            tools=[TOOL],
            resources=[resource],
            max_output_tokens=64,
            stream=False,
            on_request=capture,
        )
    )
    assert pixels in json.dumps(state["calls"][0][2])
    assert "Check all pending requirements." in json.dumps(state["calls"][0][2])
    assert pixels not in json.dumps(saved)
    assert "image_ref" in json.dumps(saved)
    assert_reference_payload(saved)
    if protocol == "claude":
        native = state["calls"][0][2]["messages"][-1]["content"][0]
        assert native["type"] == "tool_result"
        assert native["content"][0]["source"]["type"] == "base64"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_stream_terminal_and_usage(provider, protocol):
    base, state = provider
    state["responses"] = [(200, sse_response(protocol, text="step|done"), "text/event-stream")]
    result = asyncio.run(
        gateway().decide(
            snapshot(base, protocol),
            "fixture-key",
            messages=[HumanMessage(content="Proceed")],
            tools=[],
            resources=[],
            max_output_tokens=64,
        )
    )
    assert result["output"] == "stepdone"
    assert result["usage"]["output_tokens"] == 3


def test_unknown_result_does_not_retry(provider):
    base, state = provider
    state["responses"] = [(500, {"error": {"message": "fixture failure"}})]
    with pytest.raises(AgentGatewayError):
        asyncio.run(
            gateway().decide(
                snapshot(base, "chat"),
                "fixture-key",
                messages=[HumanMessage(content="Proceed")],
                tools=[],
                resources=[],
                max_output_tokens=64,
                stream=False,
            )
        )
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_admission_failure_keeps_safe_code_and_never_sends(provider, stream):
    base, state = provider

    async def reject(_request):
        raise AgentGatewayError("agent_context_budget_exhausted")

    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(
            gateway().decide(
                snapshot(base, "chat"),
                "fixture-key",
                messages=[HumanMessage(content="Proceed")],
                tools=[],
                resources=[],
                max_output_tokens=64,
                stream=stream,
                on_request=reject,
            )
        )
    assert failure.value.code == "agent_context_budget_exhausted"
    assert failure.value.requests == 0 and not failure.value.accepted_unknown
    assert not state["calls"]


@pytest.mark.parametrize("status", [400, 401, 429, 500])
@pytest.mark.parametrize("stream", [False, True])
def test_http_failures_keep_safe_status_and_acceptance(provider, status, stream):
    base, state = provider
    state["responses"] = [(status, {"error": {"message": "private provider detail"}})]
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(
            gateway().decide(
                snapshot(base, "chat"),
                "fixture-key",
                messages=[HumanMessage(content="Proceed")],
                tools=[],
                resources=[],
                max_output_tokens=64,
                stream=stream,
            )
        )
    expected = {
        400: "agent_provider_rejected",
        401: "agent_provider_authentication",
        429: "agent_provider_rate_limited",
        500: "agent_provider_unknown",
    }
    assert failure.value.code == expected[status]
    assert failure.value.http_status == status
    assert failure.value.accepted_unknown == (status >= 500)
    assert "private provider detail" not in str(failure.value)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_tool_body_reaches_wire_but_snapshot_uses_operation_reference(provider, protocol):
    base, state = provider
    config = snapshot(base, protocol)
    if protocol == "claude":
        config.update(
            base_url=base, runtime_profile={"api_format": "claude", "protocol": "messages"}
        )
    state["responses"] = [
        (200, claude_response() if protocol == "claude" else response(protocol, text="done"))
    ]
    body = json.dumps({"instructions": "方法正文" * 200}, ensure_ascii=False, sort_keys=True)
    messages = [
        HumanMessage(content="Proceed"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "load"}]),
        ToolMessage(content=body, tool_call_id="load"),
    ]
    saved = {}

    async def request(value):
        saved["request"] = value

    async def raw(value):
        saved["raw"] = value

    boundary = gateway()
    kwargs = dict(
        messages=messages,
        tools=[TOOL],
        resources=[],
        tool_results={body: {"operation_id": "42", "digest": "a" * 64}},
        max_output_tokens=64,
        stream=False,
    )
    result = asyncio.run(
        boundary.decide(config, "fixture-key", **kwargs, on_request=request, on_response=raw)
    )
    assert "方法正文" * 200 in json.dumps(state["calls"][0][2], ensure_ascii=False)
    assert "方法正文" not in json.dumps(saved["request"], ensure_ascii=False)
    assert "operation_ref" in json.dumps(saved["request"])
    replay = asyncio.run(boundary.decide(config, "fixture-key", **kwargs, **saved))
    assert replay["output"] == result["output"] and replay["requests"] == 0
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_capability_probe_uses_two_protected_langchain_decisions(provider, protocol):
    base, state = provider
    state["responses"] = [
        (
            200,
            sse_response(
                protocol, calls=[("probe", "verify_agent_echo", {"value": "agent-capability-v1"})]
            ),
            "text/event-stream",
        ),
        (200, sse_response(protocol, text="AGENT_CAPABILITY_OK"), "text/event-stream"),
    ]
    evidence = asyncio.run(
        gateway().validate_capability(
            snapshot(base, protocol), "fixture-key", conversation_id="local-probe"
        )
    )
    assert evidence.tool_calling and evidence.tool_result_continuation
    assert evidence.requests == 2 and len(state["calls"]) == 2
