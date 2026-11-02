"""Protected factory uses real framework parsing and an entirely local HTTP transport."""

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from short_drama.agent.gateway.contracts import AgentGatewayError, ModelCallbacks


class LocalTransport:
    def __init__(self, status=200):
        self.status, self.requests = status, []

    def request(self, method, url, *, body, on_send, **kwargs):
        on_send()
        self.requests.append(body)
        if self.status != 200:
            return self.status, {"Content-Type": "application/json"}, b'{"error":{}}'
        function = body.get("tools", [{}])[0].get("function", {})
        message = {"role": "assistant", "content": "echo factory-secret"}
        finish = "stop"
        if function:
            message["tool_calls"] = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": '{"answer":"ok"}',
                    },
                }
            ]
            finish = "tool_calls"
        content = json.dumps(
            {
                "id": "local",
                "object": "chat.completion",
                "created": 1,
                "model": "controlled",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
        ).encode()
        return 200, {"Content-Type": "application/json"}, content


def configured():
    return {
        "service_type": "text",
        "provider": "compatible",
        "base_url": "https://local.invalid/v1",
        "model_key": "controlled",
    }


class DeadlineTransport(LocalTransport):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.remaining = []
        self.release, self.finished = Event(), Event()

    def request(self, method, url, *, body, on_send, deadline, **kwargs):
        try:
            on_send()
            self.requests.append(body)
            self.remaining.append(deadline - time.monotonic())
            if self.failure is not None:
                raise self.failure
            headers = {"Content-Type": "text/event-stream"}
            kwargs["on_headers"](200, headers)
            chunk = {
                "id": "deadline-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "controlled",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "受控流"},
                        "finish_reason": "stop",
                    }
                ],
            }
            content = b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"
            kwargs["on_chunk"](content)
            assert self.release.wait(5), "bounded transport watchdog expired"
            return 200, headers, content
        finally:
            self.finished.set()


def deadline_receipts(timeout_seconds):
    records = {name: [] for name in ("admitted", "raw", "usage", "errors")}

    async def before_send(payload):
        records["admitted"].append(payload)

    async def save_raw(payload):
        records["raw"].append(payload)

    async def usage(payload):
        records["usage"].append(payload)

    async def error(payload):
        records["errors"].append(payload)

    return ModelCallbacks(
        settings=SimpleNamespace(),
        before_send=before_send,
        save_raw=save_raw,
        usage=usage,
        error=error,
        timeout_seconds=timeout_seconds,
    ), records


@pytest.mark.parametrize(
    "secrets,steps",
    [
        (
            ["ab", "abc", "bc", "ab", ""],
            [
                ("xab", False, "x", "ab"),
                ("c", False, "", "abc"),
                ("Zab", False, "[redacted]Z", "ab"),
                ("", True, "[redacted]", ""),
            ],
        ),
        (
            ["aba", "ababa", "bab"],
            [
                ("xab", False, "x", "ab"),
                ("abaZ", False, "[redacted]Z", ""),
                ("ba", False, "", "ba"),
                ("b", False, "", "bab"),
                ("", True, "[redacted]", ""),
            ],
        ),
        (
            ["abcd", "bc"],
            [("ab", False, "", "ab"), ("Xb", False, "abX", "b"), ("", True, "b", "")],
        ),
        (
            ["雨", "雨夜", "🌧️"],
            [
                ("前雨", False, "前", "雨"),
                ("夜🌧", False, "[redacted]", "🌧"),
                ("️后", False, "[redacted]后", ""),
            ],
        ),
        (["token"], [("to", False, "", "to"), ("", True, "to", "")]),
        (["ab", "abc"], [("ab", True, "[redacted]", "")]),
        (["", ""], [("雨🌧️\n", False, "雨🌧️\n", ""), ("", True, "", "")]),
    ],
    ids=["prefix", "overlap", "mismatch", "unicode", "partial-flush", "final-input", "empty"],
)
def test_redactor_preserves_exact_chunk_outputs_and_pending_prefixes(secrets, steps):
    from short_drama.agent.gateway.transport import _TextRedactor

    redactor = _TextRedactor(secrets)
    for text, final, expected, pending in steps:
        assert redactor.add(text, final=final) == expected
        assert redactor.pending == pending


class LargeRawTransport(LocalTransport):
    def __init__(self):
        super().__init__()
        # Valid SSE comments isolate whole-raw redaction from visible-text aggregation.
        padding = (": 雨" + "a" * 60 + "\n\n") * 6400
        events = []
        for index, text in enumerate(("echo factory-", "secret / factory-secret")):
            events.append(
                {
                    "id": "large-local",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "controlled",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": "stop" if index else None,
                        }
                    ],
                }
            )
        events.append(
            {
                "id": "large-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "controlled",
                "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
            }
        )
        self.content = (
            padding
            + "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            + "data: [DONE]\n\n"
        ).encode()

    def request(self, method, url, *, body, on_send, on_headers, on_chunk, **kwargs):
        on_send()
        self.requests.append(body)
        assert method == "POST" and body["stream"] is True
        headers = {"Content-Type": "text/event-stream"}
        on_headers(200, headers)
        for offset in range(0, len(self.content), 65536):
            on_chunk(self.content[offset : offset + 65536])
        return 200, headers, self.content


def test_large_raw_redaction_completes_sdk_receipt_and_usage_within_model_deadline():
    from short_drama.agent.gateway.langchain import build_chat_model

    callbacks, records = deadline_receipts(3)
    transport = LargeRawTransport()
    deltas, content = [], []

    async def save_raw(payload):
        # The real ledger also yields to a persistence thread before stream close can finish.
        await asyncio.to_thread(records["raw"].append, payload)

    async def on_text_delta(text):
        deltas.append(text)

    async def on_model_content(payload):
        content.append(payload)

    model = build_chat_model(
        configured(),
        "factory-secret",
        callbacks=replace(
            callbacks,
            save_raw=save_raw,
            on_text_delta=on_text_delta,
            on_model_content=on_model_content,
        ),
        transport=transport,
    )
    result = asyncio.run(model.ainvoke([HumanMessage(content="大回执")]))

    assert result.text == "echo [redacted] / [redacted]" == "".join(deltas)
    assert len(transport.requests) == len(records["admitted"]) == len(records["raw"]) == 1
    raw = records["raw"][0]
    assert raw["codec"] == "agent.raw-response" and raw["status_code"] == 200
    assert raw["content_type"] == "text/event-stream"
    assert base64.b64decode(raw["body_b64"]) == transport.content.replace(
        b"factory-secret", b"[redacted]"
    )
    assert len(records["usage"]) == 1 and records["errors"] == []
    usage = records["usage"][0]
    assert (usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]) == (12, 7, 19)
    assert usage["external_requests"] == 1
    assert usage["usage_reported"] is True and usage["output_tokens_reported"] is True
    assert content[-1]["channel"] == "end" and content[-1]["status"] == "succeeded"


def test_cancel_after_large_raw_persistence_keeps_unknown_without_usage_or_retry():
    from short_drama.agent.gateway.langchain import build_chat_model

    callbacks, records = deadline_receipts(30)
    transport = LargeRawTransport()
    content = []

    async def invoke():
        raw_saved, release = asyncio.Event(), asyncio.Event()

        async def save_raw(payload):
            await asyncio.to_thread(records["raw"].append, payload)
            raw_saved.set()
            await release.wait()

        async def on_model_content(payload):
            content.append(payload)

        model = build_chat_model(
            configured(),
            "factory-secret",
            callbacks=replace(callbacks, save_raw=save_raw, on_model_content=on_model_content),
            transport=transport,
        )
        task = asyncio.create_task(model.ainvoke([HumanMessage(content="保存后停止")]))
        try:
            await asyncio.wait_for(raw_saved.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)

    asyncio.run(invoke())

    assert len(transport.requests) == len(records["admitted"]) == len(records["raw"]) == 1
    assert base64.b64decode(records["raw"][0]["body_b64"]) == transport.content.replace(
        b"factory-secret", b"[redacted]"
    )
    assert records["usage"] == [] and len(records["errors"]) == 1
    error = records["errors"][0]
    assert error["code"] == "agent_model_cancelled" and error["requests"] == 1
    assert error["accepted_unknown"] is True and error["not_accepted"] is False
    assert content[-1]["channel"] == "end" and content[-1]["status"] == "interrupted"


@asynccontextmanager
async def blocked_sdk_close(model, transport, monkeypatch):
    from short_drama.agent.gateway.transport import _CapturedStream

    close_entered = asyncio.Event()
    original_close = _CapturedStream.aclose

    async def observe_close(stream):
        close_entered.set()
        return await original_close(stream)

    monkeypatch.setattr(_CapturedStream, "aclose", observe_close)
    task = asyncio.create_task(model.ainvoke([HumanMessage(content="整次期限")]))
    try:
        await asyncio.wait_for(close_entered.wait(), 2)
        yield task
    finally:
        transport.release.set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
        assert await asyncio.to_thread(transport.finished.wait, 2)


@pytest.mark.parametrize("configured_timeout", [None, 300])
def test_run_callbacks_use_deployment_timeout_and_keep_settings_protocol(configured_timeout):
    from short_drama.agent.runtime.model_ledger import RunModelLedger

    settings = SimpleNamespace()
    if configured_timeout is not None:
        settings.agent_model_timeout_seconds = configured_timeout
    callbacks = RunModelLedger(SimpleNamespace(settings=settings)).callbacks()
    assert callbacks.timeout_seconds == (120 if configured_timeout is None else 300)


def test_run_timeout_reaches_actual_sdk_deadline(monkeypatch):
    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.core.config import Settings

    formal = RunModelLedger(
        SimpleNamespace(
            settings=Settings(_env_file=None, agent_model_timeout_seconds=1), events=None
        )
    ).callbacks()
    capture, records = deadline_receipts(1)
    callbacks = replace(
        formal,
        before_send=capture.before_send,
        save_raw=capture.save_raw,
        usage=capture.usage,
        error=capture.error,
    )
    transport = DeadlineTransport()
    model = build_chat_model(configured(), "", callbacks=callbacks, transport=transport)

    async def invoke():
        async with blocked_sdk_close(model, transport, monkeypatch) as task:
            assert 0 < transport.remaining[0] <= 1
            with pytest.raises(AgentGatewayError) as caught:
                await asyncio.wait_for(asyncio.shield(task), 2)
            assert caught.value.code == "timeout"

    asyncio.run(invoke())
    assert len(transport.requests) == len(records["admitted"]) == 1
    assert records["errors"][0]["code"] == "timeout"
    assert records["raw"] == records["usage"] == []


def test_expired_local_stream_deadline_is_timeout_without_retry(monkeypatch):
    from short_drama.agent.gateway.langchain import build_chat_model

    callbacks, records = deadline_receipts(0.2)
    transport = DeadlineTransport()
    model = build_chat_model(configured(), "", callbacks=callbacks, transport=transport)

    async def invoke():
        async with blocked_sdk_close(model, transport, monkeypatch) as task:
            with pytest.raises(AgentGatewayError) as caught:
                await asyncio.wait_for(asyncio.shield(task), 2)
            assert caught.value.code == "timeout"
            assert caught.value.requests == 1 and caught.value.accepted_unknown

    asyncio.run(invoke())
    assert len(transport.requests) == len(records["errors"]) == 1
    assert records["errors"][0]["accepted_unknown"] is True
    assert records["errors"][0]["not_accepted"] is False
    assert records["raw"] == records["usage"] == []


def test_deadline_before_transport_send_is_not_accepted():
    from short_drama.agent.gateway.langchain import build_chat_model

    callbacks, records = deadline_receipts(0.2)
    entered = []

    async def invoke():
        admission = asyncio.Event()

        async def before_send(payload):
            entered.append(True)
            await admission.wait()

        transport = DeadlineTransport()
        model = build_chat_model(
            configured(),
            "",
            callbacks=replace(callbacks, before_send=before_send),
            transport=transport,
        )
        try:
            with pytest.raises(AgentGatewayError) as caught:
                await asyncio.wait_for(model.ainvoke([HumanMessage(content="尚未发送")]), 2)
            assert caught.value.code == "timeout"
            assert caught.value.requests == 0 and not caught.value.accepted_unknown
            assert transport.requests == []
        finally:
            admission.set()
            transport.release.set()
            assert await asyncio.to_thread(transport.finished.wait, 2)

    asyncio.run(invoke())
    assert entered and len(records["errors"]) == 1
    assert records["errors"][0]["not_accepted"] is True
    assert records["errors"][0]["accepted_unknown"] is False
    assert records["raw"] == records["usage"] == []


def test_explicit_cancel_keeps_model_cancelled_with_long_deadline(monkeypatch):
    from short_drama.agent.gateway.langchain import build_chat_model

    callbacks, records = deadline_receipts(300)
    transport = DeadlineTransport()
    model = build_chat_model(configured(), "", callbacks=callbacks, transport=transport)

    async def invoke():
        async with blocked_sdk_close(model, transport, monkeypatch) as task:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)

    asyncio.run(invoke())
    assert len(transport.requests) == len(records["errors"]) == 1
    error = records["errors"][0]
    assert error["code"] == "agent_model_cancelled" and error["requests"] == 1
    assert error["accepted_unknown"] is True and error["not_accepted"] is False
    assert records["raw"] == records["usage"] == []


def test_sdk_timeout_without_local_expiry_keeps_provider_classification():
    from short_drama.agent.gateway.langchain import build_chat_model

    callbacks, records = deadline_receipts(300)
    transport = DeadlineTransport(TimeoutError("controlled SDK timeout"))
    model = build_chat_model(configured(), "", callbacks=callbacks, transport=transport)
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(model.ainvoke([HumanMessage(content="SDK 异常")]))
    assert caught.value.code == "agent_provider_error"
    assert len(transport.requests) == caught.value.requests == 1
    assert caught.value.accepted_unknown and records["errors"][0]["not_accepted"] is False
    assert records["raw"] == records["usage"] == []


@pytest.mark.parametrize("origin", ["admission", "transport"])
def test_gateway_and_generation_errors_keep_existing_precedence(origin):
    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.ai.types import GenerationError

    callbacks, records = deadline_receipts(300)
    transport = DeadlineTransport(GenerationError("controlled_generation", accepted_unknown=True))
    if origin == "admission":

        async def before_send(payload):
            raise AgentGatewayError("agent_admission_denied")

        callbacks = replace(callbacks, before_send=before_send)
    model = build_chat_model(configured(), "", callbacks=callbacks, transport=transport)
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(model.ainvoke([HumanMessage(content="保留精确错误")]))
    sent = 0 if origin == "admission" else 1
    assert caught.value.code == (
        "agent_admission_denied" if origin == "admission" else "controlled_generation"
    )
    assert len(transport.requests) == caught.value.requests == sent
    assert caught.value.accepted_unknown is bool(sent)
    assert records["errors"][0]["not_accepted"] is (not sent)
    assert records["raw"] == records["usage"] == []


class UnusableTransport(LocalTransport):
    def __init__(self, failure, *, reported=True):
        super().__init__(status=400 if failure == "rejected" else 200)
        self.failure, self.reported = failure, reported

    def request(self, *args, **kwargs):
        status, headers, content = super().request(*args, **kwargs)
        if status != 200:
            return status, headers, content
        payload = json.loads(content)
        choice = payload["choices"][0]
        choice["finish_reason"] = "length" if self.failure == "length" else "tool_calls"
        if self.failure == "invalid_tool_calls":
            choice["message"]["tool_calls"] = [
                {
                    "id": "malformed",
                    "type": "function",
                    "function": {
                        "name": "unused",
                        "arguments": "{broken",
                    },
                }
            ]
        if self.reported:
            payload["usage"] = {
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
                "prompt_tokens_details": {"cached_tokens": 4},
            }
        else:
            payload.pop("usage", None)
        return status, headers, json.dumps(payload).encode()


@pytest.mark.parametrize(
    "failure,reported",
    [
        ("length", True),
        ("invalid_tool_calls", True),
        ("length", False),
        ("rejected", False),
    ],
)
def test_unusable_response_keeps_usage_and_acceptance_facts(failure, reported):
    from short_drama.agent.gateway.contracts import ModelCallbacks
    from short_drama.agent.gateway.langchain import build_chat_model

    events, usage, errors = [], [], []

    async def raw(payload):
        events.append("raw")

    async def settle(payload):
        events.append("usage")
        usage.append(payload)

    async def failed(payload):
        events.append("error")
        errors.append(payload)

    transport = UnusableTransport(failure, reported=reported)
    model = build_chat_model(
        configured(),
        "",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(),
            save_raw=raw,
            usage=settle,
            error=failed,
            stream=False,
        ),
        transport=transport,
    )
    with pytest.raises(AgentGatewayError) as raised:
        asyncio.run(model.ainvoke([HumanMessage(content="输出仍须计费")]))
    assert len(transport.requests) == raised.value.requests == 1
    assert errors[0]["not_accepted"] is (failure == "rejected")
    assert events == (["raw", "error"] if failure == "rejected" else ["raw", "usage", "error"])
    if failure != "rejected":
        assert usage[0]["output_tokens_reported"] is reported
        assert usage[0].get("output_tokens", 0) == (7 if reported else 0)


def test_factory_returns_framework_model_and_protects_every_structured_call():
    from short_drama.agent.gateway.contracts import ModelCallbacks
    from short_drama.agent.gateway.langchain import build_chat_model

    class Answer(BaseModel):
        answer: str

    admitted, raw, usage = [], [], []

    async def before_send(payload):
        admitted.append(payload)

    async def save_raw(payload):
        raw.append(payload)

    async def save_usage(payload):
        usage.append(payload)

    transport = LocalTransport()
    model = build_chat_model(
        configured(),
        "factory-secret",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(),
            before_send=before_send,
            save_raw=save_raw,
            usage=save_usage,
            stream=False,
        ),
        transport=transport,
    )
    assert isinstance(model, BaseChatModel)
    assert "factory-secret" not in json.dumps(model.model_dump())

    async def invoke():
        result = await model.ainvoke([HumanMessage(content="讨论")])
        assert result.text == "echo [redacted]"
        selected = await model.with_structured_output(Answer).ainvoke(
            [HumanMessage(content="选择")]
        )
        assert selected.answer == "ok"

    asyncio.run(invoke())
    assert len(transport.requests) == len(admitted) == len(raw) == len(usage) == 2
    assert usage[0]["output_tokens"] == 2
    assert admitted[0]["model_call_id"] != admitted[1]["model_call_id"]
    assert all("factory-secret" not in base64.b64decode(item["body_b64"]).decode() for item in raw)


def test_anthropic_factory_sends_native_tool_result_and_no_hidden_retry():
    from langchain_core.messages import AIMessage, ToolMessage

    from short_drama.agent.gateway.contracts import ModelCallbacks
    from short_drama.agent.gateway.langchain import build_chat_model

    class AnthropicTransport(LocalTransport):
        def request(self, method, url, *, body, on_send, **kwargs):
            on_send()
            self.requests.append(body)
            assert body["messages"][-1]["content"][0]["type"] == "tool_result"
            content = json.dumps(
                {
                    "id": "anthropic",
                    "type": "message",
                    "role": "assistant",
                    "model": "controlled",
                    "content": [{"type": "text", "text": "native tool_result 完成"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 11, "output_tokens": 3},
                }
            ).encode()
            return 200, {"Content-Type": "application/json"}, content

    admitted, receipts = [], []

    async def admit(payload):
        admitted.append(payload)

    async def usage(payload):
        receipts.append(payload)

    transport = AnthropicTransport()
    model = build_chat_model(
        {
            **configured(),
            "provider": "anthropic",
            "runtime_profile": {
                "api_format": "claude",
                "protocol": "messages",
            },
        },
        "factory-secret",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(),
            before_send=admit,
            usage=usage,
            stream=False,
        ),
        transport=transport,
    )
    result = asyncio.run(
        model.ainvoke(
            [
                HumanMessage(content="读取方法"),
                AIMessage(content="", tool_calls=[{"id": "native", "name": "read", "args": {}}]),
                ToolMessage(content="冻结正文", tool_call_id="native"),
            ]
        )
    )
    assert result.text == "native tool_result 完成"
    assert len(transport.requests) == len(admitted) == len(receipts) == 1
    assert receipts[0]["output_tokens"] == 3


def test_sdk_does_not_hide_two_retries_after_a_503():
    from short_drama.agent.gateway.contracts import ModelCallbacks
    from short_drama.agent.gateway.langchain import build_chat_model

    transport = LocalTransport(status=503)
    admitted = []

    async def before_send(payload):
        admitted.append(payload)

    model = build_chat_model(
        configured(),
        "",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(),
            before_send=before_send,
            stream=False,
        ),
        transport=transport,
    )
    with pytest.raises(AgentGatewayError) as failure:
        asyncio.run(model.ainvoke([HumanMessage(content="失败一次")]))
    assert failure.value.accepted_unknown
    assert len(transport.requests) == len(admitted) == 1


def test_capability_probe_uses_bounded_real_echo_graph_with_native_continuation():
    from short_drama.agent.gateway.langchain import LangChainGateway

    class EchoTransport(LocalTransport):
        def request(self, method, url, *, body, on_send, on_headers, on_chunk, **kwargs):
            on_send()
            self.requests.append(body)
            continued = body["messages"][-1]["role"] == "tool"
            delta = (
                {"role": "assistant", "content": "AGENT_CAPABILITY_OK"}
                if continued
                else {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "echo",
                            "type": "function",
                            "function": {
                                "name": "verify_agent_echo",
                                "arguments": '{"value":"agent-capability-v1"}',
                            },
                        }
                    ],
                }
            )
            content = (
                "data: "
                + json.dumps(
                    {
                        "id": "echo",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "controlled",
                        "choices": [
                            {
                                "index": 0,
                                "delta": delta,
                                "finish_reason": "stop" if continued else "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                    }
                )
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            headers = {"Content-Type": "text/event-stream"}
            on_headers(200, headers)
            on_chunk(content)
            return 200, headers, content

    transport = EchoTransport()
    evidence = asyncio.run(
        LangChainGateway(SimpleNamespace(), transport).validate_capability(
            configured(),
            "",
            conversation_id="probe",
        )
    )
    assert evidence.tool_calling and evidence.tool_result_continuation
    assert evidence.streaming == "verified" and evidence.requests == len(transport.requests) == 2


def test_skill_dependencies_are_added_after_selector_without_shared_scope_leaks():
    from langchain.agents.middleware import LLMToolSelectorMiddleware

    from short_drama.agent.gateway.contracts import ModelCallbacks
    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import SkillToolsMiddleware
    from short_drama.agent.tools.registry import get_spec

    class SelectionTransport(LocalTransport):
        def request(self, method, url, *, body, on_send, **kwargs):
            on_send()
            self.requests.append(body)
            function = body.get("tools", [{}])[0].get("function", {})
            selected = "tools" in function.get("parameters", {}).get("properties", {})
            message = {"role": "assistant", "content": "完成"}
            if selected:
                message["tool_calls"] = [
                    {
                        "id": "selection",
                        "type": "function",
                        "function": {"name": function["name"], "arguments": '{"tools":[]}'},
                    }
                ]
            return (
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "id": "selection",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "controlled",
                        "choices": [
                            {
                                "index": 0,
                                "message": message,
                                "finish_reason": "tool_calls" if selected else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                    }
                ).encode(),
            )

    transport = SelectionTransport()
    model = build_chat_model(
        configured(),
        "",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(),
            stream=False,
        ),
        transport=transport,
    )
    tool = get_spec("runtime__memory_catalog").handler
    selector = LLMToolSelectorMiddleware(model=model, max_tools=16, always_include=[])
    graph = build_agent(
        model=model,
        tools=[tool],
        middleware=[selector, SkillToolsMiddleware([tool])],
        checkpointer=None,
        system_prompt="讨论",
    )

    async def invoke(label, required):
        return await graph.ainvoke(
            {
                "messages": [HumanMessage(content=label)],
                "permitted_tool_names": [tool.name],
                "skill_activations": [
                    {
                        "id": label,
                        "active": True,
                        "skill": {"required_tools": required},
                    }
                ],
            },
            context=SimpleNamespace(tool_lookup=get_spec),
        )

    async def parallel():
        await asyncio.gather(invoke("范围甲", [tool.name]), invoke("范围乙", []))

    asyncio.run(parallel())
    main = [body for body in transport.requests if body["messages"][0]["content"] == "讨论"]
    by_scope = {body["messages"][-1]["content"]: body.get("tools", []) for body in main}
    assert [item["function"]["name"] for item in by_scope["范围甲"]] == [tool.name]
    assert by_scope["范围乙"] == [] and selector.always_include == []
