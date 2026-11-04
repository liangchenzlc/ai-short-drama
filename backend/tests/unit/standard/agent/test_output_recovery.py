"""Length terminals are recoverable; ambiguous acceptance never is."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from short_drama.agent.gateway.contracts import AgentGatewayError, ModelCallbacks
from short_drama.agent.gateway.langchain import build_chat_model
from tests.support.assistant_http import provider as provider
from tests.support.assistant_http import response, snapshot

SETTINGS = SimpleNamespace(
    generation_allowed_hosts=["127.0.0.1"], generation_max_response_bytes=65536
)


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_explicit_length_preserves_receipt_and_removes_partial_calls(provider, protocol):
    base, wire = provider
    config = snapshot(base, protocol)
    if protocol == "claude":
        config.update(
            base_url=base, runtime_profile={"api_format": "claude", "protocol": "messages"}
        )
        reply = {
            "id": "local",
            "type": "message",
            "role": "assistant",
            "model": "fixture",
            "content": [{"type": "text", "text": "partial"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
    else:
        reply = response(protocol, calls=[("unfinished", "lookup", {})])
        if protocol == "chat":
            reply["choices"][0]["finish_reason"] = "length"
        else:
            reply.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    wire["responses"] = [(200, reply)]
    receipts = []

    async def usage(payload):
        receipts.append(payload)

    model = build_chat_model(
        config,
        "",
        callbacks=ModelCallbacks(
            settings=SETTINGS,
            stream=False,
            usage=usage,
        ),
    )
    result = asyncio.run(model.ainvoke([HumanMessage(content="Continue")]))
    assert result.response_metadata["output_recovery"]["kind"] in {"text", "tool_arguments"}
    assert not result.tool_calls and not result.invalid_tool_calls
    assert len(receipts) == 1 and receipts[0]["output_tokens"] == 3


def test_responses_filter_incomplete_is_not_length_recovery(provider):
    base, wire = provider
    reply = response("responses", text="filtered")
    reply.update(status="incomplete", incomplete_details={"reason": "content_filter"})
    wire["responses"] = [(200, reply)]
    model = build_chat_model(
        snapshot(base, "responses"),
        "",
        callbacks=ModelCallbacks(
            settings=SETTINGS,
            stream=False,
        ),
    )
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(model.ainvoke([HumanMessage(content="Continue")]))
    assert failure.value.code == "agent_response_filtered"
    assert len(wire["calls"]) == 1


def truncated(kind, call_id, amount=8192, text=""):
    return AIMessage(
        content=text,
        response_metadata={
            "output_recovery": {
                "kind": kind,
                "model_call_id": call_id,
                "max_output_tokens": amount,
            }
        },
    )


def test_reasoning_continuation_increases_capacity_without_disabling_thinking():
    from short_drama.agent.runtime.output_recovery import recovery_update

    state = {"messages": [truncated("reasoning", "first")], "applied_input_revision": 1}
    update = recovery_update(state, {})
    assert update["jump_to"] == "model"
    assert update["output_recovery"]["next_output_tokens"] == 16384
    assert update["output_recovery"]["fragments"] == []
    assert "thinking" not in update
    with pytest.raises(AgentGatewayError, match="agent_reasoning_output_limit"):
        recovery_update(
            {"messages": [truncated("reasoning", "last", 393216)]},
            {
                "model_key": "deepseek-flash",
                "base_url": "https://api.deepseek.com",
            },
        )


def test_text_fragments_reference_receipts_and_replayed_hook_is_idempotent():
    from short_drama.agent.runtime.output_recovery import recovery_update

    state = {"messages": [truncated("text", "first", text="first half")]}
    update = recovery_update(state, {})
    assert update["output_recovery"]["fragments"] == ["first"]
    assert "first half" not in str(update["output_recovery"])
    replay = recovery_update({**state, **update, "messages": state["messages"]}, {})
    assert replay["output_recovery"]["fragments"] == ["first"]
    changed = recovery_update(
        {**state, **update, "messages": [AIMessage(content="done")], "applied_input_revision": 2},
        {},
    )
    assert changed["output_recovery"]["fragments"] == []


def test_three_partial_tool_arguments_stop_without_executing_tools():
    from short_drama.agent.runtime.output_recovery import recovery_update

    state = {}
    for index in range(3):
        state["messages"] = [truncated("tool_arguments", str(index))]
        if index == 2:
            with pytest.raises(AgentGatewayError, match="agent_output_no_progress"):
                recovery_update(state, {})
        else:
            state.update(recovery_update(state, {}))


def test_text_is_retained_even_when_tool_arguments_are_truncated():
    from short_drama.agent.runtime.output_recovery import recovery_update

    state = {}
    for index in range(4):
        state["messages"] = [truncated("tool_arguments", str(index), text=f"new paragraph {index}")]
        state.update(recovery_update(state, {}))
    assert state["output_recovery"]["fragments"] == ["0", "1", "2", "3"]


def test_streamed_responses_length_terminal_retains_reported_usage(provider):
    import json

    from tests.support.assistant_http import sse_response

    base, wire = provider
    events = sse_response("responses", text="partial").decode().split("\n\n")
    chunks = []
    for event in events:
        if not event:
            continue
        item = json.loads(event.split("data: ", 1)[1])
        if item["type"] == "response.completed":
            item["type"] = "response.incomplete"
            item["response"].update(
                status="incomplete", incomplete_details={"reason": "max_output_tokens"}
            )
        chunks.append(f"event: {item['type']}\ndata: {json.dumps(item)}\n\n".encode())
    wire["responses"] = [(200, b"".join(chunks), "text/event-stream")]
    usage = []

    async def settle(payload):
        usage.append(payload)

    model = build_chat_model(
        snapshot(base, "responses"), "", callbacks=ModelCallbacks(settings=SETTINGS, usage=settle)
    )
    result = asyncio.run(model.ainvoke([HumanMessage(content="Continue")]))
    assert result.response_metadata["output_recovery"]["kind"] == "text"
    assert usage[0]["output_tokens_reported"] and usage[0]["output_tokens"] == 3


@pytest.mark.parametrize("status", ["error", "success"])
def test_same_tool_error_three_rounds_stops_but_success_resets(status):
    from short_drama.agent.runtime.output_recovery import tool_failure_update

    state = {}
    for index in range(3):
        state["messages"] = [
            AIMessage(content="", tool_calls=[{"id": str(index), "name": "lookup", "args": {}}]),
            ToolMessage(
                content='{"error":{"code":"missing"}}',
                status=status,
                name="lookup",
                tool_call_id=str(index),
            ),
        ]
        if index == 2:
            with pytest.raises(AgentGatewayError, match="agent_tool_no_progress"):
                tool_failure_update(state)
        else:
            state.update(tool_failure_update(state))
    state["messages"][-1].status = "success"
    state["messages"][-1].content = "new evidence"
    assert tool_failure_update(state)["tool_failure_streak"] == {}


def test_second_summary_keeps_required_current_media_group():
    import json

    from langchain.agents.middleware.types import ModelRequest

    from short_drama.agent.resources.contracts import HydrationIntent, ResourceRef
    from short_drama.agent.resources.graph import GraphResources
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from tests.support.assistant_model import ScriptedModel

    class SummaryModel(ScriptedModel):
        snapshot: dict
        model_callbacks: object

    config = {
        "service_type": "text",
        "provider": "compatible",
        "base_url": "https://model.invalid/v1",
        "model_key": "fixture",
    }
    resources = GraphResources(None, config, None)
    intents = [
        HydrationIntent(
            resource=ResourceRef(
                kind="image",
                ref_id=f"image:{i}",
                uri=f"minio://test/frozen{i}.png",
                source="hydrated_image",
                checksum="a" * 64,
                metadata={"mime_type": "image/png", "byte_size": 1, "detail": "high"},
            ),
            purpose="inspect current pixels",
            tool_call_id="pixels",
            invocation_id="turn:1",
            detail="high",
        ).model_dump(mode="json")
        for i in range(14)
    ]
    model = SummaryModel(
        snapshot=config,
        model_callbacks=ModelCallbacks(
            settings=SimpleNamespace(),
            project_input=resources.project_input,
            input_scope=resources.input_scope,
        ),
        replies=[AIMessage(content="history summary"), AIMessage(content="second summary")],
    )
    messages = [
        *[HumanMessage(content=f"old {i}", id=f"old:{i}") for i in range(5)],
        AIMessage(
            content="", id="call", tool_calls=[{"name": "read_image", "id": "pixels", "args": {}}]
        ),
        ToolMessage(
            content=json.dumps({"hydration_intents": intents}),
            id="pixels-result",
            name="read_image",
            tool_call_id="pixels",
        ),
        HumanMessage(
            content="current goal", id="goal", additional_kwargs={"task_context_pinned": True}
        ),
    ]

    async def handler(_request):
        pytest.fail("Necessary pixels must not be silently replaced by a text summary")

    request = ModelRequest(model=model, messages=messages, state={"messages": messages})
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(
            SkillSummarizationMiddleware(
                model=model, trigger=("messages", 4), keep=("messages", 2)
            ).awrap_model_call(request, handler)
        )
    assert failure.value.code == "agent_context_budget_exhausted"
    assert failure.value.context_budget["phase"] == "post_summary"
    assert len(model.requests) == 2
