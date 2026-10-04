"""Agent protocol checks use a local HTTP server; no real provider is contacted."""

import asyncio
import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import ToolDefinition

from short_drama.agent.model_gateway import (
    AgentGatewayError,
    AgentModelGateway,
    deserialize_history,
    serialize_deferred_requests,
    serialize_history,
    serialize_segment_result,
)


@pytest.fixture
def provider():
    state = {"responses": [], "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["calls"].append((self.path, dict(self.headers), json.loads(raw)))
            if not state["responses"]:
                self.connection.shutdown(2)
                return
            reply = state["responses"].pop(0)
            status, body = reply[:2]
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", reply[2] if len(reply) > 2 else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            gate = state.get("stream_gate")
            if gate is None:
                self.wfile.write(raw)
            else:
                first, remaining = raw.split(b"\n\n", 1)
                self.wfile.write(first + b"\n\n")
                self.wfile.flush()
                state["gate_released"] = gate.wait(timeout=2)
                self.wfile.write(remaining)
                state["stream_complete"] = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def gateway():
    return AgentModelGateway(
        SimpleNamespace(generation_allowed_hosts=["127.0.0.1"], generation_max_response_bytes=65536)
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_inline_image_payload_is_protected_and_replays_without_live_io(
    provider, protocol, monkeypatch
):
    base, state = provider
    state["responses"] = [(200, response(protocol, text="Image understood"))]
    config = {**snapshot(base, protocol), "model_key": "gpt-4o"}
    inputs = {
        "instructions": "Review the image",
        "user_prompt": [
            "What is shown?",
            BinaryContent(data=b"managed-image-bytes", media_type="image/jpeg"),
        ],
    }
    saved = {}

    async def save_request(value):
        saved["request"] = value

    async def save_response(value):
        saved["response"] = value

    boundary = gateway()
    result = asyncio.run(
        boundary.run_segment(
            config, "", **inputs, on_request=save_request, on_response=save_response
        )
    )
    body = state["calls"][0][2]
    encoded = base64.b64encode(b"managed-image-bytes").decode()
    assert f"data:image/jpeg;base64,{encoded}" in json.dumps(body)
    assert saved["request"]["body"] == body
    loaded = deserialize_history(result.history)
    content = next(
        part.content
        for message in loaded
        for part in message.parts
        if part.part_kind == "user-prompt"
    )
    assert any(
        isinstance(item, BinaryContent) and item.data == b"managed-image-bytes" for item in content
    )

    def forbidden(*_, **__):
        raise AssertionError("Archived inline input must not resolve DNS or send live requests")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(boundary.transport, "request", forbidden)
    replay = asyncio.run(
        boundary.replay_segment(
            config, "", **inputs, request_payload=saved["request"], raw_response=saved["response"]
        )
    )
    assert replay.output == result.output and replay.requests == 0
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_inline_binary_history_survives_deferred_tool_continuation(provider, protocol):
    base, state = provider
    state["responses"] = [
        (200, response(protocol, calls=[("read-1", "read_episode", {})])),
        (200, response(protocol, text="Ready")),
    ]
    config = {**snapshot(base, protocol), "model_key": "gpt-4o"}
    boundary = gateway()
    first = asyncio.run(
        boundary.run_segment(
            config,
            "",
            instructions="Review",
            tools=[tool()],
            user_prompt=["Image", BinaryContent(data=b"image-data", media_type="image/png")],
        )
    )
    second = asyncio.run(
        boundary.run_segment(
            config,
            "",
            instructions="Review",
            tools=[tool()],
            history=first.history,
            deferred_results=DeferredToolResults(calls={"read-1": {"title": "Episode"}}),
        )
    )
    assert second.output == "Ready"
    assert "data:image/png;base64," in json.dumps(state["calls"][1][2])


def test_inline_audio_uses_chat_input_audio_payload(provider):
    base, state = provider
    state["responses"] = [(200, response("chat", text="Audio understood"))]
    config = {**snapshot(base, "chat"), "model_key": "gpt-4o-audio-preview"}
    result = asyncio.run(
        gateway().run_segment(
            config,
            "",
            instructions="Review the sound",
            user_prompt=["Listen", BinaryContent(data=b"managed-audio", media_type="audio/mpeg")],
        )
    )
    contents = [
        part
        for message in state["calls"][0][2]["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
    ]
    audio = next(part["input_audio"] for part in contents if part["type"] == "input_audio")
    assert audio == {"data": base64.b64encode(b"managed-audio").decode(), "format": "mp3"}
    assert result.output == "Audio understood"


@pytest.mark.parametrize(
    ("protocol", "mime", "model"),
    [
        ("responses", "audio/mpeg", "gpt-4o-audio-preview"),
        ("chat", "image/jpeg", "text-only"),
        ("chat", "video/mp4", "gpt-4o"),
    ],
)
def test_unsupported_inline_modality_is_rejected_before_network(provider, protocol, mime, model):
    base, state = provider
    with pytest.raises(AgentGatewayError, match="unsupported_agent_input_modality"):
        asyncio.run(
            gateway().run_segment(
                {**snapshot(base, protocol), "model_key": model},
                "",
                instructions="Review",
                user_prompt=[BinaryContent(data=b"file", media_type=mime)],
            )
        )
    assert state["calls"] == []


def snapshot(base, protocol):
    return {
        "service_type": "text",
        "base_url": base + ("/responses" if protocol == "responses" else "/chat/completions"),
        "model_key": "test-model",
    }


def tool(name="read_episode"):
    return ToolDefinition(
        name=name,
        description="Read the authorized episode.",
        parameters_json_schema={
            "type": "object",
            "properties": {"episode_id": {"type": "string"}},
            "required": ["episode_id"],
            "additionalProperties": False,
        },
    )


def response(protocol, *, text=None, calls=None):
    usage = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    if protocol == "chat":
        message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for call_id, name, args in calls
            ]
        return {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
    output = (
        [
            {
                "type": "function_call",
                "id": f"fc_{call_id}",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(args),
                "status": "completed",
            }
            for call_id, name, args in calls
        ]
        if calls
        else [
            {
                "type": "message",
                "id": "msg_local",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ]
    )
    return {
        "id": "resp_local",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "test-model",
        "output": output,
        "usage": usage,
    }


def sse_response(protocol, *, text=None, calls=None, with_usage=True, terminal=True):
    events = []
    if protocol == "chat":

        def chunk(delta, finish=None):
            return {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        if calls:
            for index, (call_id, name, args) in enumerate(calls):
                events.append(
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ]
                        }
                    )
                )
        else:
            events.extend(chunk({"content": part}) for part in (text or "").split("|"))
        events.append(chunk({}, "tool_calls" if calls else "stop"))
        if with_usage:
            events.append(
                {
                    "id": "chatcmpl-local",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
            )
        encoded = [f"data: {json.dumps(item)}\n\n".encode() for item in events]
        return b"".join(encoded) + (b"data: [DONE]\n\n" if terminal else b"")
    final = response(protocol, text=(text or "").replace("|", ""), calls=calls)
    if not with_usage:
        final.pop("usage")
    initial = {**final, "status": "in_progress", "output": [], "usage": None}
    events.append({"type": "response.created", "response": initial})
    for index, item in enumerate(final["output"]):
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if not calls:
            for part in (text or "").split("|"):
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "delta": part,
                        "logprobs": [],
                    }
                )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    if terminal:
        events.append({"type": "response.completed", "response": final})
    return b"".join(
        (
            f"event: {item['type']}\n"
            + f"data: {json.dumps({**item, 'sequence_number': index})}\n\n"
        ).encode()
        for index, item in enumerate(events)
    )


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_external_tool_history_survives_serialize_and_continue(provider, protocol):
    base, state = provider
    state["responses"] = [
        (200, response(protocol, calls=[("call_1", "read_episode", {"episode_id": "42"})])),
        (200, response(protocol, text="The episode is ready.")),
    ]
    boundary = gateway()
    first = asyncio.run(
        boundary.run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Read the episode, then explain it.",
            user_prompt="Inspect episode 42.",
            tools=[tool()],
            conversation_id="private-conversation-1",
        )
    )
    assert isinstance(first.output, DeferredToolRequests)
    assert first.requests == first.usage["requests"] == 1
    assert len(state["calls"]) == 1
    assert first.output.calls[0].args_as_dict() == {"episode_id": "42"}
    assert serialize_deferred_requests(first.output)["calls"][0]["tool_call_id"] == "call_1"
    loaded_history = json.loads(json.dumps(first.history))
    assert serialize_history(deserialize_history(loaded_history)) == loaded_history
    second = asyncio.run(
        boundary.run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Read the episode, then explain it.",
            history=loaded_history,
            tools=[tool()],
            deferred_results=DeferredToolResults(calls={"call_1": {"title": "Episode 42"}}),
            conversation_id="private-conversation-1",
        )
    )
    assert second.output == "The episode is ready."
    assert second.requests == 1 and len(state["calls"]) == 2
    assert second.usage["output_tokens"] == 3
    body = state["calls"][1][2]
    if protocol == "chat":
        returns = [message for message in body["messages"] if message["role"] == "tool"]
        assert returns[0]["tool_call_id"] == "call_1"
    else:
        returns = [
            message for message in body["input"] if message.get("type") == "function_call_output"
        ]
        assert returns[0]["call_id"] == "call_1"


@pytest.mark.parametrize("status,unknown", [(400, False), (429, False), (500, True), (503, True)])
def test_http_errors_are_safe_and_never_retried(provider, status, unknown):
    base, state = provider
    state["responses"] = [(status, b"private response test-secret")]
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
            )
        )
    assert caught.value.accepted_unknown is unknown
    assert caught.value.http_status == status
    assert caught.value.requests == 1 and len(state["calls"]) == 1
    assert "private response" not in str(caught.value)
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize("body", [b'{"choices":', b"{}"])
def test_incomplete_or_invalid_response_is_unknown_without_second_post(provider, body):
    base, state = provider
    state["responses"] = [(200, body)]
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
            )
        )
    assert caught.value.accepted_unknown
    assert caught.value.requests == 1 and len(state["calls"]) == 1


def test_connection_lost_after_send_is_unknown_and_never_retried(provider):
    base, state = provider
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
            )
        )
    assert caught.value.accepted_unknown
    assert caught.value.requests == 1 and len(state["calls"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_visible_output_is_redacted_while_private_history_preserves_provider_text(
    provider, protocol
):
    base, state = provider
    state["responses"] = [(200, response(protocol, text="test-secret must be removed."))]
    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
        )
    )
    assert result.output == "[redacted] must be removed."
    assert "test-secret must be removed." in json.dumps(result.history)
    assert "test-secret" not in repr(result)
    headers = {key.lower(): value for key, value in state["calls"][0][1].items()}
    assert headers["authorization"] == "Bearer test-secret"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_authentication_credential_is_not_injected_into_private_history(provider, protocol):
    base, state = provider
    state["responses"] = [(200, response(protocol, text="The episode is ready."))]
    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
        )
    )
    assert "test-secret" not in json.dumps(result.history)
    assert "test-secret" not in json.dumps(state["calls"][0][2])
    headers = {key.lower(): value for key, value in state["calls"][0][1].items()}
    assert headers["authorization"] == "Bearer test-secret"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("credential", ["id", "ai"])
def test_short_credentials_preserve_protocol_state_and_tool_continuation(
    provider, protocol, credential
):
    base, state = provider
    call_id = "call_id_ai_1"
    tool_name = "read_id_ai_episode"
    arguments = {"episode_id": "episode_id_ai_42"}
    first_response = response(protocol, calls=[(call_id, tool_name, arguments)])
    first_response["model"] = "id-ai-model"
    second_response = response(protocol, text="The episode is ready.")
    second_response["model"] = "id-ai-model"
    state["responses"] = [(200, first_response), (200, second_response)]
    config = {**snapshot(base, protocol), "model_key": "id-ai-model"}
    boundary = gateway()
    first = asyncio.run(
        boundary.run_segment(
            config,
            credential,
            instructions="Read the episode, then explain it.",
            user_prompt="Inspect the episode.",
            tools=[tool(tool_name)],
        )
    )
    assert isinstance(first.output, DeferredToolRequests)
    call = first.output.calls[0]
    assert call.tool_call_id == call_id
    assert call.tool_name == tool_name
    assert call.args_as_dict() == arguments
    private_history = json.loads(json.dumps(first.history))
    assert serialize_history(deserialize_history(private_history)) == private_history
    encoded_response = private_history["messages"][-1]
    assert encoded_response["model_name"] == "id-ai-model"
    encoded_call = encoded_response["parts"][0]
    assert encoded_call["tool_call_id"] == call_id
    assert encoded_call["tool_name"] == tool_name
    assert json.loads(encoded_call["args"]) == arguments
    second = asyncio.run(
        boundary.run_segment(
            config,
            credential,
            instructions="Read the episode, then explain it.",
            history=private_history,
            tools=[tool(tool_name)],
            deferred_results=DeferredToolResults(calls={call_id: {"title": "Episode 42"}}),
        )
    )
    assert second.output == "The episode is ready."
    assert len(state["calls"]) == 2
    body = state["calls"][1][2]
    assert body["model"] == "id-ai-model"
    if protocol == "chat":
        assistant = next(message for message in body["messages"] if message.get("tool_calls"))
        sent_call = assistant["tool_calls"][0]
        assert sent_call["id"] == call_id
        assert sent_call["function"]["name"] == tool_name
        assert json.loads(sent_call["function"]["arguments"]) == arguments
        returns = [message for message in body["messages"] if message["role"] == "tool"]
        assert returns[0]["tool_call_id"] == call_id
    else:
        sent_call = next(
            message for message in body["input"] if message.get("type") == "function_call"
        )
        assert sent_call["call_id"] == call_id
        assert sent_call["name"] == tool_name
        assert json.loads(sent_call["arguments"]) == arguments
        returns = [
            message for message in body["input"] if message.get("type") == "function_call_output"
        ]
        assert returns[0]["call_id"] == call_id


def test_history_codec_rejects_unknown_versions_and_keeps_tool_results():
    messages = [
        ModelRequest(parts=[UserPromptPart("Hello")]),
        ModelRequest(parts=[ToolReturnPart("read_episode", {"title": "Example"}, "call_1")]),
    ]
    encoded = serialize_history(messages)
    assert serialize_history(deserialize_history(encoded)) == encoded
    encoded["version"] = 99
    with pytest.raises(AgentGatewayError, match="unsupported_agent_history"):
        deserialize_history(encoded)


def test_private_addresses_rejected_without_configured_allowlist(provider):
    base, state = provider
    boundary = AgentModelGateway(SimpleNamespace(generation_allowed_hosts=[]))
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            boundary.run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
            )
        )
    assert caught.value.code == "unsafe_address" and not caught.value.accepted_unknown
    assert state["calls"] == []


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_capability_probe_counts_two_explicit_segments_and_leaves_streaming_unverified(
    provider, protocol
):
    base, state = provider
    state["responses"] = [
        (
            200,
            response(
                protocol,
                calls=[("probe_1", "verify_agent_echo", {"value": "agent-capability-v1"})],
            ),
        ),
        (200, response(protocol, text="AGENT_CAPABILITY_OK")),
    ]
    observed = []

    async def save_segment(segment):
        observed.append(segment)

    evidence = asyncio.run(
        gateway().validate_capability(
            snapshot(base, protocol),
            "test-secret",
            conversation_id="capability-check",
            on_segment_result=save_segment,
            stream=False,
        )
    )
    assert evidence.tool_calling and evidence.tool_result_continuation
    assert evidence.requests == 2 and len(state["calls"]) == 2
    assert evidence.streaming == "not_tested"
    assert len(observed) == len(evidence.segments) == 2


@pytest.mark.parametrize("finish_reason,unknown", [(None, True), ("length", False)])
def test_missing_terminal_or_truncated_text_cannot_be_a_completed_decision(
    provider, finish_reason, unknown
):
    base, state = provider
    body = response("chat", text="Partial text")
    body["choices"][0]["finish_reason"] = finish_reason
    state["responses"] = [(200, body)]
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
            )
        )
    assert caught.value.accepted_unknown is unknown
    assert caught.value.requests == 1 and len(state["calls"]) == 1


@pytest.mark.parametrize(
    "calls,max_calls",
    [
        ([("duplicate", "read_episode", {}), ("duplicate", "read_episode", {})], 2),
        ([("one", "read_episode", {}), ("two", "read_episode", {})], 1),
        ([("unknown", "unexpected_tool", {})], 1),
    ],
)
def test_invalid_tool_batch_does_not_trigger_hidden_second_decision(provider, calls, max_calls):
    base, state = provider
    state["responses"] = [(200, response("chat", calls=calls))]
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Use read_episode.",
                user_prompt="Read the episode.",
                tools=[tool()],
                max_tool_calls=max_calls,
            )
        )
    assert caught.value.requests == 1 and len(state["calls"]) == 1


def test_capability_checkpoint_failure_stops_before_second_post(provider):
    base, state = provider
    state["responses"] = [
        (
            200,
            response(
                "chat", calls=[("probe_1", "verify_agent_echo", {"value": "agent-capability-v1"})]
            ),
        ),
        (200, response("chat", text="AGENT_CAPABILITY_OK")),
    ]

    async def failed_save(segment):
        raise RuntimeError("Private checkpoint unavailable")

    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        asyncio.run(
            gateway().validate_capability(
                snapshot(base, "chat"),
                "test-secret",
                conversation_id="capability-check",
                on_segment_result=failed_save,
                stream=False,
            )
        )
    assert len(state["calls"]) == 1


def test_capability_second_request_failure_reports_total_and_completed_first_segment(provider):
    base, state = provider
    state["responses"] = [
        (
            200,
            response(
                "chat", calls=[("probe_1", "verify_agent_echo", {"value": "agent-capability-v1"})]
            ),
        ),
        (503, b"upstream unavailable"),
    ]
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().validate_capability(
                snapshot(base, "chat"),
                "test-secret",
                conversation_id="capability-check",
                stream=False,
            )
        )
    assert caught.value.accepted_unknown and caught.value.requests == 2
    assert len(caught.value.completed_segments) == 1
    assert len(state["calls"]) == 2


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_checkpoint_payloads_and_local_replay_never_use_network(
    provider, protocol, stream, monkeypatch
):
    base, state = provider
    body = (
        sse_response(protocol, text="The episode is ready.")
        if stream
        else response(protocol, text="The episode is ready.")
    )
    state["responses"] = [(200, body, "text/event-stream" if stream else "application/json")]
    saved = {}
    order = []

    async def request_checkpoint(payload):
        assert not state["calls"]
        saved["request"] = payload
        order.append("request")

    async def response_checkpoint(payload):
        saved["response"] = payload
        order.append("response")

    async def text_delta(text):
        order.append("delta")

    config = snapshot(base, protocol)
    inputs = {"instructions": "Answer safely.", "user_prompt": "Hello.", "stream": stream}
    boundary = gateway()
    result = asyncio.run(
        boundary.run_segment(
            config,
            "test-secret",
            **inputs,
            on_request=request_checkpoint,
            on_response=response_checkpoint,
            on_text_delta=text_delta if stream else None,
        )
    )
    assert result.output == "The episode is ready."
    assert result.requests == result.usage["external_requests"] == 1
    assert result.usage["usage_reported"] and result.usage["output_tokens_reported"]
    assert result.streaming is stream
    assert order[0] == "request" and "response" in order
    assert saved["request"]["body"] == state["calls"][0][2]
    assert "test-secret" not in json.dumps(saved)
    assert set(saved["response"]) == {"codec", "version", "status_code", "content_type", "body_b64"}

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must not resolve DNS or call the live transport")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(boundary.transport, "request", forbidden)
    replay = asyncio.run(
        boundary.replay_segment(
            config,
            "test-secret",
            **inputs,
            request_payload=saved["request"],
            raw_response=saved["response"],
        )
    )
    assert replay.output == result.output
    assert replay.requests == replay.usage["external_requests"] == 0
    assert replay.usage["requests"] == 1
    assert len(state["calls"]) == 1
    assert serialize_segment_result(replay)["output_kind"] == "text"
    with pytest.raises(AgentGatewayError, match="agent_replay_request_mismatch"):
        asyncio.run(
            boundary.replay_segment(
                config,
                "test-secret",
                **{**inputs, "user_prompt": "Changed"},
                request_payload=saved["request"],
                raw_response=saved["response"],
            )
        )
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_streaming_capability_probe_uses_two_requests_and_keeps_all_tools_deferred(
    provider, protocol
):
    base, state = provider
    state["responses"] = [
        (
            200,
            sse_response(
                protocol, calls=[("probe_1", "verify_agent_echo", {"value": "agent-capability-v1"})]
            ),
            "text/event-stream",
        ),
        (200, sse_response(protocol, text="AGENT_CAPABILITY_OK"), "text/event-stream"),
    ]
    observed = []

    async def save(segment):
        observed.append(segment)

    evidence = asyncio.run(
        gateway().validate_capability(
            snapshot(base, protocol),
            "test-secret",
            conversation_id="capability-check",
            on_segment_result=save,
        )
    )
    assert evidence.tool_calling and evidence.tool_result_continuation
    assert evidence.streaming == "verified"
    assert evidence.requests == 2 and len(state["calls"]) == 2
    assert isinstance(observed[0].output, DeferredToolRequests)
    assert all(segment.streaming for segment in observed)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_stream_text_redacts_credentials_across_deltas_without_exposing_tool_arguments(
    provider, protocol
):
    base, state = provider
    state["responses"] = [
        (200, sse_response(protocol, text="Visible test-|sec|ret message."), "text/event-stream")
    ]
    deltas = []

    async def delta(text):
        deltas.append(text)

    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
            stream=True,
            on_text_delta=delta,
        )
    )
    assert "".join(deltas) == result.output == "Visible [redacted] message."
    assert "test-secret" not in "".join(deltas)
    state["responses"] = [
        (
            200,
            sse_response(
                protocol, calls=[("call_1", "read_episode", {"episode_id": "private-argument"})]
            ),
            "text/event-stream",
        )
    ]
    deltas.clear()
    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Read the episode.",
            user_prompt="Inspect it.",
            tools=[tool()],
            stream=True,
            on_text_delta=delta,
        )
    )
    assert isinstance(result.output, DeferredToolRequests) and deltas == []


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("checkpoint", ["request", "response"])
def test_checkpoint_failure_cannot_return_decision_or_repeat_post(provider, stream, checkpoint):
    base, state = provider
    body = sse_response("chat", text="Ready.") if stream else response("chat", text="Ready.")
    state["responses"] = [(200, body, "text/event-stream" if stream else "application/json")]

    async def failure(payload):
        raise RuntimeError("private persistence error test-secret")

    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=stream,
                **{f"on_{checkpoint}": failure},
            )
        )
    expected_calls = int(checkpoint == "response")
    assert len(state["calls"]) == caught.value.requests == expected_calls
    assert caught.value.accepted_unknown is bool(expected_calls)
    assert "test-secret" not in str(caught.value)
    assert caught.value.code == f"agent_{checkpoint}_checkpoint_failed"


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_missing_provider_usage_keeps_output_reservation_evidence_false(provider, protocol, stream):
    base, state = provider
    if stream:
        body = sse_response(protocol, text="Ready.", with_usage=False)
    else:
        body = response(protocol, text="Ready.")
        body.pop("usage")
    state["responses"] = [(200, body, "text/event-stream" if stream else "application/json")]
    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, protocol),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
            stream=stream,
        )
    )
    assert not result.usage["usage_reported"] and not result.usage["output_tokens_reported"]


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_stream_without_terminal_is_unknown_but_complete_raw_is_saved(provider, protocol):
    base, state = provider
    body = sse_response(protocol, text="Partial.", terminal=False)
    state["responses"] = [(200, body, "text/event-stream")]
    saved = []

    async def save(payload):
        saved.append(payload)

    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, protocol),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=True,
                on_response=save,
            )
        )
    assert caught.value.accepted_unknown and len(state["calls"]) == 1
    assert base64.b64decode(saved[0]["body_b64"]) == body


def test_text_arrives_before_provider_finishes_stream_and_raw_commit_precedes_result(provider):
    base, state = provider
    state["stream_gate"] = threading.Event()
    state["stream_complete"] = False
    body = sse_response("chat", text="Visible.| More text.")
    state["responses"] = [(200, body, "text/event-stream")]
    observed = []

    async def delta(text):
        observed.append(text)
        if not state["stream_gate"].is_set():
            assert not state["stream_complete"]
            state["stream_gate"].set()

    async def response_checkpoint(payload):
        assert state["gate_released"]
        assert base64.b64decode(payload["body_b64"]) == body
        observed.append("raw-committed")

    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, "chat"),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
            stream=True,
            on_text_delta=delta,
            on_response=response_checkpoint,
        )
    )
    assert result.output == "Visible. More text."
    assert observed[0] == "Visible." and "raw-committed" in observed
    assert len(state["calls"]) == 1


def test_stream_delta_checkpoint_failure_is_safe_and_never_retried(provider):
    base, state = provider
    state["responses"] = [(200, sse_response("chat", text="Visible."), "text/event-stream")]

    async def failure(text):
        raise RuntimeError("private delta failure test-secret")

    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=True,
                on_text_delta=failure,
            )
        )
    assert caught.value.code == "agent_event_checkpoint_failed"
    assert caught.value.accepted_unknown and caught.value.requests == len(state["calls"]) == 1
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize("stream", [False, True])
def test_dns_denial_happens_before_sent_checkpoint_and_does_not_count_external_post(
    provider, stream
):
    base, state = provider
    saved = []

    async def sent(payload):
        saved.append(payload)

    boundary = AgentModelGateway(SimpleNamespace(generation_allowed_hosts=[]))
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            boundary.run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=stream,
                on_request=sent,
            )
        )
    assert caught.value.code == "unsafe_address"
    assert caught.value.requests == 0 and not caught.value.accepted_unknown
    assert saved == state["calls"] == []


def test_stream_response_byte_limit_is_enforced_without_retry_or_raw_checkpoint(provider):
    base, state = provider
    state["responses"] = [(200, sse_response("chat", text="Oversized."), "text/event-stream")]
    boundary = AgentModelGateway(
        SimpleNamespace(generation_allowed_hosts=["127.0.0.1"], generation_max_response_bytes=64)
    )
    saved = []

    async def response_checkpoint(payload):
        saved.append(payload)

    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            boundary.run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=True,
                on_response=response_checkpoint,
            )
        )
    assert caught.value.accepted_unknown and caught.value.requests == len(state["calls"]) == 1
    assert saved == []


def test_reported_zero_output_usage_is_evidence_but_missing_output_field_is_not(provider):
    base, state = provider
    for usage, expected in [
        ({"completion_tokens": 0, "prompt_tokens": 7, "total_tokens": 7}, True),
        ({"prompt_tokens": 7, "total_tokens": 7}, False),
    ]:
        body = response("chat", text="Ready.")
        body["usage"] = usage
        state["responses"] = [(200, body)]
        if expected:
            result = asyncio.run(
                gateway().run_segment(
                    snapshot(base, "chat"),
                    "test-secret",
                    instructions="Answer safely.",
                    user_prompt="Hello.",
                )
            )
            assert result.usage["usage_reported"] and result.usage["output_tokens_reported"]
            assert result.usage["output_tokens"] == 0
        else:
            with pytest.raises(AgentGatewayError) as caught:
                asyncio.run(
                    gateway().run_segment(
                        snapshot(base, "chat"),
                        "test-secret",
                        instructions="Answer safely.",
                        user_prompt="Hello.",
                    )
                )
            assert caught.value.accepted_unknown and caught.value.requests == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_multimodal_history_cannot_bypass_protected_transport_or_offline_replay(
    provider, protocol, monkeypatch
):
    base, state = provider
    history = serialize_history(
        [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        [ImageUrl("https://invalid.example/reference.png", force_download=True)]
                    )
                ]
            )
        ]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("No secondary SDK download client may resolve a URL")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(AgentGatewayError, match="unsupported_agent_history_content"):
        asyncio.run(
            gateway().run_segment(
                snapshot(base, protocol),
                "test-secret",
                instructions="Inspect this.",
                history=history,
            )
        )
    assert state["calls"] == []


def test_intermediate_stream_usage_without_final_usage_does_not_refund_output_reservation(provider):
    base, state = provider
    body = sse_response("chat", text="Ready.", with_usage=False)
    first_frame, rest = body.split(b"\n\n", 1)
    first_chunk = json.loads(first_frame.removeprefix(b"data: "))
    first_chunk["usage"] = {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}
    body = b"data: " + json.dumps(first_chunk).encode() + b"\n\n" + rest
    state["responses"] = [(200, body, "text/event-stream")]
    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, "chat"),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
            stream=True,
        )
    )
    assert not result.usage["usage_reported"] and not result.usage["output_tokens_reported"]


def test_thinking_chunks_are_private_and_never_emitted_as_visible_delta(provider):
    base, state = provider
    body = sse_response("chat", text="Visible answer.")
    thought = {
        "id": "chatcmpl-local",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": None,
                "delta": {"reasoning_content": "Private internal thought."},
            }
        ],
    }
    body = b"data: " + json.dumps(thought).encode() + b"\n\n" + body
    state["responses"] = [(200, body, "text/event-stream")]
    observed = []

    async def delta(text):
        observed.append(text)

    result = asyncio.run(
        gateway().run_segment(
            snapshot(base, "chat"),
            "test-secret",
            instructions="Answer safely.",
            user_prompt="Hello.",
            stream=True,
            on_text_delta=delta,
        )
    )
    assert "Private internal thought." in json.dumps(result.history)
    assert "".join(observed) == "Visible answer."


@pytest.mark.parametrize("stream", [False, True])
def test_request_checkpoint_deadline_stops_before_post(provider, stream):
    base, state = provider

    async def waiting(payload):
        await asyncio.Event().wait()

    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=stream,
                timeout_seconds=0.15,
                on_request=waiting,
            )
        )
    assert caught.value.requests == 0 and not caught.value.accepted_unknown
    assert state["calls"] == []


@pytest.mark.parametrize("stream", [False, True])
def test_redirect_does_not_make_a_second_post_and_raw_status_is_recorded(provider, stream):
    base, state = provider
    state["responses"] = [(302, b"provider-error test-secret")]
    saved = []

    async def response_checkpoint(payload):
        saved.append(payload)

    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            gateway().run_segment(
                snapshot(base, "chat"),
                "test-secret",
                instructions="Answer safely.",
                user_prompt="Hello.",
                stream=stream,
                on_response=response_checkpoint,
            )
        )
    assert caught.value.requests == len(state["calls"]) == 1
    assert not caught.value.accepted_unknown and caught.value.http_status == 302
    assert saved[0]["status_code"] == 302 and saved[0]["body_b64"] == ""
