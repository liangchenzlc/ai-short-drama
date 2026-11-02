"""Real compiled agents and MySQL checkpoints with controlled models and HTTP only."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from short_drama.domain.assistant.tasks.task import AgentTask
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    new_task,
)
from tests.integration.standard.assistant.test_graph_checkpointer import bound, saver_for
from tests.support.assistant_model import ScriptedModel

pytestmark = pytest.mark.integration


def runtime(workspace, accepted):
    from short_drama.agent.runtime.context import RuntimeContext
    from short_drama.agent.tools.registry import TOOL_SPECS, get_spec
    from short_drama.service.assistant.execution.scope import AgentExecutionScopeService

    factory, settings, _model = workspace
    service = bound(workspace, accepted)
    scopes = AgentExecutionScopeService(service)
    root = scopes.root()
    return saver_for(service), RuntimeContext(
        owner_id=1,
        task_id=accepted.task.id,
        run_id=accepted.run.id,
        root_scope_id=root.id,
        factory=factory,
        settings=settings,
        root_tool_lookup=get_spec,
        root_tool_names=tuple(spec.name for spec in TOOL_SPECS),
        fence=service.fence,
        scopes=scopes,
    )


def selector_for(context):
    from langchain.agents.middleware import LLMToolSelectorMiddleware

    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.agent.tools.langchain import create_langchain_tool
    from short_drama.agent.tools.registry import get_spec
    from short_drama.domain.assistant.execution.models import AgentRun
    from short_drama.service.ai_config.management.assistant_models import model_credentials
    from tests.support.assistant_runtime import RuntimeTransport

    transport = RuntimeTransport([])
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        model = build_chat_model(
            run.config_snapshot,
            model_credentials(run.config_snapshot, context.settings.encryption_key),
            callbacks=RunModelLedger(context).callbacks(
                max_output_tokens=run.budget["output_tokens_per_call"]
            ),
            transport=transport,
        )
    return (
        [create_langchain_tool(get_spec("runtime__get_generation_policy"))],
        LLMToolSelectorMiddleware(model=model, max_tools=16),
        transport,
    )


def test_resume_consumes_only_new_input_ids_and_does_not_advance_product_early(harness_workspace):
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import ContextMiddleware

    _conversation, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    model = ScriptedModel(replies=[AIMessage(content="首次讨论"), AIMessage(content="恢复讨论")])
    graph = build_agent(
        model=model,
        tools=[],
        middleware=[ContextMiddleware()],
        checkpointer=saver,
        system_prompt="讨论",
    )
    config = {"configurable": {"thread_id": saver.thread_id}}

    async def invoke():
        first = await graph.ainvoke({"messages": []}, config, context=context)
        assert first["applied_input_revision"] == 1
        with context.factory() as session:
            task = session.get(AgentTask, accepted.task.id)
            assert task.applied_input_revision == 0
        rebuilt = build_agent(
            model=model,
            tools=[],
            middleware=[ContextMiddleware()],
            checkpointer=saver,
            system_prompt="讨论",
        )
        second = await rebuilt.ainvoke({"messages": []}, config, context=context)
        input_ids = [
            message.id for message in second["messages"] if message.id.startswith("task-input:")
        ]
        assert len(input_ids) == len(set(input_ids)) == 1
        assert sum(message.text == "讨论镜头节奏" for message in model.requests[-1]) == 1
        return second

    result = asyncio.run(invoke())
    assert result["applied_input_revision"] == 1


def test_two_framework_summaries_keep_complete_active_skill_pairs(harness_workspace):
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware

    _conversation, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    main = ScriptedModel(replies=[AIMessage(content="已讨论"), AIMessage(content="继续讨论")])
    summary = ScriptedModel(
        replies=[AIMessage(content="第一次来源摘要"), AIMessage(content="第二次来源摘要")]
    )
    graph = build_agent(
        model=main,
        tools=[],
        middleware=[
            SkillSummarizationMiddleware(
                model=summary,
                trigger=("messages", 6),
                keep=("messages", 2),
                trim_tokens_to_summarize=None,
            )
        ],
        checkpointer=saver,
        system_prompt="讨论",
    )
    pair = [
        AIMessage(
            content="",
            id="skill-call",
            tool_calls=[
                {
                    "id": "skill-tool",
                    "name": "runtime__load_skill",
                    "args": {"name": "方法", "content_version": "1"},
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "activation_id": "41",
                    "body_operation_id": "42",
                    "instructions": "完整活动方法正文",
                }
            ),
            id="skill-body",
            tool_call_id="skill-tool",
            name="runtime__load_skill",
        ),
    ]
    skill = {
        "id": "41",
        "body_operation_id": "42",
        "active": True,
        "scope_id": str(context.scope_id),
    }
    config = {"configurable": {"thread_id": saver.thread_id}}

    async def invoke():
        seed = [HumanMessage(content=f"原始来源{i}", id=f"source:{i}") for i in range(8)]
        first = await graph.ainvoke(
            {"messages": [*pair, *seed], "skill_activations": [skill]}, config, context=context
        )
        second = await graph.ainvoke(
            {"messages": [HumanMessage(content=f"后续{i}") for i in range(8)]},
            config,
            context=context,
        )
        for state in (first, second):
            body = next(message for message in state["messages"] if message.id == "skill-body")
            call = next(message for message in state["messages"] if message.id == "skill-call")
            assert json.loads(body.content)["instructions"] == "完整活动方法正文"
            assert call.tool_calls[0]["id"] == body.tool_call_id

    asyncio.run(invoke())
    assert len(summary.requests) == 2
    assert len(main.requests) == 2


def test_summary_does_not_resurrect_removed_skill_on_saved_graph(harness_workspace):
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import ContextMiddleware

    _conversation, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    model = ScriptedModel(replies=[AIMessage(content="继续")])
    graph = build_agent(
        model=model,
        tools=[],
        middleware=[ContextMiddleware()],
        checkpointer=saver,
        system_prompt="讨论",
    )
    state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="不得复活的方法秘密",
                        additional_kwargs={
                            "lc_source": "summarization",
                            "skill_activation_ids": ["41"],
                        },
                    )
                ],
                "skill_activations": [{"id": "41", "active": False}],
            },
            {"configurable": {"thread_id": saver.thread_id}},
            context=context,
        )
    )
    assert all("不得复活的方法秘密" not in message.text for message in state["messages"])
    assert all("不得复活的方法秘密" not in message.text for message in model.requests[-1])


def test_new_run_keeps_one_ordered_copy_of_each_task_input_and_selection(harness_workspace):
    from short_drama.agent.core.items import allocate_item_seq
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import ContextMiddleware
    from short_drama.agent.task.state import apply_inputs_locked
    from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
    from short_drama.domain.assistant.execution.models import AgentRun
    from short_drama.utils.snowflake import next_id
    from tests.integration.standard.assistant.test_assistant_skills import (
        _Storage,
        create_skill,
        new_skill_task,
    )
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_service

    factory, settings, model_id = harness_workspace
    settings.minio_image_bucket = "fixture-new-run-context"
    workspace = factory, settings, model_id, _Storage(factory)
    skill = create_skill(workspace)
    conversation, accepted = new_skill_task(workspace, skill)
    with factory.begin() as session:
        current = session.get(AgentConversation, conversation.id)
        task = session.get(AgentTask, accepted.task.id)
        apply_inputs_locked(session, current, task, session.get(AgentRun, accepted.run.id))
        session.add(
            AgentMessage(
                id=next_id(),
                conversation_id=current.id,
                task_id=task.id,
                seq=current.next_message_seq,
                item_seq=allocate_item_seq(current),
                role="assistant",
                content="先前已保存的答复",
                references=[],
                artifacts=[],
            )
        )
        current.next_message_seq += 1
    with factory() as session:
        manager = task_service(session, settings)
        manager.control_task(accepted.task.id, {"kind": "stop"}, "stop-for-new-run")
        again = manager.send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "继续讨论，不再使用原方法", "skills": []},
            "new-run-input",
        )
        assert again.task.id == accepted.task.id and again.run.id != accepted.run.id
    saver, context = runtime(harness_workspace, again)
    model = ScriptedModel(replies=[AIMessage(content="新Run答复"), AIMessage(content="恢复答复")])
    graph = build_agent(
        model=model,
        tools=[],
        middleware=[ContextMiddleware()],
        checkpointer=saver,
        system_prompt="讨论",
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    first = asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
    resumed = asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
    expected = [accepted.message.content, "先前已保存的答复", "继续讨论，不再使用原方法"]
    for request in model.requests:
        actual = [message.text for message in request if message.text in expected]
        assert actual == expected
    assert first["selected_skills"] == resumed["selected_skills"] == []
    assert skill.id in resumed["excluded_skill_ids"]
    assert first["applied_input_revision"] == resumed["applied_input_revision"] == 2
    for state in (first, resumed):
        inputs = [message for message in state["messages"] if message.id.startswith("task-input:")]
        assert len(inputs) == 2 and len({message.id for message in inputs}) == 2
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert task.applied_input_revision == 1
        assert task.selected_skills[0]["id"] == skill.id


@pytest.mark.parametrize("summarize", [False, True], ids=["without-summary", "with-summary"])
def test_framework_summaries_keep_bounded_original_goal_and_latest_correction(
    harness_workspace, summarize
):
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import ContextMiddleware, SkillSummarizationMiddleware
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_service

    factory, settings, model_id = harness_workspace
    original = "主角不能杀人。背景是城市。" + "最初背景解释。" * 1500
    latest = "最新更正：反派改为母亲，保留原来的不杀人原则。"
    with factory() as session:
        manager = task_service(session, settings)
        conversation = manager.create_global_conversation({}, "source-compaction")
        accepted = manager.send_task_message(
            conversation.id, {"content": original, "model_config_id": model_id}, "source-goal"
        )
        for index in range(36):
            manager.send_task_message(
                conversation.id,
                {"task_id": accepted.task.id, "content": f"补充{index}：" + "背景说明。" * 20},
                f"source-supplement-{index}",
            )
        manager.send_task_message(
            conversation.id, {"task_id": accepted.task.id, "content": latest}, "source-correction"
        )
    saver, context = runtime(harness_workspace, accepted)
    tools, selector, transport = selector_for(context)
    main = ScriptedModel(replies=[AIMessage(content="第一次讨论"), AIMessage(content="第二次讨论")])
    summary = ScriptedModel(
        replies=[AIMessage(content="未复述原约束的摘要"), AIMessage(content="第二次摘要")]
    )
    graph = build_agent(
        model=main,
        tools=tools,
        middleware=[
            ContextMiddleware(),
            SkillSummarizationMiddleware(
                model=summary,
                trigger=("messages", 6 if summarize else 100),
                keep=("messages", 2),
                trim_tokens_to_summarize=None,
            ),
            selector,
        ],
        checkpointer=saver,
        system_prompt="按原文和最新更正继续",
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
    asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=f"中间推导{index}", id=f"source-filler:{index}")
                    for index in range(8)
                ],
            },
            config,
            context=context,
        )
    )
    assert len(summary.requests) == (2 if summarize else 0)
    for request in main.requests:
        message = next(
            item
            for item in request
            if item.id and item.id.startswith(f"task-sources:{accepted.task.id}")
        )
        sources = json.loads(message.text)["task_user_sources"]
        assert request[-1].id == message.id and isinstance(request[-1], HumanMessage)
        assert sources["items"][0]["id"] == str(accepted.message.id)
        assert sources["items"][0]["truncated"]
        assert original.startswith(sources["items"][0]["content"])
        assert sources["items"][-1]["content"] == latest
        assert sources["omitted_messages"] > 0
        assert sources["estimated_tokens"] <= sources["quota_tokens"] == 4000
    assert len(transport.selection_requests) == 2
    for body in transport.selection_requests:
        query = json.dumps(body["messages"], ensure_ascii=False)
        assert "主角不能杀人。背景是城市。" in query and latest in query
        assert "task_working_notes" not in query


@pytest.mark.parametrize("withdrawal", [None, "selection", "forget", "revocation"])
def test_working_notes_survive_summaries_only_while_source_and_scope_are_eligible(
    harness_workspace,
    withdrawal,
):
    from short_drama.agent.context.graph import scope_context
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.context import execution_scope
    from short_drama.agent.runtime.middleware import ContextMiddleware, SkillSummarizationMiddleware
    from short_drama.core.exceptions import NotFound
    from short_drama.domain.assistant.conversations.models import AgentConversation
    from short_drama.domain.assistant.tasks.task import AgentOperation
    from short_drama.schemas.assistant.memory.memory import MemoryCreate
    from short_drama.service.assistant.memory.memory import create_memory_locked, source_rows
    from short_drama.service.assistant.memory.memory_store import forget_memory_locked
    from tests.integration.standard.assistant.test_business_invocations import scope_operation
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        subject_assets,
        task_service,
    )
    from tests.support.assistant_tools import invoke_tool_locked, scope_graph

    factory, settings, model_id = harness_workspace
    reference = subject_assets(harness_workspace)[0]
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "working-note-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "核对选入人物并保留有来源的工作笔记",
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
            },
            "working-note-input",
        )
    context, _transport, operations = scope_graph(
        harness_workspace,
        accepted,
        [
            ("read-source", "runtime__read_selected_subjects", {}),
        ],
    )
    source = operations[0]
    assert source.status == "succeeded", source.error
    arguments = {
        "operation_id": str(source.id),
        "source_quote": source.result["subjects"][0]["content"][:80],
        "note": "PRIVATE_WORKING_NOTE_人物动机已核对",
    }
    _context, _transport, recorded = scope_graph(
        harness_workspace,
        accepted,
        [
            ("record-observation", "runtime__record_task_observation", arguments),
        ],
        context=context,
    )
    assert recorded[0].status == "succeeded", recorded[0].error
    parent_id = scope_operation(factory, accepted, context.scope_id, "task")
    with factory() as session:
        parent_call = session.get(AgentOperation, parent_id).tool_call_id
    child_scope = context.scopes.child(context.scope_id, parent_call)
    token = execution_scope.set(child_scope.id)
    try:
        child_view = scope_context(context, {"messages": []}, "notes-child")
        assert arguments["note"] not in str(child_view)
        child_record = scope_operation(
            factory, accepted, child_scope.id, "runtime__record_task_observation", arguments
        )
        with factory.begin() as session, pytest.raises(NotFound):
            invoke_tool_locked(
                session,
                session.get(AgentConversation, conversation.id),
                session.get(AgentTask, accepted.task.id),
                session.get(AgentOperation, child_record),
                settings=settings,
            )
    finally:
        execution_scope.reset(token)
    saver = saver_for(context.scopes.graph)
    tools, selector, transport = selector_for(context)
    main = ScriptedModel(replies=[AIMessage(content="第一次核对"), AIMessage(content="第二次核对")])
    summary = ScriptedModel(
        replies=[
            AIMessage(content=f"摘要保留观察：{arguments['note']}"),
            AIMessage(content="摘要二"),
        ]
    )
    graph = build_agent(
        model=main,
        tools=tools,
        middleware=[
            ContextMiddleware(),
            SkillSummarizationMiddleware(
                model=summary,
                trigger=("messages", 6),
                keep=("messages", 2),
                trim_tokens_to_summarize=None,
            ),
            selector,
        ],
        checkpointer=saver,
        system_prompt="使用仍合格的工作笔记",
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    for index in range(2):
        if index and withdrawal == "selection":
            with factory() as session:
                task_service(session, settings).send_task_message(
                    conversation.id,
                    {
                        "task_id": accepted.task.id,
                        "content": "移除人物引用，继续讨论",
                        "subject_selection": {"expected_input_revision": "1", "subjects": []},
                    },
                    "withdraw-note-selection",
                )
        if index and withdrawal == "forget":
            with factory.begin() as session:
                memory = create_memory_locked(
                    session,
                    1,
                    MemoryCreate(
                        scope_type="conversation",
                        conversation_id=conversation.id,
                        kind="decision",
                        memory_key="working-note-source",
                        title="撤回来源",
                        content="已选人物动机",
                    ),
                    sources=source_rows(session, 1, conversation.id, [accepted.message.id]),
                )
                forget_memory_locked(session, memory)
        if index and withdrawal == "revocation":
            from short_drama.core.time import utcnow
            from short_drama.domain import Project

            with factory.begin() as session:
                session.get(Project, int(reference["project_id"])).archived_at = utcnow()
        asyncio.run(
            graph.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=f"中间步骤{index}-{item}", id=f"working-filler:{index}:{item}"
                        )
                        for item in range(8)
                    ]
                },
                config,
                context=context,
            )
        )
        note_message = next(
            message
            for message in main.requests[-1]
            if message.id == f"task-notes:{accepted.task.id}:{context.scope_id}"
        )
        notes = json.loads(note_message.text)["task_working_notes"]
        assert bool(notes["items"]) is (not index or withdrawal is None)
        if notes["items"]:
            assert notes["items"][0]["note"] == arguments["note"]
            assert notes["items"][0]["operation_id"] == str(source.id)
        else:
            assert arguments["note"] not in str(main.requests[-1])
        query = json.dumps(transport.selection_requests[-1]["messages"], ensure_ascii=False)
        assert arguments["note"] not in query
        if withdrawal == "forget" and index:
            assert "核对选入人物并保留有来源的工作笔记" not in query
        else:
            assert "核对选入人物并保留有来源的工作笔记" in query
    assert len(summary.requests) == (1 if withdrawal == "forget" else 2)
    assert all(
        not message.id.startswith("task-notes:")
        for request in summary.requests
        for message in request
        if message.id
    )


@pytest.mark.parametrize("withdrawal", ["release", "manual"])
def test_working_note_and_its_summary_follow_real_skill_eligibility(
    harness_workspace,
    monkeypatch,
    withdrawal,
):
    import short_drama.agent.skill.runtime as skill_runtime
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import ContextMiddleware, SkillSummarizationMiddleware
    from short_drama.agent.tools.registry import get_spec
    from short_drama.service.skills.catalog.skill import AgentSkillService
    from tests.integration.standard.assistant.test_assistant_skills import (
        _Storage,
        create_skill,
        new_skill_task,
    )
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_service
    from tests.support.assistant_tools import scope_graph

    factory, settings, model_id = harness_workspace
    settings.minio_image_bucket = "fixture-working-note-skill"
    storage = _Storage(factory)
    workspace = factory, settings, model_id, storage
    monkeypatch.setattr(
        skill_runtime,
        "read_resource_bytes",
        lambda configured, manifest: AgentSkillService(
            None, configured, storage, tool_lookup=get_spec
        )._resource_bytes(manifest),
    )
    skill = create_skill(workspace)
    conversation, accepted = new_skill_task(workspace, skill if withdrawal == "manual" else None)
    context, _transport, loaded = scope_graph(
        harness_workspace,
        accepted,
        [
            (
                "load-note-method",
                "runtime__load_skill",
                {
                    "name": skill.id,
                    "content_version": skill.content_version,
                    "purpose": "核对人物目标",
                },
            ),
        ],
    )
    activation_id = loaded[0].result["activation_id"]
    _, _, reads = scope_graph(
        harness_workspace,
        accepted,
        [
            (
                "read-note-method",
                "runtime__read_skill_resource",
                {
                    "activation_id": activation_id,
                    "path": "assets/method.txt",
                },
            ),
        ],
        context=context,
    )
    source = reads[0]
    assert source.status == "succeeded", source.error
    note = "PRIVATE_SKILL_WORKING_NOTE_依据方法核对完成"
    _, _, recorded = scope_graph(
        harness_workspace,
        accepted,
        [
            (
                "note-from-method",
                "runtime__record_task_observation",
                {
                    "operation_id": str(source.id),
                    "source_quote": source.result["content"],
                    "note": note,
                },
            ),
        ],
        context=context,
    )
    assert recorded[0].status == "succeeded", recorded[0].error
    saver = saver_for(context.scopes.graph)
    model = ScriptedModel(
        replies=[AIMessage(content="方法观察已核对"), AIMessage(content="已撤回方法")]
    )
    summary = ScriptedModel(replies=[AIMessage(content=f"摘要含有方法观察：{note}")])
    graph = build_agent(
        model=model,
        tools=[],
        middleware=[
            ContextMiddleware(),
            SkillSummarizationMiddleware(
                model=summary,
                trigger=("messages", 6),
                keep=("messages", 2),
                trim_tokens_to_summarize=None,
            ),
        ],
        checkpointer=saver,
        system_prompt="核对仍然有效的方法和观察",
    )
    config = {"configurable": {"thread_id": saver.thread_id}}
    asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=f"方法推导{index}", id=f"skill-note-filler:{index}")
                    for index in range(8)
                ]
            },
            config,
            context=context,
        )
    )
    assert len(summary.requests) == 1
    assert note in str(model.requests[-1])
    if withdrawal == "release":
        _, _, released = scope_graph(
            harness_workspace,
            accepted,
            [
                ("release-note-method", "runtime__release_skill", {"activation_id": activation_id}),
            ],
            context=context,
        )
        assert released[0].status == "succeeded", released[0].error
    else:
        with factory() as session:
            task_service(session, settings).send_task_message(
                conversation.id,
                {
                    "task_id": accepted.task.id,
                    "content": "移除手选方法后继续",
                    "skills": [],
                },
                "withdraw-manual-note-method",
            )
    asyncio.run(graph.ainvoke({"messages": []}, config, context=context))
    notes = next(
        message
        for message in model.requests[-1]
        if message.id == f"task-notes:{accepted.task.id}:{context.scope_id}"
    )
    assert json.loads(notes.text)["task_working_notes"]["items"] == []
    assert note not in str(model.requests[-1])
    assert source.result["content"] not in str(model.requests[-1])
