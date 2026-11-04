"""Public trace channels use UTF-8 byte positions and provider-visible reasoning only."""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from short_drama.agent.gateway.contracts import ModelCallbacks
from short_drama.agent.gateway.langchain import build_chat_model
from short_drama.agent.runtime.delta_buffer import _DeltaBuffer
from short_drama.agent.runtime.trace_content import display_safe, reasoning_texts, utf8_preview
from tests.support.assistant_http import provider as provider
from tests.support.assistant_http import response
from tests.unit.standard.agent.test_assistant_model_protocols import (
    SETTINGS,
    claude_response,
    configured,
)


def test_sse_drains_full_pages_then_backs_off_idle_and_ends_on_revoked_access(monkeypatch):
    from contextlib import contextmanager

    from short_drama.api.v1.assistant.conversations import events
    from short_drama.core.exceptions import WorkflowError

    cursors, waits, sessions = [], [], []

    @contextmanager
    def factory():
        session = SimpleNamespace(info={})
        sessions.append(session)
        yield session

    def read(service, conversation_id, cursor, *, verify_session):
        assert conversation_id == "1" and verify_session is True
        assert service.session.info["actor"] == "actor"
        cursors.append(cursor)
        if len(cursors) <= 3:
            end = min(cursor + 100, 250)
            return [
                SimpleNamespace(seq=seq, model_dump_json=lambda: "{}")
                for seq in range(cursor + 1, end + 1)
            ]
        if len(cursors) <= 8:
            return []
        raise WorkflowError("forbidden", "已撤销", 403)

    async def sleep(delay):
        waits.append(delay)

    async def connected():
        return False

    monkeypatch.setattr(events.AgentRunService, "events", read)
    monkeypatch.setattr(events.asyncio, "sleep", sleep)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=factory, settings=None)),
        state=SimpleNamespace(actor="actor"),
        is_disconnected=connected,
    )

    async def consume():
        response = await events.conversation_events("1", request, 0, None)
        return [frame async for frame in response.body_iterator]

    frames = asyncio.run(consume())
    assert len(frames) == 251 and "access-ended" in frames[-1]
    assert cursors == [0, 100, 200, 250, 250, 250, 250, 250, 250]
    assert len({id(session) for session in sessions}) == len(cursors)
    assert waits[0] == pytest.approx(0.2) and all(0.2 <= delay <= 1 for delay in waits)
    assert waits[-1] == 1 and waits == sorted(waits)
    assert len(waits) == 6


@pytest.mark.parametrize("streaming", [False, True])
def test_live_diagnostics_bound_and_redact_archived_selector_and_main_receipts(streaming):
    from tests.support.agent_runtime_live import model_diagnostic

    secret = "fixture-diagnostic-private-key"
    names = [secret, "雨" * 100, *[f"tool_{index}" for index in range(38)]]
    arguments = json.dumps({"tools": names}, ensure_ascii=False)
    if streaming:
        payloads = [
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]}}
                ]
            }
            for fragment in (arguments[:17], arguments[17:])
        ]
        raw = (
            b"".join(
                b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n"
                for payload in payloads
            )
            + b"data: [DONE]\n\n"
        )
    else:
        raw = json.dumps(
            {"choices": [{"message": {"tool_calls": [{"function": {"arguments": arguments}}]}}]},
            ensure_ascii=False,
        ).encode()
    turn = SimpleNamespace(
        id=11,
        run_id=12,
        scope_id=13,
        usage={"source": "tool_selection"},
        request_messages=[
            {
                "body": {
                    "messages": [
                        {"role": "system", "content": "不属于用户查询"},
                        {"role": "user", "content": secret + "雨" * 5000},
                    ]
                }
            }
        ],
        response={
            "raw": {
                "body_b64": base64.b64encode(raw).decode(),
                "content_type": "text/event-stream" if streaming else "application/json",
                "headers": {"authorization": secret},
            }
        },
    )
    selected = model_diagnostic(turn, secret)
    assert selected["selection_parsed"] is True and len(selected["selected_tool_names"]) == 32
    assert selected["selected_tool_names"][0] == "[已隐藏]"
    assert all(len(name.encode()) <= 160 for name in selected["selected_tool_names"])
    assert selected["query"]["truncated"] and len(selected["query"]["preview"].encode()) <= 12000
    assert "不属于用户查询" not in selected["query"]["preview"]

    turn.usage = {"source": "assistant"}
    turn.request_messages = [
        {"body": {"tools": [{"function": {"name": name}} for name in names * 2]}}
    ]
    turn.response = {"graph_message": {"data": {"content": secret + "答" * 1000}}}
    main = model_diagnostic(turn, secret)
    assert main["omitted_bound_tools"] == 16 and len(main["bound_tool_names"]) == 64
    assert all(len(name.encode()) <= 160 for name in main["bound_tool_names"])
    assert main["final_reply"]["truncated"] and len(main["final_reply"]["preview"].encode()) <= 2000
    encoded = json.dumps([selected, main], ensure_ascii=False)
    assert secret not in encoded and "body_b64" not in encoded and "headers" not in encoded
    assert selected["scope_id"] == main["scope_id"] == "13"


@pytest.mark.parametrize("producer", ["sandbox", "trailing-delta"])
def test_trace_preview_stops_at_first_incomplete_utf8_character(monkeypatch, producer):
    from copy import deepcopy

    from short_drama.agent.runtime import events
    from short_drama.core.time import utcnow
    from short_drama.dao.assistant.execution.trace import AssistantTraceDAO

    now = utcnow()
    payload = {
        "block_id": "1",
        "block_version": "1",
        "run_id": "2",
        "execution_scope_id": "3",
        "tool_call_id": "4",
        "kind": "sandbox",
        "source": "execute",
        "action": "execute",
        "title": "执行日志",
        "target": {"kind": "sandbox", "display_name": "测试"},
        "started_at": now,
        "updated_at": now,
        "channels": {},
    }
    pieces = ["a" * 16383, "🙂", "b"]
    if producer == "sandbox":
        saved = [SimpleNamespace(payload=payload)]
        monkeypatch.setattr(events.AssistantTraceDAO, "latest", lambda *_: saved[-1])
        monkeypatch.setattr(events, "append_event", lambda *_args, **_kwargs: None)

        def save(_session, _conversation, _run, block):
            saved.append(SimpleNamespace(payload=deepcopy(block)))
            return block

        monkeypatch.setattr(events, "save_block_locked", save)
        adapter = events.AgentEventAdapter(None)
        for piece in pieces:
            adapter.sandbox_locked(
                None,
                SimpleNamespace(id=1, next_event_seq=1),
                SimpleNamespace(id=2),
                SimpleNamespace(id=4),
                "sandbox.log",
                {"stream": "stdout", "content": piece},
            )
        channel = saved[-1].payload["channels"]["stdout"]
    else:
        offset, deltas = 0, []
        for piece in pieces:
            deltas.append(
                SimpleNamespace(
                    payload={
                        "block_version": "2",
                        "channel": "stdout",
                        "offset": offset,
                        "delta": piece,
                    },
                    created_at=now,
                )
            )
            offset += len(piece.encode("utf-8"))
        channel = (
            AssistantTraceDAO(None)
            .block(SimpleNamespace(payload=payload), deltas)
            .channels["stdout"]
            .model_dump()
        )
    assert channel["preview"] == "a" * 16383
    assert channel["total_bytes"] == 16388 and channel["truncated"] is True


def test_assistant_delta_offsets_are_utf8_bytes():
    saved = []
    store = SimpleNamespace(
        save_delta=lambda _claim, text, offset: saved.append((text, offset)) or "ok"
    )
    buffer = _DeltaBuffer(store, None)
    buffer.add("雨🙂", final=True)
    buffer.add("声", final=True)
    assert saved == [("雨🙂", 0), ("声", 7)]


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_sdk_visible_reasoning_is_separate_from_normal_text(provider, protocol):
    base, state = provider
    body = claude_response() if protocol == "claude" else response(protocol, text="understood")
    if protocol == "responses":
        body["output"].insert(
            0,
            {
                "type": "reasoning",
                "id": "rs_local",
                "summary": [{"type": "summary_text", "text": "可见摘要雨🙂"}],
                "encrypted_content": "opaque-do-not-display",
            },
        )
    if protocol == "claude":
        body["content"].insert(
            0, {"type": "thinking", "thinking": "可见摘要雨🙂", "signature": "private-signature"}
        )
    state["responses"] = [(200, body)]
    received = []

    async def capture(value):
        received.append(value)

    result = asyncio.run(
        build_chat_model(
            configured(base, protocol),
            "fixture-key",
            callbacks=ModelCallbacks(settings=SETTINGS, stream=False, on_model_content=capture),
        ).ainvoke([HumanMessage(content="Proceed")])
    )
    assert result.text == "understood"
    assert "".join(item["delta"] for item in received if item["channel"] == "text") == "understood"
    thoughts = [item["delta"] for item in received if item["channel"] == "reasoning"]
    assert thoughts == ([] if protocol == "chat" else ["可见摘要雨🙂"])
    assert len({item["model_call_id"] for item in received}) == 1
    assert all(item["source"] == "assistant" for item in received)
    assert "private-signature" not in json.dumps(received)
    assert "opaque-do-not-display" not in json.dumps(received)


def test_plain_plan_and_opaque_blocks_never_become_reasoning():
    chunk = AIMessageChunk(
        content=[
            {"type": "text", "text": "我先核对剧本再修改"},
            {"type": "reasoning", "encrypted_content": "cipher"},
            {"type": "non_standard", "value": {"signature": "opaque"}},
        ]
    )
    assert list(reasoning_texts(chunk)) == []


def test_trace_redacts_details_and_utf8_preview_is_bounded():
    safe = display_safe(
        {
            "api_key": "secret",
            "nested": {
                "locator": "minio://bucket/key",
                "message": "token=abc123 minio://bucket/key",
                "signature": "private",
            },
        }
    )
    assert safe == {"nested": {"message": "[已隐藏] [内部资源]"}}
    preview = utf8_preview("雨🙂" * 10000)
    assert len(preview.encode("utf-8")) <= 16384
    assert "\ufffd" not in preview


def test_model_projection_batches_small_chunks_and_flushes_each_call_before_end(monkeypatch):
    from short_drama.agent.runtime import delta_buffer
    from short_drama.agent.runtime.events import AgentEventAdapter

    monkeypatch.setattr(delta_buffer.time, "monotonic", lambda: 1.0)
    adapter = AgentEventAdapter(SimpleNamespace())
    saved, ended = [], []
    adapter.save_delta = lambda event, text, offset: (
        saved.append((event["model_call_id"], text, offset)) or "ok"
    )
    adapter._finish_model = lambda call_id, status: ended.append((call_id, status))
    for call_id in ("one", "two"):
        for text in ("雨🙂", "声", "后"):
            adapter.model_content_event(
                {
                    "model_call_id": call_id,
                    "source": "assistant",
                    "channel": "reasoning",
                    "delta": text,
                }
            )
    assert saved == [("one", "雨🙂", 0), ("two", "雨🙂", 0)]
    adapter.model_content_event({"model_call_id": "one", "channel": "end"})
    assert saved[-1] == ("one", "声后", 7) and ended == [("one", "succeeded")]


@pytest.mark.parametrize(
    "stderr", ["fatal-error", "E" * 21000], ids=["short-stderr", "large-stderr"]
)
def test_command_archives_each_large_channel_at_trace_preview_limit(monkeypatch, stderr):
    from threading import Lock

    from short_drama.agent.sandbox import backend as sandbox_backend
    from short_drama.agent.sandbox.backend import CommandBackend, sandbox_call

    monkeypatch.setattr(sandbox_backend, "ARCHIVE_MAX_BYTES", 32768)
    events, archives = [], []

    def run(_command, *, opts, handlers):
        handlers.on_stdout(SimpleNamespace(text="a" * 70000))
        handlers.on_stderr(SimpleNamespace(text=stderr))
        return SimpleNamespace(id="execution", exit_code=1)

    backend = CommandBackend.__new__(CommandBackend)
    backend.workspace = SimpleNamespace(
        context=SimpleNamespace(settings=SimpleNamespace(agent_sandbox_output_max_bytes=65536)),
        admitted=lambda *_: None,
    )
    backend.scope_id, backend.fence, backend._state_lock = 1, (1, "lease"), Lock()
    backend._sandbox = SimpleNamespace(commands=SimpleNamespace(run=run))
    backend._event = lambda kind, payload, _call: events.append((kind, payload))
    backend._archive = lambda data, _call, total, *stream: archives.append((data, total, stream))
    token = sandbox_call.set((None, "call", "execute"))
    try:
        result = backend._run("npm install fixture && node fixture.mjs", 120)
    finally:
        sandbox_call.reset(token)
    assert archives and archives[0][2] == ("stdout",)
    assert {payload["stream"] for kind, payload in events if kind == "sandbox.log"} == {
        "stdout",
        "stderr",
    }
    logs = {
        payload["stream"]: payload["content"] for kind, payload in events if kind == "sandbox.log"
    }
    assert logs["stderr"] == (stderr + "\n")[:16384]
    assert all(len(text.encode("utf-8")) <= 16384 for text in logs.values())
    assert result.truncated and len(result.output.encode("utf-8")) == 65536
    assert "fatal-error" not in result.output
    if len(stderr) > 16384:
        assert archives[1] == ((stderr + "\n").encode(), 21001, ("stderr",))
