"""The model and MCP consume the same concrete, uniquely named tool definitions."""

import asyncio
from dataclasses import dataclass, fields
from importlib import import_module

import pytest

from short_drama.core.identity import ActorContext


def test_business_identity_does_not_require_a_browser_session():
    assert {field.name for field in fields(ActorContext)} == {
        "user_id",
        "username",
        "email",
        "verified",
        "request_id",
    }
    identity = import_module("short_drama.core.identity")
    browser = identity.SessionActorContext(
        user_id=1,
        username="creator",
        email="creator@example.test",
        verified=True,
        request_id="browser",
        session_id=3,
        csrf_hash="hash",
    )
    assert isinstance(browser, ActorContext)


def test_catalog_has_one_spec_per_retained_capability_and_explicit_mcp_boundary():
    registry = import_module("short_drama.agent.tools.registry")
    assert hasattr(registry, "TOOL_SPECS"), (
        "The single SPEC catalog has not replaced duplicate tables"
    )
    specs = registry.TOOL_SPECS
    assert len(specs) == 45
    assert len({spec.name for spec in specs}) == len(specs)
    assert {spec.name for spec in specs if spec.expose_mcp} == {
        "workbench__read_current_script",
        "workbench__apply_standard_patch",
        "workbench__apply_canvas_patch",
        "workbench__submit_generation_batch",
        "workbench__get_generation_status",
        "workbench__read_image_group",
        "workbench__read_image_content",
        "workbench__set_image_group_primary",
        "workbench__list_projects",
        "workbench__read_project",
        "workbench__read_assets",
        "workbench__read_storyboard",
    }
    forbidden = {"run_id", "scope_id", "user_id", "request_message_id", "request_hash", "runtime"}
    for spec in specs:
        schema = (
            spec.args_schema
            if isinstance(spec.args_schema, dict)
            else spec.args_schema.model_json_schema()
        )
        assert not forbidden.intersection(schema.get("properties", {})), spec.name
        assert not spec.expose_mcp or spec.source == "business"
    with pytest.raises(ValueError, match="重复|Duplicate"):
        registry.build_catalog([specs[0], specs[0]])
    assert registry.get_spec("workbench__read_image_content").modes == frozenset(
        {"standard", "infinite_canvas"}
    )


def test_mcp_directory_reads_cannot_expand_assistant_or_frozen_skill_tool_pool():
    from types import SimpleNamespace

    from short_drama.agent.runtime.context import RuntimeContext
    from short_drama.agent.tools.registry import available_specs, get_spec
    from short_drama.service.skills.packages.skill_package import platform_tool_issues

    external = {
        "workbench__list_projects",
        "workbench__read_project",
        "workbench__read_assets",
        "workbench__read_storyboard",
    }
    for target in (
        None,
        {"project_id": "1", "mode": "standard"},
        {"project_id": "1", "mode": "infinite_canvas"},
    ):
        specs = available_specs(SimpleNamespace(execution_target=target), media_enabled=True)
        assert not external.intersection(spec.name for spec in specs)
        if target is None:
            assert all(spec.source == "runtime" for spec in specs)
    context = RuntimeContext(
        owner_id=1,
        task_id=2,
        run_id=3,
        root_scope_id=3,
        fence=(1, "lease"),
        factory=None,
        settings=None,
        root_tool_lookup=get_spec,
        scopes=None,
        root_tool_names=("runtime__load_skill",),
    )
    for name in external:
        assert context.tool_lookup(name) is None
        assert platform_tool_issues([name], context.tool_lookup)


def test_framework_tool_schema_hides_runtime_injection():
    registry = import_module("short_drama.agent.tools.registry")
    assert hasattr(registry, "get_spec"), "A ToolSpec lookup is required"
    adapter = import_module("short_drama.agent.tools.langchain")
    spec = registry.get_spec("workbench__apply_standard_patch")
    tool = adapter.create_langchain_tool(spec)
    assert tool.name == spec.name
    assert tool.description == spec.description
    assert "runtime" not in tool.tool_call_schema["properties"]
    assert tool.args_schema == spec.args_schema.model_json_schema()
    assert tool.args_schema["additionalProperties"] is False


@pytest.mark.parametrize("name", ["workbench__apply_standard_patch", "runtime__ask_user"])
def test_frozen_description_does_not_change_the_catalog_tool(name):
    from langchain_core.tools import BaseTool

    from short_drama.agent.tools.langchain import create_langchain_tool
    from short_drama.agent.tools.registry import get_spec

    spec = get_spec(name)
    tool = create_langchain_tool(spec, description="该Run冻结的工具描述")
    assert tool.description == "该Run冻结的工具描述"
    assert spec.description != tool.description
    if isinstance(spec.handler, BaseTool):
        assert tool is not spec.handler
        assert spec.handler.description == spec.description


def test_actual_tool_node_injects_context_and_call_id(monkeypatch):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    from short_drama.agent.tools import langchain as adapter
    from short_drama.agent.tools.registry import get_spec

    observed = []

    @dataclass
    class Context:
        owner_id: int

    context = Context(owner_id=7)

    def invoke(spec, values, context, tool_call_id):
        observed.append((spec.name, values, context, tool_call_id))
        return {"saved": True}

    monkeypatch.setattr(adapter, "_invoke_business_tool", invoke)
    spec = get_spec("workbench__apply_standard_patch")
    tool = adapter.create_langchain_tool(spec)
    builder = StateGraph(MessagesState, context_schema=Context)
    builder.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    arguments = {
        "project_id": "4",
        "patch": {
            "actions": [
                {
                    "action": "create_asset",
                    "key": "hero",
                    "values": {"name": "主角", "kind": "character"},
                }
            ]
        },
    }
    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": spec.name, "args": arguments, "id": "provider-call-7"}
                        ],
                    )
                ]
            },
            context=context,
        )
    )
    assert observed, result["messages"][-1].content
    assert observed[0][0] == spec.name
    assert observed[0][1].project_id == 4
    assert observed[0][2] is context
    assert observed[0][3] == "provider-call-7"
    assert '"saved": true' in result["messages"][-1].content


def test_runtime_specs_bind_concrete_tools_and_skill_validation_uses_injected_catalog():
    from deepagents.middleware.subagents import SubAgentMiddleware, TaskToolSchema
    from langchain_core.tools import BaseTool

    from short_drama.agent.tools.registry import TOOL_SPECS, get_spec
    from short_drama.service.skills.packages.skill_package import (
        mode_tool_issues,
        platform_tool_issues,
    )

    runtime = [spec for spec in TOOL_SPECS if spec.source == "runtime"]
    assert len(runtime) == 33
    from short_drama.agent.sandbox.native import FILESYSTEM

    native = {tool.name: tool for tool in FILESYSTEM.tools}
    resources = {
        "runtime__import_resources",
        "runtime__export_artifacts",
        "runtime__materialize_skill_resource",
    }
    for spec in runtime:
        if spec.name == "task":
            assert spec.handler is SubAgentMiddleware and spec.args_schema is TaskToolSchema
            assert not spec.expose_mcp and spec.effect == "delegate"
            continue
        assert isinstance(spec.handler, BaseTool)
        assert spec.name == spec.handler.name
        assert spec.description
        schema = (
            spec.args_schema
            if isinstance(spec.args_schema, dict)
            else (spec.args_schema.model_json_schema())
        )
        if spec.name in native:
            assert spec.handler is native[spec.name]
        elif spec.name in {"runtime__ask_user", "runtime__wait_generation"} | resources:
            assert spec.handler.args_schema == spec.args_schema.model_json_schema()
        else:
            assert spec.description == spec.handler.description
            assert callable(spec.handler.locked_handler)
            assert spec.args_schema is spec.handler.input_model
        assert "runtime" not in schema.get("properties", {})
    assert not platform_tool_issues(["workbench__apply_standard_patch"], get_spec)
    assert platform_tool_issues(["confirm_requirements"], get_spec)
    assert platform_tool_issues(["task"], lambda _name: None)
    assert mode_tool_issues(["workbench__apply_standard_patch"], ["infinite_canvas"], get_spec)


@pytest.mark.parametrize("source", ["business", "runtime"])
def test_actual_tool_node_uses_one_strict_input_model_with_defaults_and_aliases(
    monkeypatch, source
):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.prebuilt.tool_node import ToolInvocationError
    from pydantic import Field, ValidationError

    from short_drama.agent.tools import langchain as business_adapter
    from short_drama.agent.tools import runtime as runtime_adapter
    from short_drama.agent.tools.contracts import ToolSpec
    from short_drama.schemas.base import InputModel

    class Values(InputModel):
        target_id: int = Field(alias="target")
        limit: int = 7

    observed = []
    context = {"scope": "trusted"}

    def capture(values, context, call_id):
        observed.append((values, context, call_id))
        return {"limit": values.limit}

    if source == "business":
        monkeypatch.setattr(
            business_adapter, "_invoke_business_tool", lambda spec, *args: capture(*args)
        )
        spec = ToolSpec("probe", "公开参数契约", Values, lambda *args: None)
        tool = business_adapter.create_langchain_tool(spec)
    else:

        def capture_runtime(handler, values, context, call_id, *, state):
            assert state is None
            return capture(values, context, call_id)

        monkeypatch.setattr(runtime_adapter, "invoke_runtime_tool", capture_runtime)
        tool = runtime_adapter.create_runtime_tool(
            "probe", "公开参数契约", Values, lambda *args: None
        )
    builder = StateGraph(MessagesState, context_schema=dict)
    builder.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    async def invoke(arguments, call_id):
        return await graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="", tool_calls=[{"name": "probe", "args": arguments, "id": call_id}]
                    )
                ]
            },
            context=context,
        )

    asyncio.run(invoke({"target": "9"}, "alias-default"))
    assert isinstance(observed[0][0], Values)
    assert observed[0][0].target_id == 9 and observed[0][0].limit == 7
    assert observed[0][1] is context and observed[0][2] == "alias-default"
    with pytest.raises(ToolInvocationError) as error:
        asyncio.run(invoke({"target": "9", "owner_id": 99}, "untrusted-owner"))
    assert isinstance(error.value.__cause__, ValidationError)
    assert error.value.__cause__.errors()[0]["type"] == "extra_forbidden"
    assert len(observed) == 1


def test_readonly_workbench_keeps_analysis_and_account_memory():
    from types import SimpleNamespace

    from short_drama.agent.tools.registry import available_specs

    task = SimpleNamespace(execution_target={"mode": "standard"})
    names = {
        spec.name
        for spec in available_specs(
            task, media_enabled=True, workbench_enabled=False, memory_enabled=True
        )
    }
    assert "workbench__read_current_script" in names
    assert "runtime__remember_memory" in names
    assert "workbench__apply_standard_patch" not in names
    assert "runtime__propose_script_patch" not in names
    assert "workbench__submit_generation_batch" not in names


def test_disabled_memory_does_not_remove_conversation_history():
    from types import SimpleNamespace

    from short_drama.agent.tools.registry import available_specs

    task = SimpleNamespace(execution_target={"mode": "standard"})
    names = {spec.name for spec in available_specs(task, media_enabled=True, memory_enabled=False)}
    assert "runtime__read_conversation_history" in names
    assert "runtime__read_memory" not in names
    assert "runtime__remember_memory" not in names
