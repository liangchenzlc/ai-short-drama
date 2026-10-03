"""Builtin skill snapshots survive worker crashes and real, offline SDK replay."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic_ai.messages import ToolReturnPart
from sqlalchemy import select
from test_agent_conversations import service
from test_agent_runtime import configure_local_gateway, expire, version
from test_agent_runtime import runtime_setup as runtime_setup

from short_drama.agent import model_gateway, tools
from short_drama.agent.model_gateway import deserialize_history, serialize_segment_result
from short_drama.agent.recovery import recover
from short_drama.agent.runtime import AgentRuntime
from short_drama.domain.agent import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def skill_provider():
    """Two local chat completions, including a genuine deferred read_skill call."""
    state = {"requests": [], "stopped": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(request)
            first = len(state["requests"]) == 1
            message = (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "skill-call-1",
                            "type": "function",
                            "function": {
                                "name": "read_skill",
                                "arguments": '{"name":"script.v1"}',
                            },
                        }
                    ],
                }
                if first
                else {"role": "assistant", "content": "已参考本集剧本规范。"}
            )
            body = json.dumps(
                {
                    "id": f"chatcmpl-skill-{len(state['requests'])}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "mock",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                },
                ensure_ascii=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        if not state["stopped"]:
            state["stopped"] = True
            server.shutdown()
            server.server_close()
            thread.join(2)

    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state, stop
    finally:
        stop()


def test_completed_builtin_skill_keeps_its_version_through_crash_and_offline_replay(
    runtime_setup, skill_provider, monkeypatch
):
    factory, settings = runtime_setup
    base_url, provider, stop_provider = skill_provider
    # runtime_setup uses dict-only mocks elsewhere; this case uses the real SDK codec.
    monkeypatch.setattr(model_gateway, "serialize_segment_result", serialize_segment_result)
    gateway = configure_local_gateway(factory, settings, base_url)
    expected = {"name": "script.v1", "version": 1, "instructions": tools.SKILLS["script.v1"]}
    with factory.begin() as session:
        conversation = session.get(AgentConversation, 10)
        run = session.get(AgentRun, 12)
        run.checkpoint = {
            **run.checkpoint,
            "scope": {"project_id": conversation.project_id, "episode_id": conversation.episode_id},
            "user_prompt": "讨论本集剧本规范",
        }

    def plain_prepare(*args):
        return {**tools.prepare_decision(*args), "stream": False}

    executions = []
    original_execute = tools._execute

    def execute_once(session, conversation, run, tool, settings):
        executions.append(tool.id)
        assert len(executions) == 1, "A completed skill must never be executed again"
        return original_execute(session, conversation, run, tool, settings)

    monkeypatch.setattr(tools, "_execute", execute_once)
    original_lock = tools.lock_run
    locks = 0

    def crash_before_next_tool_transaction(*args, **kwargs):
        nonlocal locks
        locks += 1
        if locks == 2:
            # The preceding loop transaction has committed the skill and event;
            # pending_results and the next model scheduling have not happened yet.
            raise RuntimeError("Worker stopped after skill commit")
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(tools, "lock_run", crash_before_next_tool_transaction)
    runtime = AgentRuntime(factory, settings, gateway, plain_prepare)
    with pytest.raises(RuntimeError, match="recovery required"):
        runtime.execute_one(12, 1)
    with factory.begin() as session:
        skill = session.scalars(select(AgentToolCall)).one()
        skill_id = skill.id
        assert skill.tool_name == "read_skill" and skill.status == "succeeded"
        assert skill.result == expected
        assert session.get(AgentRun, 12).phase == "tools"
        finished = session.scalars(
            select(AgentEvent).where(AgentEvent.event_type == "tool.finished")
        ).all()
        assert len(finished) == 1
    assert len(provider["requests"]) == 1

    # Simulate changed builtin text after deployment; recovery must use the stored snapshot.
    changed_instructions = "Changed builtin instructions that must not replace the saved version"
    monkeypatch.setitem(tools.SKILLS, "script.v1", changed_instructions)
    monkeypatch.setattr(tools, "lock_run", original_lock)
    expire(factory)
    assert recover(factory, settings) == 1
    tools_delivery = version(factory)
    runtime.execute_one(12, tools_delivery)
    runtime.execute_one(12, tools_delivery)  # duplicate recovered tools delivery
    with factory.begin() as session:
        run = session.get(AgentRun, 12)
        assert run.phase == "model"
        assert run.checkpoint["pending_results"] == {"calls": {"skill-call-1": expected}}
        assert session.get(AgentToolCall, skill_id).result == expected
    assert executions == [skill_id] and len(provider["requests"]) == 1

    def crash_normalization(_result):
        raise RuntimeError("Worker stopped after continuation raw response commit")

    monkeypatch.setattr(model_gateway, "serialize_segment_result", crash_normalization)
    model_delivery = version(factory)
    with pytest.raises(RuntimeError, match="recovery required"):
        runtime.execute_one(12, model_delivery)
    with factory.begin() as session:
        continuation = session.scalars(select(AgentTurn).where(AgentTurn.turn_no == 2)).one()
        assert continuation.status == "sent" and "normalized" not in continuation.response
        assert continuation.response["raw"]["status_code"] == 200
        assert continuation.request_messages[0]["kwargs"]["deferred_results"] == {
            "calls": {"skill-call-1": expected}
        }
    assert len(provider["requests"]) == 2
    returns = [
        message for message in provider["requests"][1]["messages"] if message["role"] == "tool"
    ]
    assert len(returns) == 1 and returns[0]["tool_call_id"] == "skill-call-1"
    assert json.loads(returns[0]["content"]) == expected

    # Disconnect the local provider and reject any live transport during replay.
    stop_provider()

    def no_live_request(*args, **kwargs):
        raise AssertionError("Recovery must replay the saved raw response without networking")

    monkeypatch.setattr(model_gateway.SafeTransport, "request", no_live_request)
    monkeypatch.setattr(model_gateway, "serialize_segment_result", serialize_segment_result)
    expire(factory)
    assert recover(factory, settings) == 1
    replay_delivery = version(factory)
    runtime.execute_one(12, replay_delivery)
    runtime.execute_one(12, replay_delivery)
    runtime.execute_one(12, model_delivery)
    with factory.begin() as session:
        run = session.get(AgentRun, 12)
        continuation = session.scalars(select(AgentTurn).where(AgentTurn.turn_no == 2)).one()
        assert run.status == "succeeded" and continuation.response["applied"] is True
        assert continuation.response["normalized"]["usage"]["external_requests"] == 0
        assert session.scalars(select(AgentToolCall)).one().result == expected
        for history in (run.checkpoint["history"], continuation.response["normalized"]["history"]):
            skill_returns = [
                part
                for message in deserialize_history(history)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_skill"
            ]
            assert len(skill_returns) == 1
            assert skill_returns[0].tool_call_id == "skill-call-1"
            assert skill_returns[0].content == expected
            assert changed_instructions not in json.dumps(history, ensure_ascii=False)
        events = session.scalars(select(AgentEvent)).all()
        assert len([event for event in events if event.event_type == "tool.finished"]) == 1
        public = json.dumps([event.payload for event in events], ensure_ascii=False)
        assert expected["instructions"] not in public and changed_instructions not in public
        messages = session.scalars(
            select(AgentMessage).where(AgentMessage.role == "assistant")
        ).all()
        assert len(messages) == 1 and messages[0].content == "已参考本集剧本规范。"
    with factory() as session:
        public_conversation = service(session).get_conversation(10).model_dump_json()
        assert expected["instructions"] not in public_conversation
        assert "pydantic-ai.messages" not in public_conversation
    assert executions == [skill_id] and len(provider["requests"]) == 2
