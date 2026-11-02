"""冻结助手迁移的公开 API 假设；模型、沙箱动作和外部传输使用替身。"""

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx
import pytest
from deepagents.backends import StateBackend
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from tests.support.assistant_model import ScriptedModel


def test_framework_continues_after_each_tool():
    calls = []

    @tool
    def record(value: str) -> str:
        """记录测试值。"""
        calls.append(value)
        return value

    model = ScriptedModel(
        replies=[
            AIMessage(
                content="", tool_calls=[{"name": "record", "args": {"value": "a"}, "id": "a"}]
            ),
            AIMessage(
                content="", tool_calls=[{"name": "record", "args": {"value": "b"}, "id": "b"}]
            ),
            AIMessage(content="完成"),
        ]
    )
    graph = create_agent(model, tools=[record], checkpointer=InMemorySaver())
    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="记录两个值")]},
            {"configurable": {"thread_id": "framework-test"}},
        )
    )
    assert calls == ["a", "b"]
    assert result["messages"][-1].content == "完成"
    assert [message.content for message in model.requests[2] if message.type == "tool"] == [
        "a",
        "b",
    ]


@dataclass
class ScopeContext:
    scope_id: str


def test_dynamic_tool_receives_hidden_runtime_and_model_middleware_is_nested():
    observed = []

    @tool
    def scoped(value: str, runtime: ToolRuntime[ScopeContext]) -> str:
        """在当前范围读取测试值。"""
        observed.append((runtime.context.scope_id, runtime.tool_call_id))
        return f"{runtime.context.scope_id}:{value}"

    events = []

    class Outer(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            events.append("outer-before")
            result = handler(request.override(tools=[scoped]))
            events.append("outer-after")
            return result

        def wrap_tool_call(self, request, handler):
            return handler(request.override(tool=scoped))

    class Inner(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            events.append("inner-before")
            result = handler(request)
            events.append("inner-after")
            return result

    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[{"name": "scoped", "args": {"value": "x"}, "id": "dynamic"}],
            ),
            AIMessage(content="完成"),
        ]
    )
    graph = create_agent(model, middleware=[Outer(), Inner()], context_schema=ScopeContext)
    result = graph.invoke(
        {"messages": [HumanMessage(content="范围内读取")]},
        context=ScopeContext("scope-17"),
    )
    assert set(scoped.tool_call_schema.model_json_schema()["properties"]) == {"value"}
    assert observed == [("scope-17", "dynamic")]
    assert result["messages"][-2].content == "scope-17:x"
    assert events == ["outer-before", "inner-before", "inner-after", "outer-after"] * 2


def test_compiled_children_have_isolated_history_in_parallel_and_next_round():
    ready = asyncio.Event()
    scopes = []

    class ParallelBarrier(AgentMiddleware):
        async def abefore_model(self, state, runtime):
            scopes.append(runtime.context.scope_id)
            if len(scopes) == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)

    child_model = ScriptedModel(replies=[AIMessage(content="子任务完成") for _ in range(4)])
    child = create_agent(child_model, middleware=[ParallelBarrier()], context_schema=ScopeContext)
    parent_model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": scope, "subagent_type": "worker"},
                        "id": scope,
                    }
                    for scope in ("scope-a", "scope-b", "scope-c")
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "scope-d", "subagent_type": "worker"},
                        "id": "scope-d",
                    }
                ],
            ),
            AIMessage(content="全部完成"),
        ]
    )
    graph = create_agent(
        parent_model,
        middleware=[
            SubAgentMiddleware(
                backend=StateBackend(),
                subagents=[{"name": "worker", "description": "测试子任务", "runnable": child}],
            )
        ],
        checkpointer=InMemorySaver(),
        context_schema=ScopeContext,
    )
    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="不要传给子任务的父会话历史")]},
            {"configurable": {"thread_id": "isolated-children"}},
            context=ScopeContext("root"),
        )
    )
    assert sorted([[m.content for m in messages] for messages in child_model.requests]) == [
        ["scope-a"],
        ["scope-b"],
        ["scope-c"],
        ["scope-d"],
    ]
    assert len([message for message in result["messages"] if message.type == "tool"]) == 4
    assert result["messages"][-1].content == "全部完成"
    assert scopes == ["root"] * 4


def test_production_compiled_child_keeps_private_state_isolated_in_both_directions():
    from short_drama.agent.runtime.agent import delegation_middleware
    from short_drama.agent.runtime.context import AssistantState

    observed = []

    class PrivateState(AgentMiddleware):
        state_schema = AssistantState

        async def abefore_model(self, state, runtime):
            observed.append(dict(state))

        async def aafter_model(self, state, runtime):
            return {
                "scope_id": "child-scope",
                "selected_skills": [{"id": "child-skill"}],
                "skill_activations": [{"id": "child-activation", "active": True}],
                "permitted_tool_names": ["execute"],
                "context_initialized": True,
            }

    child_model = ScriptedModel(
        replies=[AIMessage(content="一次交付"), AIMessage(content="再次交付")]
    )
    native = delegation_middleware(
        model=child_model,
        child_tools=[],
        child_middleware=[PrivateState()],
        backend=StateBackend(),
        system_prompt="冻结用户规则",
        task_description="冻结委派工具说明",
    )
    parent_model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": str(index),
                        "name": "task",
                        "args": {
                            "description": f"独立目标{index}",
                            "subagent_type": "workbench-task",
                        },
                    }
                ],
            )
            for index in (1, 2)
        ]
        + [AIMessage(content="汇总")]
    )
    saver = InMemorySaver()
    graph = create_agent(
        parent_model, middleware=[native], state_schema=AssistantState, checkpointer=saver
    )
    initial = {
        "messages": [HumanMessage(content="父历史私密正文")],
        "scope_id": "parent-scope",
        "context_initialized": True,
        "selected_skills": [{"id": "parent-skill"}],
        "skill_activations": [{"id": "parent-activation", "active": True}],
        "permitted_tool_names": ["task"],
    }
    result = asyncio.run(graph.ainvoke(initial, {"configurable": {"thread_id": "private-state"}}))
    for state in observed:
        assert state.get("skill_activations", []) == []
        assert not any(
            key in state for key in initial if key not in {"messages", "skill_activations"}
        )
        assert len(state["messages"]) == 1
        assert "父历史私密正文" not in state["messages"][0].content
    for key, value in initial.items():
        if key != "messages":
            assert result[key] == value
    assert (
        len({item.config["configurable"]["checkpoint_ns"] for item in saver.list(None)} - {""}) == 2
    )


@pytest.mark.parametrize(
    "descriptions",
    [
        ("done-child", "waiting-child-a", "waiting-child-b"),
        ("waiting-child-a", "waiting-child-b", "waiting-child-c"),
    ],
)
def test_nested_interrupt_resumes_fresh_graph_with_original_scope_and_saved_siblings(descriptions):
    active_scope = ContextVar("framework-test-scope", default=None)
    audit = []
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "restart-child"}}

    class ScopeAudit(BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, **kwargs):
            audit.append(("model", active_scope.get(), "callback"))

    def build_worker(parent_model, child_models):
        class ScopeBinding(AgentMiddleware):
            async def awrap_model_call(self, request, handler):
                token = active_scope.set(request.state["messages"][0].content)
                try:
                    audit.append(
                        ("model-binding", active_scope.get(), request.runtime.context.scope_id)
                    )
                    return await handler(request.override(model=child_models[active_scope.get()]))
                finally:
                    active_scope.reset(token)

            async def awrap_tool_call(self, request, handler):
                token = active_scope.set(request.runtime.state["messages"][0].content)
                try:
                    audit.append(
                        ("enter-scope", active_scope.get(), request.runtime.context.scope_id)
                    )
                    return await handler(request)
                finally:
                    active_scope.reset(token)

        @tool
        def finish_child(runtime: ToolRuntime[ScopeContext]) -> str:
            """等待必要用户信息后交付当前范围。"""
            scope = runtime.state["messages"][0].content
            assert active_scope.get() == scope
            if scope.startswith("waiting-child"):
                reply = interrupt({"scope_id": active_scope.get(), "kind": "ask_user"})
                audit.append(("tool", active_scope.get(), reply))
            audit.append(("sandbox", active_scope.get(), "test-only-execution"))
            return f"完成:{scope}"

        child = create_agent(
            next(iter(child_models.values())),
            tools=[finish_child],
            middleware=[ScopeBinding()],
            context_schema=ScopeContext,
        )
        graph = create_agent(
            parent_model,
            middleware=[
                SubAgentMiddleware(
                    backend=StateBackend(),
                    subagents=[{"name": "worker", "description": "测试子任务", "runnable": child}],
                )
            ],
            checkpointer=saver,
            context_schema=ScopeContext,
        )
        return graph

    first_model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": scope, "subagent_type": "worker"},
                        "id": scope,
                    }
                    for scope in descriptions
                ],
            )
        ]
    )

    async def exercise():
        first_child_models = {
            scope: ScriptedModel(
                replies=[
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "finish_child", "args": {}, "id": f"tool-{scope}"}],
                    ),
                    AIMessage(content=f"完成:{scope}"),
                ],
                callbacks=[ScopeAudit()],
            )
            for scope in descriptions
        }
        first = build_worker(first_model, first_child_models)
        events = [
            event
            async for event in first.astream(
                {"messages": [HumanMessage(content="并行委派并等待")]},
                config,
                context=ScopeContext("root-scope"),
                subgraphs=True,
                stream_mode="updates",
            )
        ]
        snapshot = await first.aget_state(config, subgraphs=True)
        checkpoints = [item async for item in saver.alist(None)]
        completed = []
        for pending in snapshot.interrupts:
            before = list(audit)
            resumed = build_worker(
                ScriptedModel(replies=[AIMessage(content="汇总完成")]),
                {
                    scope: ScriptedModel(
                        replies=[AIMessage(content=f"完成:{scope}")], callbacks=[ScopeAudit()]
                    )
                    for scope in descriptions
                },
            )
            result = await resumed.ainvoke(
                Command(resume={pending.id: f"补充:{pending.value['scope_id']}"}),
                config,
                context=ScopeContext("root-scope"),
            )
            for scope in completed:
                assert [entry for entry in audit if entry[1] == scope] == [
                    entry for entry in before if entry[1] == scope
                ]
            completed.append(pending.value["scope_id"])
        return first, events, snapshot, checkpoints, result

    first, events, snapshot, checkpoints, result = asyncio.run(exercise())
    assert all(isinstance(event, tuple) and len(event) == 2 for event in events)
    assert any(namespace for namespace, _ in events)
    interruptions = [value for _, update in events for value in update.get("__interrupt__", ())]
    waiting = [scope for scope in descriptions if scope.startswith("waiting-child")]
    assert sorted({item.value["scope_id"] for item in interruptions}) == waiting
    assert sorted(item.value["scope_id"] for item in snapshot.interrupts) == waiting
    assert len({item.id for item in snapshot.interrupts}) == len(waiting)
    assert snapshot.next == ("tools",) * len(waiting)
    assert list(first.get_subgraphs()) == []
    assert any(item.config["configurable"]["checkpoint_ns"] for item in checkpoints)
    saved_interrupts = {
        saved.id
        for checkpoint in checkpoints
        for _, channel, values in (checkpoint.pending_writes or [])
        if channel == "__interrupt__"
        for saved in values
    }
    assert {item.id for item in snapshot.interrupts} <= saved_interrupts
    for pending in snapshot.interrupts:
        task = next(task for task in snapshot.tasks if pending in task.interrupts)
        assert task.state is None
        namespaces = {
            checkpoint.config["configurable"]["checkpoint_ns"]
            for checkpoint in checkpoints
            for _, channel, values in checkpoint.pending_writes or []
            if channel == "__interrupt__" and any(item.id == pending.id for item in values)
        }
        assert "" in namespaces
        assert f"tools:{task.id}" in namespaces
    for scope in descriptions:
        assert audit.count(("model-binding", scope, "root-scope")) == 2
        assert audit.count(("model", scope, "callback")) == 2
        assert audit.count(("sandbox", scope, "test-only-execution")) == 1
    if "done-child" in descriptions:
        assert audit.count(("enter-scope", "done-child", "root-scope")) == 1
    for scope in waiting:
        assert audit.count(("enter-scope", scope, "root-scope")) >= 2
        assert ("tool", scope, f"补充:{scope}") in audit
    assert active_scope.get() is None
    assert result["messages"][-1].content == "汇总完成"
    outputs = sorted(m.content for m in result["messages"] if m.type == "tool")
    assert outputs == [f"完成:{scope}" for scope in descriptions]


def test_fastmcp_http_mount_runs_lifespan_and_serves_business_tool():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastmcp import Context, FastMCP

    lifecycle = []

    @asynccontextmanager
    async def lifespan(server):
        lifecycle.append("start")
        yield {"workspace": "ready"}
        lifecycle.append("stop")

    mcp = FastMCP("workbench-test", lifespan=lifespan)

    @mcp.tool
    def read_project(project_id: str, ctx: Context) -> str:
        return f"{project_id}:{ctx.lifespan_context['workspace']}"

    mcp_app = mcp.http_app(path="/", json_response=True, stateless_http=True)
    app = FastAPI(lifespan=mcp_app.lifespan)
    app.mount("/mcp", mcp_app)
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app) as client:
        initialized = client.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "framework-test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        headers["MCP-Protocol-Version"] = initialized.json()["result"]["protocolVersion"]
        tools = client.post(
            "/mcp/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ).json()["result"]["tools"]
        assert [entry["name"] for entry in tools] == ["read_project"]
        assert set(tools[0]["inputSchema"]["properties"]) == {"project_id"}
        called = client.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "read_project", "arguments": {"project_id": "17"}},
            },
        )
        assert called.status_code == 200
        assert called.json()["result"]["content"] == [{"type": "text", "text": "17:ready"}]
        assert lifecycle == ["start"]
    assert lifecycle == ["start", "stop"]


def test_opensandbox_adapter_binds_existing_sdk_and_preserves_workspace_wire_contract():
    from langchain_opensandbox import OpenSandboxBackend
    from opensandbox import SandboxSync
    from opensandbox.config import ConnectionConfigSync
    from opensandbox.models import PVC, Volume

    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.content))
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            return httpx.Response(
                202,
                json={
                    "id": "sandbox-17",
                    "status": {"state": "Running"},
                    "createdAt": "2026-10-29T00:00:00Z",
                    "entrypoint": ["tail", "-f", "/dev/null"],
                    "metadata": {},
                    "expiresAt": "2026-10-29T00:10:00Z",
                },
            )
        if request.method == "GET" and "/endpoints/" in request.url.path:
            return httpx.Response(200, json={"endpoint": "execd.test:44772", "headers": {}})
        if request.method == "POST" and request.url.path == "/command":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text=(
                    'data: {"type":"stdout","text":"workspace-result","timestamp":1}\n\n'
                    'data: {"type":"execution_complete","execution_time":1,"timestamp":2}\n\n'
                ),
            )
        if request.method == "DELETE" and request.url.path == "/v1/sandboxes/sandbox-17":
            return httpx.Response(204)
        raise AssertionError(f"未定义外部传输: {request.method} {request.url}")

    transport = httpx.MockTransport(respond)
    connection = ConnectionConfigSync(
        domain="sandbox.test", transport=transport, disable_metrics=True
    )
    volume = Volume(name="workspace", pvc=PVC(claim_name="scope-17"), mount_path="/workspace")
    original = SandboxSync.create(
        "python:3.12", volumes=[volume], connection_config=connection, skip_health_check=True
    )
    backend = OpenSandboxBackend(sandbox=original, timeout=45)
    assert backend.id == "sandbox-17"
    executed = backend.execute("cat /workspace/result", timeout=7)
    assert (executed.output, executed.exit_code, executed.truncated) == (
        "workspace-result",
        0,
        False,
    )
    original.close()
    assert not [entry for entry in requests if entry[0] == "DELETE"]
    restored = SandboxSync.connect(
        "sandbox-17", connection_config=connection, skip_health_check=True
    )
    restored_backend = OpenSandboxBackend(sandbox=restored)
    assert (
        asyncio.run(restored_backend.aexecute("cat /workspace/result")).output == "workspace-result"
    )
    restored.kill()
    restored.close()
    recreated = SandboxSync.create(
        "python:3.12", volumes=[volume], connection_config=connection, skip_health_check=True
    )
    recreated.close()
    creates = [json.loads(body) for method, path, body in requests if path == "/v1/sandboxes"]
    assert [body["volumes"] for body in creates] == [
        [
            {
                "name": "workspace",
                "pvc": {
                    "claimName": "scope-17",
                    "createIfNotExists": True,
                    "deleteOnSandboxTermination": False,
                    "storageClass": None,
                    "storage": None,
                    "accessModes": None,
                },
                "mountPath": "/workspace",
                "readOnly": False,
            }
        ]
    ] * 2
    command = next(json.loads(body) for method, path, body in requests if path == "/command")
    assert command["command"] == "cat /workspace/result"
    assert command["timeout"] == 7000
    assert len([entry for entry in requests if entry[0] == "DELETE"]) == 1
    transport.close()
