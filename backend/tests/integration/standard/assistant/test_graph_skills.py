"""Real ToolNode commands keep Skill state local to durable execution scopes."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from sqlalchemy import select

from short_drama.agent.runtime.agent import build_agent
from short_drama.agent.runtime.middleware import ContextMiddleware
from short_drama.agent.skill.contracts import ReadSkillResource
from short_drama.agent.skill.runtime import resource_manifest_locked
from short_drama.agent.task.state import apply_inputs_locked
from short_drama.agent.tools.registry import get_spec
from short_drama.core.exceptions import NotFound
from short_drama.core.time import utcnow
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from tests.integration.standard.assistant.test_agent_context_compaction import runtime
from tests.integration.standard.assistant.test_assistant_skills import (
    _Storage,
    create_skill,
    new_skill_task,
)
from tests.integration.standard.assistant.test_business_invocations import scope_operation
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.support.assistant_model import ScriptedModel

pytestmark = pytest.mark.integration


def skill_graph(workspace, accepted, replies, *, required=()):
    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.resources.graph import GraphResources
    from short_drama.agent.runtime.middleware import ExecutionMiddleware, SkillToolsMiddleware
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.agent.tools.langchain import create_langchain_tool
    from tests.support.assistant_runtime import RuntimeTransport

    saver, context = runtime(workspace, accepted)
    with context.factory() as session:
        snapshot = session.get(AgentRun, context.run_id).config_snapshot
    transport = RuntimeTransport(replies)
    resources = GraphResources(context, snapshot, None)
    model = build_chat_model(
        snapshot,
        "",
        callbacks=RunModelLedger(context).callbacks(hydrate=resources.hydrate),
        transport=transport,
    )
    tools = [get_spec(name).handler for name in ("runtime__load_skill", "runtime__release_skill")]
    available = [*tools, *[create_langchain_tool(get_spec(name)) for name in required]]
    graph = build_agent(
        model=model,
        tools=tools,
        middleware=[
            ContextMiddleware(),
            ExecutionMiddleware(resources),
            SkillToolsMiddleware(available),
        ],
        checkpointer=saver,
        system_prompt="按要求加载或释放创作方法。",
    )
    return graph, context, saver, transport, resources


@pytest.mark.parametrize("budget", ["count", "tokens"])
@pytest.mark.parametrize("manual", [False, True])
def test_parallel_skill_admission_uses_merged_active_quota(harness_workspace, budget, manual):
    factory, settings, model_id = harness_workspace
    workspace = factory, settings, model_id, _Storage(factory)
    costs = [10] * 9 if budget == "count" else [6000, 1500, 1500]
    skills = [
        create_skill(
            workspace,
            name=f"方法{index}",
            slug=f"quota-{index}",
            instructions="文" * cost,
            resources=[],
        )
        for index, cost in enumerate(costs)
    ]
    _, accepted = new_skill_task(workspace, skills[-1] if manual else None)

    def call(index):
        return (
            f"load-{index}",
            "runtime__load_skill",
            {
                "name": skills[index].id,
                "content_version": "1",
                "purpose": "配额验证",
            },
        )

    graph, context, saver, transport, resources = skill_graph(
        harness_workspace,
        accepted,
        [
            {"calls": [call(index) for index in range(len(skills) - 2)]},
            {"calls": [call(len(skills) - 2), call(len(skills) - 1)]},
            {"text": "已核对配额"},
        ],
    )
    try:
        result = asyncio.run(
            graph.ainvoke(
                {"messages": []},
                {"configurable": {"thread_id": saver.thread_id}},
                context=context,
            )
        )
    finally:
        resources.close()
    active = [item for item in result["skill_activations"] if item["active"]]
    assert len(active) <= 8
    assert sum(item["body_tokens"] for item in active) <= 8000
    errors = [
        json.loads(message.content)["error"]
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.status == "error"
    ]
    if manual:
        assert any(
            item["skill"]["id"] == skills[-1].id and item["source"] == "manual" for item in active
        )
    else:
        assert len(errors) == 1 and errors[0]["code"] == "assistant_skill_budget"
        details = errors[0]["details"]
        assert details["active_count"] == (8 if budget == "count" else 2)
        assert details["body_tokens"] == (80 if budget == "count" else 7500)
        assert all(
            item["activation_id"] and item["source"] == "auto" and item["body_tokens"] > 0
            for item in details["releasable"]
        )
    assert len(transport.main_requests) == 3


@pytest.mark.parametrize("restriction", ["mode", "policy"])
def test_changed_skill_eligibility_revokes_body_and_derivations_before_model(
    harness_workspace,
    restriction,
):
    from short_drama.domain.assistant.operations.preference import AgentPreference
    from tests.integration.standard.assistant.test_agent_subtasks import new_project_task

    factory, settings, model_id = harness_workspace
    required = "workbench__submit_generation_batch"
    skill = create_skill(
        (factory, settings, model_id, _Storage(factory)),
        modes=["standard"],
        required_tools=[required],
        instructions="REVOKED_SKILL_BODY",
        resources=[],
    )
    accepted, _project_id = new_project_task(harness_workspace)
    graph, context, saver, transport, resources = skill_graph(
        harness_workspace,
        accepted,
        [
            {
                "calls": [
                    (
                        "load",
                        "runtime__load_skill",
                        {
                            "name": skill.id,
                            "content_version": "1",
                            "purpose": "执行创作方法",
                        },
                    )
                ]
            },
            {"text": "已加载"},
            {"text": "已调整方法"},
        ],
        required=[required],
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    try:
        loaded = asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
        assert loaded["skill_activations"][0]["active"]
        with factory.begin() as session:
            if restriction == "mode":
                task = session.get(AgentTask, context.task_id)
                task.execution_target = {**task.execution_target, "mode": "infinite_canvas"}
            else:
                preference = session.scalar(
                    select(AgentPreference).where(
                        AgentPreference.owner_user_id == context.owner_id,
                    )
                )
                if preference is None:
                    from short_drama.utils.snowflake import next_id

                    session.add(
                        AgentPreference(
                            id=next_id(), owner_user_id=context.owner_id, media_generation_enabled=0
                        )
                    )
                else:
                    preference.media_generation_enabled = 0
        result = asyncio.run(
            graph.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content="REVOKED_DERIVED_SUMMARY",
                            additional_kwargs={
                                "skill_activation_ids": [loaded["skill_activations"][0]["id"]],
                                "lc_source": "summarization",
                            },
                        )
                    ]
                },
                config,
                context=context,
            )
        )
    finally:
        resources.close()
    assert not result["skill_activations"][0]["active"]
    assert result["skill_activations"][0]["issue"]
    request = transport.main_requests[-1]
    assert "REVOKED_SKILL_BODY" not in json.dumps(request)
    assert "REVOKED_DERIVED_SUMMARY" not in json.dumps(request)
    assert required not in {tool["function"]["name"] for tool in request["tools"]}
    assert "issue" in json.dumps(request["messages"])


@pytest.mark.parametrize("damage", ["missing", "invalid", "invalid_sibling"])
def test_missing_or_invalid_skill_archive_stops_with_safe_identity(
    harness_workspace,
    caplog,
    damage,
):
    from short_drama.agent.gateway.contracts import AgentGatewayError

    factory, settings, model_id = harness_workspace
    workspace = factory, settings, model_id, _Storage(factory)
    skill = create_skill(workspace, instructions="PRIVATE_BODY_NOT_FOR_DIAGNOSTICS", resources=[])
    sibling = create_skill(
        workspace,
        name="同组方法",
        slug="sibling-method",
        instructions="PRIVATE_SIBLING_BODY",
        resources=[],
    )
    _, accepted = new_skill_task(workspace)
    graph, context, saver, transport, resources = skill_graph(
        harness_workspace,
        accepted,
        [
            {
                "calls": [
                    (
                        "load",
                        "runtime__load_skill",
                        {
                            "name": skill.id,
                            "content_version": "1",
                            "purpose": "归档验证",
                        },
                    ),
                    *(
                        [
                            (
                                "sibling",
                                "runtime__load_skill",
                                {
                                    "name": sibling.id,
                                    "content_version": "1",
                                    "purpose": "同组归档验证",
                                },
                            )
                        ]
                        if damage == "invalid_sibling"
                        else []
                    ),
                ]
            },
            {"text": "加载完成"},
            {"text": "不应继续"},
        ],
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    try:
        loaded = asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
        identifier = next(
            item["body_operation_id"]
            for item in loaded["skill_activations"]
            if item["skill"]["id"] == (sibling.id if damage == "invalid_sibling" else skill.id)
        )
        with factory.begin() as session:
            operation = session.get(AgentOperation, int(identifier))
            operation.result = {
                **operation.result,
                "instructions": "" if damage == "missing" else "PRIVATE_CORRUPTED_BODY",
            }
        asyncio.run(
            graph.aupdate_state(
                config,
                {
                    "messages": [
                        RemoveMessage(id=message.id)
                        for message in loaded["messages"]
                        if isinstance(message, ToolMessage)
                        or isinstance(message, AIMessage)
                        and message.tool_calls
                    ]
                },
            )
        )
        with pytest.raises(AgentGatewayError, match="agent_skill_context_missing"):
            asyncio.run(
                graph.ainvoke({"messages": [HumanMessage(content="继续")]}, config, context=context)
            )
    finally:
        resources.close()
    assert len(transport.main_requests) == 2
    assert f"operation_id={identifier}" in caplog.text
    assert f"scope_id={context.scope_id}" in caplog.text
    assert "PRIVATE_" not in caplog.text


def test_oversized_manual_admission_reports_committed_usage(harness_workspace):
    factory, settings, model_id = harness_workspace
    workspace = factory, settings, model_id, _Storage(factory)
    auto = create_skill(workspace, instructions="文" * 6000, resources=[])
    manual = create_skill(
        workspace, name="超长方法", slug="oversized-method", instructions="文" * 8500, resources=[]
    )
    _, accepted = new_skill_task(workspace, manual)
    replies = [
        {
            "calls": [
                (
                    f"load-{index}",
                    "runtime__load_skill",
                    {
                        "name": skill.id,
                        "content_version": "1",
                        "purpose": "正文容量验证",
                    },
                )
            ]
        }
        for index, skill in enumerate((auto, manual))
    ]
    graph, context, saver, _transport, resources = skill_graph(
        harness_workspace,
        accepted,
        [*replies, {"text": "请先缩小方法正文"}],
    )
    try:
        state = asyncio.run(
            graph.ainvoke(
                {"messages": []},
                {
                    "configurable": {"thread_id": saver.thread_id},
                },
                context=context,
            )
        )
    finally:
        resources.close()
    error = next(
        json.loads(message.content)["error"]
        for message in state["messages"]
        if isinstance(message, ToolMessage) and message.status == "error"
    )
    assert error["details"]["active_count"] == 1
    assert error["details"]["body_tokens"] == 6000
    assert len(error["details"]["releasable"]) == 1
    assert [item["skill"]["id"] for item in state["skill_activations"] if item["active"]] == [
        auto.id
    ]


@pytest.mark.parametrize("manual", [False, True])
def test_parallel_duplicate_load_and_release_preserve_activation_identity(
    harness_workspace, manual
):
    factory, settings, model_id = harness_workspace
    workspace = factory, settings, model_id, _Storage(factory)
    skill = create_skill(workspace, instructions="IDEMPOTENT_BODY", resources=[])
    _, accepted = new_skill_task(workspace, skill if manual else None)
    arguments = {"name": skill.id, "content_version": "1", "purpose": "重复调用验证"}
    activation_id = None

    def releases(_request):
        return {
            "calls": [
                (f"release-{index}", "runtime__release_skill", {"activation_id": activation_id})
                for index in range(2)
            ]
            + [("stale-load", "runtime__load_skill", arguments)]
        }

    graph, context, saver, transport, resources = skill_graph(
        harness_workspace,
        accepted,
        [
            {"calls": [(f"load-{index}", "runtime__load_skill", arguments) for index in range(2)]},
            {"text": "已加载"},
            releases,
            {"text": "已核对释放"},
        ],
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    try:
        loaded = asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
        assert len(loaded["skill_activations"]) == 1
        activation_id = loaded["skill_activations"][0]["id"]
        result = asyncio.run(
            graph.ainvoke({"messages": [HumanMessage(content="核对释放")]}, config, context=context)
        )
    finally:
        resources.close()
    original = next(item for item in result["skill_activations"] if item["id"] == activation_id)
    assert original["active"] is manual
    assert len([item for item in result["skill_activations"] if item["active"]]) <= 1
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation).where(
                AgentOperation.run_id == context.run_id,
            )
        ).all()
    initial = [item for item in operations if item.tool_name == "runtime__load_skill"][:2]
    assert len({item.result["activation_id"] for item in initial}) == 1
    released = [item for item in operations if item.tool_name == "runtime__release_skill"]
    if manual:
        assert all(
            item.status == "failed" and item.error["code"] == "conflict" for item in released
        )
    else:
        assert all(item.status == "succeeded" for item in released)
        assert sorted(item.result["already_applied"] for item in released) == [False, True]
    assert len(transport.main_requests) == 4


def test_skill_admission_rollback_and_retry_keep_durable_receipt_identity(harness_workspace):
    from copy import deepcopy

    from short_drama.agent.tools.runtime import invoke_runtime_tool
    from short_drama.core.exceptions import WorkflowError

    factory, settings, model_id = harness_workspace
    workspace = factory, settings, model_id, _Storage(factory)
    skill = create_skill(workspace, instructions="RECOVERABLE_BODY", resources=[])
    _, accepted = new_skill_task(workspace)
    _, context = runtime(harness_workspace, accepted)
    with factory.begin() as session:
        task = session.get(AgentTask, context.task_id)
        apply_inputs_locked(
            session,
            session.get(AgentConversation, task.conversation_id),
            task,
            session.get(AgentRun, context.run_id),
        )
    arguments = {"name": skill.id, "content_version": "1", "purpose": "回执恢复验证"}
    call = register(context, accepted, "runtime__load_skill", arguments)
    tool = get_spec("runtime__load_skill").handler
    values = tool.input_model.model_validate(arguments)
    initial = {"scope_id": str(context.scope_id), "permitted_tool_names": [], "selected_skills": []}

    def fail_after_load(*args, **kwargs):
        tool.locked_handler(*args, **kwargs)
        raise WorkflowError("fixture_rollback", "事务回滚验证", 409)

    with pytest.raises(WorkflowError, match="事务回滚验证"):
        invoke_runtime_tool(fail_after_load, values, context, call["id"], state=deepcopy(initial))
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == context.run_id)
        )
        assert operation.result is None
    first_state, retry_state = deepcopy(initial), deepcopy(initial)
    first = invoke_runtime_tool(tool.locked_handler, values, context, call["id"], state=first_state)
    retry = invoke_runtime_tool(tool.locked_handler, values, context, call["id"], state=retry_state)
    assert first == retry and first["instructions"] == "RECOVERABLE_BODY"
    assert first_state["skill_activations"] == retry_state["skill_activations"]
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == context.run_id)
        )
        assert operation.scope_id == context.scope_id
        assert first["body_operation_id"] == str(operation.id)
        assert operation.result["activation_record"]["id"] == first["activation_id"]


@pytest.mark.parametrize("restriction", ["mode", "required_tool", None])
def test_explicit_skill_load_rechecks_target_and_scope_tools(harness_workspace, restriction):
    from dataclasses import replace

    from tests.integration.standard.assistant.test_agent_subtasks import new_project_task
    from tests.support.assistant_tools import scope_graph

    factory, settings, model_id = harness_workspace
    workspace = factory, settings, model_id, _Storage(factory)
    skill = create_skill(
        workspace,
        modes=["infinite_canvas"] if restriction == "mode" else ["standard"],
        required_tools=["runtime__describe_creation_target"],
        instructions="核对当前范围中的人物动机。",
        resources=[],
    )
    accepted, _project_id = new_project_task(harness_workspace)
    _saver, context = runtime(harness_workspace, accepted)
    if restriction == "required_tool":
        context = replace(
            context,
            root_tool_names=tuple(
                name
                for name in context.root_tool_names
                if name != "runtime__describe_creation_target"
            ),
        )
    _context, _transport, operations = scope_graph(
        harness_workspace,
        accepted,
        [
            (
                "load-method",
                "runtime__load_skill",
                {
                    "name": skill.id,
                    "content_version": skill.content_version,
                    "purpose": "核对方法适用性",
                },
            )
        ],
        context=context,
    )
    loaded = operations[0]
    assert loaded.arguments["content_version"] == skill.content_version
    if restriction:
        assert loaded.status == "failed" and loaded.error["code"] == "conflict"
        assert not loaded.result or "instructions" not in loaded.result
        assert ("模式" if restriction == "mode" else "必需工具") in loaded.error["message"]
    else:
        assert loaded.status == "succeeded" and loaded.result["instructions"] == skill.instructions
        assert loaded.result["content_version"] == str(skill.content_version)


def test_canonical_skill_group_requires_every_original_call_to_settle(harness_workspace):
    from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
    from short_drama.utils.snowflake import next_id
    from tests.integration.standard.assistant.test_global_assistant_tasks import new_task

    _conversation, accepted = new_task(harness_workspace)
    _saver, context = runtime(harness_workspace, accepted)
    operation_id = scope_operation(
        context.factory, accepted, context.scope_id, "runtime__load_skill"
    )
    with context.factory.begin() as session:
        operation = session.get(AgentOperation, operation_id)
        operation.status, operation.finished_at = "succeeded", utcnow()
        call = session.get(AgentToolCall, operation.tool_call_id)
        session.add(
            AgentToolCall(
                id=next_id(),
                run_id=context.run_id,
                scope_id=context.scope_id,
                turn_id=call.turn_id,
                call_index=2,
                provider_call_id="unfinished-sibling",
                tool_name="runtime__search_skills",
                arguments={},
                arguments_hash="b" * 64,
                idempotency_key="b" * 64,
            )
        )
        session.flush()
        assert (
            AgentTranscriptDAO(session).scope_skill_group(
                context.task_id,
                context.run_id,
                context.scope_id,
                operation_id,
            )
            == []
        )


def register(context, accepted, name, arguments):
    operation_id = scope_operation(context.factory, accepted, context.scope_id, name, arguments)
    with context.factory() as session:
        operation = session.get(AgentOperation, operation_id)
        call = session.get(AgentToolCall, operation.tool_call_id)
        return {"id": call.provider_call_id, "name": name, "args": arguments}


def test_parallel_loads_canonical_restore_and_forgetting_keep_scope_sources(harness_workspace):
    factory, settings, model_id = harness_workspace
    settings.minio_image_bucket = "fixture-graph-skill-text"
    storage = _Storage(factory)
    workspace = factory, settings, model_id, storage
    first = create_skill(workspace)
    second = create_skill(
        workspace, name="对白方法", slug="dialogue-method", instructions="第二份完整正文"
    )
    _, accepted = new_skill_task(workspace, first)
    saver, context = runtime(harness_workspace, accepted)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        run = session.get(AgentRun, accepted.run.id)
        conversation = session.get(AgentConversation, task.conversation_id)
        apply_inputs_locked(session, conversation, task, run)
    calls = [
        register(
            context,
            accepted,
            "runtime__load_skill",
            {
                "name": skill.id,
                "content_version": skill.content_version,
                "purpose": "测试当前讨论方法",
            },
        )
        for skill in (first, second)
    ]
    model = ScriptedModel(
        replies=[AIMessage(content="", tool_calls=calls), AIMessage(content="完成讨论")]
    )
    graph = build_agent(
        model=model,
        tools=[get_spec("runtime__load_skill").handler],
        middleware=[],
        checkpointer=saver,
        system_prompt="讨论",
    )
    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="比较两份方法")],
                "scope_id": str(context.scope_id),
                "applied_input_revision": 1,
                "permitted_tool_names": [],
                "selected_skills": [],
            },
            {"configurable": {"thread_id": saver.thread_id}},
            context=context,
        )
    )
    records = result["skill_activations"]
    assert len(records) == 2 and len({item["id"] for item in records}) == 2
    assert {item["skill"]["id"] for item in records} == {first.id, second.id}
    assert all(item["scope_id"] == str(context.scope_id) for item in records)
    assert all(item["source"] == "auto" for item in records)
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert {message.tool_call_id for message in tool_messages} == {call["id"] for call in calls}
    assert all(json.loads(message.content)["instructions"] for message in tool_messages)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert task.effective_state.get("skill_activations", []) == []
        conversation = session.get(AgentConversation, task.conversation_id)
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        activation = next(item for item in records if item["skill"]["id"] == first.id)
        values = ReadSkillResource(activation_id=activation["id"], path="assets/method.txt")
        manifest = resource_manifest_locked(
            session, conversation, task, operation, values, state=result
        )
        assert manifest["checksum_sha256"] and manifest["path"] == "assets/method.txt"
        with pytest.raises(NotFound):
            resource_manifest_locked(
                session,
                conversation,
                task,
                operation,
                values,
                state={
                    **result,
                    "skill_activations": [
                        {**item, "scope_id": str(context.scope_id + 1)} for item in records
                    ],
                },
            )
        with pytest.raises(NotFound):
            resource_manifest_locked(
                session,
                conversation,
                task,
                operation,
                values,
                state={
                    **result,
                    "skill_activations": [{**item, "active": False} for item in records],
                },
            )
        for message in tool_messages:
            call = session.scalar(
                select(AgentToolCall).where(
                    AgentToolCall.run_id == context.run_id,
                    AgentToolCall.provider_call_id == message.tool_call_id,
                )
            )
            saved = session.scalar(
                select(AgentOperation).where(AgentOperation.tool_call_id == call.id)
            )
            saved.result, saved.status, saved.finished_at = (
                json.loads(message.content),
                "succeeded",
                utcnow(),
            )
    config = {"configurable": {"thread_id": saver.thread_id}}
    restored_model = ScriptedModel(
        replies=[AIMessage(content="已恢复正文"), AIMessage(content="已遗忘")]
    )
    restored_graph = build_agent(
        model=restored_model,
        tools=[],
        middleware=[ContextMiddleware()],
        checkpointer=saver,
        system_prompt="讨论",
    )

    async def restore():
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    RemoveMessage(id=message.id)
                    for message in result["messages"]
                    if isinstance(message, ToolMessage)
                    or isinstance(message, AIMessage)
                    and message.tool_calls
                ]
            },
        )
        return await restored_graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="旧摘要中的完整方法秘密",
                        additional_kwargs={
                            "source_message_ids": [str(accepted.message.id)],
                            "skill_activation_ids": [item["id"] for item in records],
                            "lc_source": "summarization",
                        },
                    )
                ]
            },
            config,
            context=context,
        )

    restored = asyncio.run(restore())
    bodies = [message for message in restored["messages"] if isinstance(message, ToolMessage)]
    assert len(bodies) == 2
    assert all(json.loads(message.content)["instructions"] for message in bodies)
    assert all(message.id.startswith("operation-result:") for message in bodies)
    from tests.integration.standard.assistant.test_assistant_memory import (
        create_sourced_memory,
        memory,
        service,
    )

    with factory() as session:
        manager = service(session)
        remembered = create_sourced_memory(
            manager,
            memory(
                source_conversation_id=accepted.task.conversation_id,
                source_message_ids=[accepted.message.id],
            ),
        )
        manager.forget_item(remembered.id, {"content_version": "1"})
    forgotten = asyncio.run(restored_graph.ainvoke({"messages": []}, config, context=context))
    assert not any(item.get("active") for item in forgotten["skill_activations"])
    assert all("旧摘要中的完整方法秘密" not in message.text for message in forgotten["messages"])
    assert all(not isinstance(message, ToolMessage) for message in forgotten["messages"])
    assert all(
        "旧摘要中的完整方法秘密" not in message.text for message in restored_model.requests[-1]
    )


@pytest.mark.parametrize("withdrawal", ["release", "forget"])
def test_one_withdrawn_skill_in_same_canonical_turn_never_returns(harness_workspace, withdrawal):
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_service

    factory, settings, model_id = harness_workspace
    settings.minio_image_bucket = "fixture-same-turn-skills"
    workspace = factory, settings, model_id, _Storage(factory)
    first = create_skill(workspace, instructions="WITHDRAWN_A_BODY")
    second = create_skill(
        workspace, name="第二方法", slug="second-method", instructions="ACTIVE_B_BODY"
    )
    conversation, accepted = new_skill_task(workspace)
    with factory() as session:
        appended = task_service(session, settings).send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "第二个方法的独立来源"},
            "second-source",
        )
    saver, context = runtime(harness_workspace, accepted)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        apply_inputs_locked(
            session,
            session.get(AgentConversation, conversation.id),
            task,
            session.get(AgentRun, accepted.run.id),
        )
    calls, operations = [], []
    original_turn = None
    for index, skill in enumerate((first, second), 1):
        arguments = {"name": skill.id, "content_version": "1", "purpose": "比较创作方法"}
        identifier = scope_operation(
            factory,
            accepted,
            context.scope_id,
            "runtime__load_skill",
            arguments,
            input_revision=index,
        )
        with factory.begin() as session:
            operation = session.get(AgentOperation, identifier)
            call = session.get(AgentToolCall, operation.tool_call_id)
            if operations:
                call.turn_id, call.call_index = original_turn, index
            original_turn = call.turn_id
            operations.append(identifier)
            calls.append({"id": call.provider_call_id, "name": call.tool_name, "args": arguments})
    graph = build_agent(
        model=ScriptedModel(
            replies=[
                AIMessage(content="", tool_calls=calls),
                AIMessage(content="两项已加载"),
            ]
        ),
        tools=[get_spec("runtime__load_skill").handler],
        middleware=[],
        checkpointer=saver,
        system_prompt="讨论",
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    loaded = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="比较方法")],
                "scope_id": str(context.scope_id),
                "applied_input_revision": 2,
                "selected_skills": [],
                "permitted_tool_names": [],
                "context_initialized": True,
            },
            config,
            context=context,
        )
    )
    records = {item["skill"]["id"]: item for item in loaded["skill_activations"]}
    assert records[first.id]["source_message_id"] == str(accepted.message.id)
    assert records[second.id]["source_message_id"] == str(appended.message.id)
    with factory.begin() as session:
        for message in loaded["messages"]:
            if isinstance(message, ToolMessage):
                call = session.scalar(
                    select(AgentToolCall).where(
                        AgentToolCall.run_id == context.run_id,
                        AgentToolCall.provider_call_id == message.tool_call_id,
                    )
                )
                assert call.turn_id == original_turn
                operation = session.scalar(
                    select(AgentOperation).where(
                        AgentOperation.tool_call_id == call.id,
                    )
                )
                operation.result, operation.status, operation.finished_at = (
                    json.loads(message.content),
                    "succeeded",
                    utcnow(),
                )
    if withdrawal == "release":
        arguments = {"activation_id": records[first.id]["id"]}
        release = register(context, accepted, "runtime__release_skill", arguments)
        replies = [
            AIMessage(content="", tool_calls=[release]),
            AIMessage(content="已释放"),
            AIMessage(content="再次恢复"),
        ]
    else:
        from tests.integration.standard.assistant.test_assistant_memory import (
            create_sourced_memory,
            memory,
            service,
        )

        with factory() as session:
            manager = service(session)
            item = create_sourced_memory(
                manager,
                memory(
                    source_conversation_id=conversation.id, source_message_ids=[accepted.message.id]
                ),
            )
            manager.forget_item(item.id, {"content_version": "1"})
        replies = [AIMessage(content="已遗忘"), AIMessage(content="再次恢复")]
    model = ScriptedModel(replies=replies)
    restored = build_agent(
        model=model,
        tools=[get_spec("runtime__release_skill").handler],
        middleware=[ContextMiddleware()],
        checkpointer=saver,
        system_prompt="讨论",
    )
    state = asyncio.run(restored.ainvoke({"messages": []}, config, context=context))
    state = asyncio.run(restored.ainvoke({"messages": []}, config, context=context))
    assert "WITHDRAWN_A_BODY" not in json.dumps(
        [message.model_dump() for message in model.requests[-1]]
    )
    assert "ACTIVE_B_BODY" in json.dumps([message.model_dump() for message in model.requests[-1]])
    pairs = {
        message.tool_call_id: message
        for message in state["messages"]
        if isinstance(message, ToolMessage)
        and message.tool_call_id in {call["id"] for call in calls}
    }
    assert set(pairs) == {call["id"] for call in calls}
    original = next(
        message
        for message in state["messages"]
        if isinstance(message, AIMessage)
        and {call["id"] for call in message.tool_calls} == set(pairs)
    )
    assert len(original.tool_calls) == 2
    for call, skill in zip(calls, (first, second), strict=True):
        message = pairs[call["id"]]
        assert message.additional_kwargs["skill_activation_ids"] == [records[skill.id]["id"]]
        assert message.additional_kwargs["source_message_ids"] == [
            records[skill.id]["source_message_id"]
        ]
    assert json.loads(pairs[calls[0]["id"]].content)["content_withheld"] is True
    assert json.loads(pairs[calls[1]["id"]].content)["instructions"] == "ACTIVE_B_BODY"
