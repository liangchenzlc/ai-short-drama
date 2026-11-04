"""Native task delegation through the production graph, ledger and disposable MySQL."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select

from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from tests.integration.standard.assets.test_asset_image_generation import flow as flow
from tests.integration.standard.assistant.test_agent_runtime import runtime_for
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    task_service,
)
from tests.support.assistant_runtime import RuntimeTransport, calls

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def delegation(name, handoff=None):
    description = "子任务:" + name + "；核对生成策略并交付摘要。"
    if handoff is not None:
        description += "\n```workbench-inputs\n" + json.dumps(handoff) + "\n```"
    return (name, "task", {"description": description, "subagent_type": "workbench-task"})


def child_name(body):
    for message in body["messages"]:
        content = message.get("content", "")
        if message["role"] == "user" and isinstance(content, str) and content.startswith("子任务:"):
            return content.split("；", 1)[0].removeprefix("子任务:")
    return None


class SubtaskTransport(RuntimeTransport):
    def __init__(self, names, *, child_reply=None, parent_reply=None):
        self.names, self.child_reply, self.parent_reply = names, child_reply, parent_reply
        self.parent_rounds = 0
        super().__init__([self.route] * 80)

    def request(self, method, url, *, body, on_send, **kwargs):
        if (
            not body.get("tools")
            and body.get("max_tokens", body.get("max_completion_tokens")) == 64
        ):
            title = RuntimeTransport([{"text": "子任务验收"}])
            result = title.request(method, url, body=body, on_send=on_send, **kwargs)
            self.requests.extend(title.requests)
            return result
        return super().request(method, url, body=body, on_send=on_send, **kwargs)

    def route(self, body):
        name = child_name(body)
        if name is not None:
            if self.child_reply:
                return self.child_reply(name, body)
            if any(
                message["role"] == "tool" and message.get("tool_call_id") == "policy"
                for message in body["messages"]
            ):
                return {"text": "完成:" + name}
            return calls(("policy", "runtime__get_generation_policy", {}))
        self.parent_rounds += 1
        if self.parent_reply:
            return self.parent_reply(body, self.parent_rounds)
        return (
            calls(*(delegation(name) for name in self.names))
            if self.parent_rounds == 1
            else {"text": "已整合子任务成果"}
        )


def test_no_delegation_does_not_create_child_scope(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    runtime_for(factory, settings, RuntimeTransport([{"text": "直接完成讨论"}])).execute(
        accepted.run.id, 1
    )
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == accepted.run.id)
        ).all()
        assert len(scopes) == 1 and scopes[0].scope_key == "root"


def test_frozen_root_tool_pool_does_not_gain_native_task(task_workspace):
    from short_drama.agent.tools.registry import get_spec

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.checkpoint = {
            **run.checkpoint,
            "permitted_tool_names": ["runtime__get_generation_policy"],
        }
        run.config_snapshot = {
            **run.config_snapshot,
            "assistant_settings": {
                **run.config_snapshot["assistant_settings"],
                "tool_descriptions": {
                    "runtime__get_generation_policy": get_spec(
                        "runtime__get_generation_policy"
                    ).description
                },
            },
        }
    transport = RuntimeTransport(
        [calls(("policy", "runtime__get_generation_policy", {})), {"text": "冻结工具池已核对"}]
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    for body in transport.main_requests:
        assert {item["function"]["name"] for item in body["tools"]} == {
            "runtime__get_generation_policy"
        }


def test_native_task_returns_to_parent_with_isolated_history_and_shared_ledger(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.config_snapshot = {
            **run.config_snapshot,
            "assistant_settings": {
                **run.config_snapshot["assistant_settings"],
                "system_prompt": run.config_snapshot["assistant_settings"]["system_prompt"]
                + "\n冻结指令：所有交付保留雨声。",
            },
        }
    transport = SubtaskTransport(["one"])
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == run.id)
        ).all()
        assert len(scopes) == 2
        child = next(scope for scope in scopes if scope.parent_scope_id is not None)
        assert child.scope_key == f"tool:{child.parent_tool_call_id}"
        assert len(session.scalars(select(AgentConversation)).all()) == 1
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        child_selections = [
            turn.request_messages[0]["body"]["messages"]
            for turn in turns
            if turn.scope_id == child.id and turn.usage.get("source") == "tool_selection"
        ]
        assert len(child_selections) == 2
        assert all(
            "子任务:one" in json.dumps(query, ensure_ascii=False) for query in child_selections
        )
        assert all(
            "讨论镜头节奏" not in json.dumps(query, ensure_ascii=False)
            for query in child_selections
        )
        assert {turn.scope_id for turn in turns} == {child.id, child.parent_scope_id}
        assert run.usage["decision_calls"] == len(transport.requests) == len(turns)
        assert run.usage["output_tokens"] == 3 * len(turns)
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert len(operations) == 2 and all(row.status == "succeeded" for row in operations)
        task = next(row for row in operations if row.tool_name == "task")
        assert "完成:one" in task.result["content"]
    children = [body for body in transport.main_requests if child_name(body)]
    assert len(children) == 2
    for body in children:
        assert "冻结指令：所有交付保留雨声。" in body["messages"][0]["content"]
        assert "讨论镜头节奏" not in json.dumps(body["messages"], ensure_ascii=False)
        assert all(item["function"]["name"] != "task" for item in body["tools"])
    assert transport.parent_rounds == 2
    assert "完成:one" in json.dumps(transport.main_requests[-1]["messages"], ensure_ascii=False)


@pytest.mark.parametrize("forget", [False, True])
def test_child_selector_keeps_scoped_goal_through_summaries_and_withholds_forgotten_origin(
    task_workspace, monkeypatch, forget
):
    from short_drama.agent.runtime import executor
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from short_drama.schemas.assistant.memory.memory import MemoryCreate
    from short_drama.service.assistant.memory.memory import create_memory_locked, source_rows
    from short_drama.service.assistant.memory.memory_store import forget_memory_locked

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original = executor.assistant_middleware

    def small_context(**kwargs):
        middleware = original(**kwargs)
        middleware[1] = SkillSummarizationMiddleware(
            model=kwargs["model"],
            trigger=("messages", 4),
            keep=("messages", 2),
            trim_tokens_to_summarize=None,
        )
        return middleware

    monkeypatch.setattr(executor, "assistant_middleware", small_context)
    rounds = {"parent": 0, "child": 0}
    goal = "子任务:SCOPED_GOAL_雨声；核对策略，独立交付。"

    def route(body):
        if not body.get("tools"):
            return {"text": "受控摘要未复述委派目标"}
        parent = any(item["function"]["name"] == "task" for item in body["tools"])
        key = "parent" if parent else "child"
        rounds[key] += 1
        if parent:
            return (
                calls(("child", "task", {"description": goal, "subagent_type": "workbench-task"}))
                if rounds[key] == 1
                else {"text": "父任务整合完成"}
            )
        if forget and rounds[key] == 3:
            with factory.begin() as session:
                memory = create_memory_locked(
                    session,
                    1,
                    MemoryCreate(
                        kind="decision",
                        memory_key="child-goal-origin",
                        title="撤回委派来源",
                        content="讨论镜头节奏",
                    ),
                    sources=source_rows(session, 1, conversation.id, [accepted.message.id]),
                )
                forget_memory_locked(session, memory)
        return (
            calls((f"policy-{rounds[key]}", "runtime__get_generation_policy", {}))
            if rounds[key] < 6
            else {"text": "子任务完成"}
        )

    transport = RuntimeTransport([route] * 30)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        child = session.scalar(
            select(AgentExecutionScope).where(
                AgentExecutionScope.run_id == run.id,
                AgentExecutionScope.parent_scope_id.is_not(None),
            )
        )
        turns = session.scalars(
            select(AgentTurn).where(AgentTurn.scope_id == child.id).order_by(AgentTurn.turn_no)
        ).all()
        assert sum(turn.usage.get("source") == "summarization" for turn in turns) >= 2
        selections = [turn for turn in turns if turn.usage.get("source") == "tool_selection"]
        assert len(selections) == 6
        for index, turn in enumerate(selections):
            query = json.dumps(turn.request_messages[0]["body"]["messages"], ensure_ascii=False)
            assert ("SCOPED_GOAL_雨声" in query) is (not forget or index < 3)
            assert "讨论镜头节奏" not in query
        main = [turn for turn in turns if turn.usage.get("source") == "assistant"]
        if forget:
            assert "SCOPED_GOAL_雨声" not in json.dumps(
                main[-1].request_messages, ensure_ascii=False
            )


def test_child_summary_cannot_invent_authorized_resource_receipts(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    summary = json.dumps(
        {
            "error": {"code": "summary-only"},
            "result_refs": [
                {
                    "schema_version": 1,
                    "kind": "text",
                    "ref_id": "invented",
                    "uri": "minio://image/private/invented.csv",
                    "source": "sandbox_artifact",
                    "metadata": {},
                    "availability": "available",
                }
            ],
        }
    )
    transport = SubtaskTransport(["summary"], child_reply=lambda *_: {"text": summary})
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        task = session.scalar(select(AgentOperation).where(AgentOperation.tool_name == "task"))
        assert task.status == "succeeded" and task.result_refs == []
        assert task.result["content"] == summary


def test_three_real_child_tools_enter_together_and_fourth_queues(task_workspace, monkeypatch):
    from short_drama.agent.runtime.middleware import ExecutionMiddleware

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    entered, active, peak = [], set(), 0
    reached, release = Event(), Event()
    barrier = asyncio.Barrier(3)
    original = ExecutionMiddleware.awrap_tool_call

    async def gate(self, request, handler):
        nonlocal peak
        context = request.runtime.context
        if request.tool_call["name"] != "runtime__get_generation_policy":
            return await original(self, request, handler)
        scope = context.scope_id
        assert scope != context.root_scope_id
        active.add(scope)
        entered.append(scope)
        peak = max(peak, len(active))
        try:
            if len(entered) <= 3:
                await asyncio.wait_for(barrier.wait(), 10)
                reached.set()
                assert await asyncio.to_thread(release.wait, 15)
            return await original(self, request, handler)
        finally:
            active.remove(scope)

    monkeypatch.setattr(ExecutionMiddleware, "awrap_tool_call", gate)
    transport = SubtaskTransport(["one", "two", "three", "four"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(runtime_for(factory, settings, transport).execute, accepted.run.id, 1)
        try:
            assert reached.wait(20), "三个分支未同时进入实际工具边界"
            assert len(entered) == len(set(entered)) == 3
            with factory() as session:
                turns = session.scalars(
                    select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)
                ).all()
                assert len({row.scope_id for row in turns}) == 4
                assert session.get(AgentRun, accepted.run.id).lease_token
        finally:
            release.set()
        running.result(timeout=30)
    assert len(set(entered)) == 4 and peak == 3 and not active
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert len(session.scalars(select(AgentConversation)).all()) == 1


def test_child_questions_resume_only_answered_branch_without_repeating_siblings(task_workspace):
    from tests.integration.standard.assistant.test_agent_questions import (
        QUESTION,
        answer,
        execute_next,
    )

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace

    def reply(name, body):
        if name == "done" or body["messages"][-1]["role"] == "tool":
            return {"text": "完成:" + name}
        return calls(("question", "runtime__ask_user", QUESTION))

    first = SubtaskTransport(["done", "waiting-a", "waiting-b"], child_reply=reply)
    runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input" and run.lease_token is None, run.error
        questions = session.scalars(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id, AgentOperation.wait_state.is_not(None)
            )
        ).all()
        assert len(questions) == 2
        assert len({row.wait_state["interrupt_id"] for row in questions}) == 2
        assert len({row.wait_state["namespace"] for row in questions}) == 2
        assert all(
            row.scope_id != row.wait_state.get("parent_scope_id") and row.wait_state["namespace"]
            for row in questions
        )
        identifiers = [row.id for row in questions]
        scopes = {row.id: row.scope_id for row in questions}
        parent_operations = session.scalars(
            select(AgentOperation).where(AgentOperation.tool_name == "task")
        ).all()
        assert sum(row.status == "succeeded" for row in parent_operations) == 1
        assert sum(row.status == "waiting_input" for row in parent_operations) == 2
        stable_scopes = {
            row.id: row.scope_key for row in session.scalars(select(AgentExecutionScope))
        }
    for index, identifier in enumerate(identifiers):
        answer(factory, settings, identifier, key=f"answer-{index}")
        next_transport = SubtaskTransport(
            [], child_reply=reply, parent_reply=lambda *_: {"text": "全部完成"}
        )
        execute_next(task_workspace, accepted, next_transport)
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.status == ("waiting_input" if index == 0 else "succeeded"), run.error
            assert {
                row.id: row.scope_key for row in session.scalars(select(AgentExecutionScope))
            } == stable_scopes
            assert session.get(AgentOperation, identifier).wait_state["consumed"]
            assert session.get(AgentOperation, identifier).scope_id == scopes[identifier]
        child_requests = [
            child_name(body) for body in next_transport.main_requests if child_name(body)
        ]
        assert len(child_requests) == 1 and child_requests[0] != "done"
    with factory() as session:
        assert len(session.scalars(select(AgentConversation)).all()) == 1
        assert all(row.status == "succeeded" for row in session.scalars(select(AgentToolCall)))


@pytest.mark.parametrize(
    "handoff",
    [
        {"generation_ids": ["42"]},
        {"subjects": ["subject:guessed"]},
        {
            "resources": [
                {
                    "kind": "file",
                    "ref_id": "guessed",
                    "uri": "minio://test/fake",
                    "source": "sandbox_artifact",
                }
            ]
        },
        {"unknown": "field"},
    ],
)
def test_native_task_rejects_unauthorized_or_malformed_handoff(task_workspace, handoff):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport([calls(delegation("invalid", handoff)), {"text": "交接未获授权"}])
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert len(session.scalars(select(AgentExecutionScope)).all()) == 1
        operation = session.scalar(select(AgentOperation))
        assert operation.status == "failed" and operation.error


def test_child_cannot_read_unhanded_parent_selected_subject(task_workspace):
    accepted, project_id = new_project_task(task_workspace)
    factory, settings, _ = task_workspace
    created = []

    def parent(body, number):
        if number == 1:
            return calls(
                (
                    "create",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": project_id,
                        "patch": {
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "hero",
                                    "values": {"kind": "character", "name": "父私有人物"},
                                }
                            ]
                        },
                    },
                )
            )
        if number == 2:
            reference = json.loads(body["messages"][-1]["content"])["result_refs"][0]
            created.append(reference["id"])
            return calls(delegation("reader"))
        return {"text": "完成授权核对"}

    def child(_name, body):
        if body["messages"][-1]["role"] == "tool":
            return {"text": "核对完毕"}
        return calls(("describe", "runtime__describe_creation_target", {}))

    transport = SubtaskTransport([], child_reply=child, parent_reply=parent)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        result = session.scalar(
            select(AgentOperation.result).where(
                AgentOperation.tool_name == "runtime__describe_creation_target"
            )
        )
        assert result["allowed_assets"] == []


def new_project_task(workspace):
    from uuid import uuid4

    from short_drama.service.projects.management.project import ProjectService

    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        project = ProjectService(session).create_project({"name": "子任务授权"})
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "分工核对角色",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": str(project.id)},
            },
            uuid4().hex,
        )
        return accepted, str(project.id)


def test_child_model_admission_exhausts_the_parent_run_budget(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.budget = {**run.budget, "decision_calls": 3}
    transport = SubtaskTransport(["budget"])
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "failed" and run.error["code"] == "agent_budget_exhausted"
        assert run.usage["decision_calls"] == len(transport.requests) == 3
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert len({row.scope_id for row in turns}) == 2
        assert run.usage["reserved_output_tokens"] == 0


def create_asset_call(project_id, label="角色", *, call_id="create"):
    return calls(
        (
            call_id,
            "workbench__apply_standard_patch",
            {
                "project_id": project_id,
                "patch": {
                    "actions": [
                        {
                            "action": "create_asset",
                            "key": "hero",
                            "values": {"kind": "character", "name": label},
                        }
                    ]
                },
            },
        )
    )


def submit_asset_call(flow, body, factory):
    from short_drama.domain import Asset
    from tests.support.assistant_runtime import tool_result

    asset_id = tool_result(body, "create")["result_refs"][0]["id"]
    with factory() as session:
        version = str(session.get(Asset, int(asset_id)).row_version)
    return calls(
        (
            "submit",
            "workbench__submit_generation_batch",
            {
                "project_id": str(flow.project.id),
                "requests": [
                    {
                        "target_kind": "asset",
                        "target_id": asset_id,
                        "row_version": version,
                        "model_config_id": str(flow.config.id),
                        "model_version": str(flow.config.row_version),
                        "prompt": "子任务数量边界",
                        "count": 1,
                    }
                ],
            },
        )
    )


@pytest.mark.parametrize("parallel_children", [False, True])
def test_scopes_keep_native_admission_receipts_without_a_user_media_cap(flow, parallel_children):
    from short_drama.domain import AsyncTask
    from short_drama.domain.assistant.operations.media import AgentMediaRequest
    from tests.integration.standard.assistant.test_generation_waiting import media_task

    workspace, accepted = media_task(flow)
    factory, settings, _ = workspace
    child_rounds = {}

    def parent(body, number):
        if parallel_children:
            return (
                calls(delegation("media-one"), delegation("media-two"))
                if number == 1
                else {"text": "受理数量已核对"}
            )
        if number == 1:
            return create_asset_call(str(flow.project.id), "父生成")
        if number == 2:
            return submit_asset_call(flow, body, factory)
        return calls(delegation("media")) if number == 3 else {"text": "受理数量已核对"}

    def child(name, body):
        child_rounds[name] = child_rounds.get(name, 0) + 1
        if child_rounds[name] == 1:
            return create_asset_call(str(flow.project.id), "子生成")
        if child_rounds[name] == 2:
            return submit_asset_call(flow, body, factory)
        return {"text": "核对子任务受理"}

    transport = SubtaskTransport([], child_reply=child, parent_reply=parent)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert len(session.scalars(select(AsyncTask)).all()) == 2
        assert not session.scalars(select(AgentMediaRequest)).all()
        submissions = session.scalars(
            select(AgentOperation).where(
                AgentOperation.tool_name == "workbench__submit_generation_batch"
            )
        ).all()
        assert len(submissions) == 2 and all(row.status == "succeeded" for row in submissions)
        assert sum(item["count"] for row in submissions for item in row.result["items"]) == 2


def test_mixed_child_waits_restore_frozen_handoff_and_do_not_rerun_completed_siblings(flow):
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.domain import AsyncTask
    from tests.integration.standard.assets.test_asset_image_generation import drain
    from tests.integration.standard.assistant.test_agent_questions import (
        QUESTION,
        answer,
        execute_next,
    )
    from tests.integration.standard.assistant.test_generation_waiting import media_task
    from tests.support.assistant_runtime import tool_result

    workspace, accepted = media_task(flow)
    factory, settings, _ = workspace
    generation, subjects = [], []

    def parent(body, number):
        if number == 1:
            return create_asset_call(str(flow.project.id))
        if number == 2:
            subjects.append("asset:" + tool_result(body, "create")["result_refs"][0]["id"])
            return submit_asset_call(flow, body, factory)
        if number == 3:
            generation.append(tool_result(body, "submit")["items"][0]["generation_id"])
            return calls(
                delegation("done"),
                delegation("asker"),
                delegation("waiter", {"generation_ids": generation, "subjects": subjects}),
            )
        return {"text": "已核对全部子任务"}

    def child(name, body):
        if name == "done" or body["messages"][-1]["role"] == "tool":
            return {"text": "完成:" + name}
        if name == "asker":
            return calls(("question", "runtime__ask_user", QUESTION))
        return calls(("wait", "runtime__wait_generation", {"task_ids": generation}))

    first = SubtaskTransport([], child_reply=child, parent_reply=parent)
    runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input" and run.lease_token is None, run.error
        question = session.scalar(
            select(AgentOperation).where(AgentOperation.tool_name == "runtime__ask_user")
        )
        waiting = session.scalar(
            select(AgentOperation).where(AgentOperation.tool_name == "runtime__wait_generation")
        )
        assert waiting.wait_state, waiting.error
        assert waiting.wait_state["kind"] == "generation" and waiting.wait_state["namespace"]
        assert question.wait_state["kind"] == "user_input"
        question_id, waiting_id = question.id, waiting.id
        delegate = session.scalar(
            select(AgentOperation).where(
                AgentOperation.tool_name == "task",
                AgentOperation.arguments["description"].as_string().like("子任务:waiter%"),
            )
        )
        frozen = delegate.result["delegation_inputs"]
        delegate_id = delegate.id
    answer(factory, settings, question_id)
    second = SubtaskTransport([], child_reply=child, parent_reply=parent)
    execute_next(workspace, accepted, second)
    assert [child_name(body) for body in second.main_requests] == ["asker"]
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_generation" and run.lease_token is None
        assert session.get(AgentOperation, delegate_id).result["delegation_inputs"] == frozen
    assert drain(flow, int(generation[0])) == "succeeded"
    assert collect_media_results(factory, settings) == 1
    third = SubtaskTransport(
        [], child_reply=child, parent_reply=lambda *_: {"text": "已整合生成成果"}
    )
    execute_next(workspace, accepted, third)
    assert [child_name(body) for body in third.main_requests] == ["waiter", None]
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert session.get(AgentOperation, waiting_id).wait_state["consumed"]
        assert session.get(AgentOperation, delegate_id).result["delegation_inputs"] == frozen
        assert len(session.scalars(select(AsyncTask)).all()) == 1
        assert len(session.scalars(select(AgentExecutionScope)).all()) == 4
    assert len(flow.provider.calls) == 1


def test_child_skill_cannot_reintroduce_task_from_root_tool_catalog(task_workspace):
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill

    factory, settings, model = task_workspace
    skill = create_skill(
        (factory, settings, model, _Storage(factory)),
        required_tools=["task"],
        instructions="父专用委派方法正文",
        resources=[],
    )
    _, accepted = new_task(task_workspace)
    load = calls(
        (
            "load",
            "runtime__load_skill",
            {
                "name": skill.name,
                "content_version": str(skill.content_version),
                "purpose": "核对子任务工具边界",
            },
        )
    )

    def parent(_body, number):
        return (
            load
            if number == 1
            else calls(delegation("skill"))
            if number == 2
            else {"text": "完成方法核对"}
        )

    def child(_name, body):
        return (
            {"text": "子任务缺少委派能力"}
            if any(message["role"] == "tool" for message in body["messages"])
            else load
        )

    transport = SubtaskTransport([], child_reply=child, parent_reply=parent)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    children = [body for body in transport.main_requests if child_name(body)]
    assert children and "父专用委派方法正文" not in json.dumps(children, ensure_ascii=False)
    assert all(item["function"]["name"] != "task" for body in children for item in body["tools"])
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        loads = session.scalars(
            select(AgentOperation).where(AgentOperation.tool_name == "runtime__load_skill")
        ).all()
        assert sorted(row.status for row in loads) == ["failed", "succeeded"]
        failed = next(row for row in loads if row.status == "failed")
        assert "task" in failed.error["message"]


def test_explicit_subject_handoff_limits_ancestor_receipts_and_frozen_reads(task_workspace):
    from tests.support.assistant_runtime import tool_result

    accepted, project_id = new_project_task(task_workspace)
    factory, settings, _ = task_workspace
    selected, receipts = [], []

    def parent(body, number):
        if number <= 2:
            return create_asset_call(
                project_id, "已交接" if number == 1 else "未交接", call_id=f"create-{number}"
            )
        if number == 3:
            with factory() as session:
                rows = session.scalars(
                    select(AgentOperation)
                    .where(AgentOperation.tool_name == "workbench__apply_standard_patch")
                    .order_by(AgentOperation.id)
                ).all()
                selected.append(rows[0].result_refs[0]["id"])
                receipts.extend(row.id for row in rows)
            return calls(delegation("explicit", {"subjects": ["asset:" + selected[0]]}))
        return {"text": "已完成输入交接核对"}

    def child(_name, body):
        if any(message["role"] == "tool" for message in body["messages"]):
            assert tool_result(body, "describe")["allowed_assets"] == [
                {"id": selected[0], "row_version": "1"}
            ]
            assert "未交接" not in json.dumps(tool_result(body, "selected"), ensure_ascii=False)
            assert tool_result(body, "permitted")["receipt"]["id"] == str(receipts[0])
            assert tool_result(body, "forbidden").get("error")
            return {"text": "已读取唯一交接角色"}
        return calls(
            ("describe", "runtime__describe_creation_target", {}),
            ("selected", "runtime__read_selected_subjects", {}),
            ("permitted", "runtime__read_operation_receipt", {"operation_id": str(receipts[0])}),
            ("forbidden", "runtime__read_operation_receipt", {"operation_id": str(receipts[1])}),
        )

    transport = SubtaskTransport([], child_reply=child, parent_reply=parent)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"


def test_stop_cancels_running_child_and_all_persisted_child_questions(task_workspace, monkeypatch):
    from uuid import uuid4

    from langchain.tools import ToolRuntime

    from short_drama.agent.runtime import executor
    from short_drama.agent.session.purge import purge_one
    from short_drama.agent.tools.media_generation.get_generation_policy import TOOL
    from short_drama.core.exceptions import BusinessError
    from short_drama.service.assistant.execution.graph import AgentGraphService
    from tests.integration.standard.assistant.test_agent_questions import QUESTION, answer

    factory, settings, model = task_workspace
    conversation, accepted = new_task((factory, settings, model))
    entered, cancelled, persisted = Event(), Event(), Event()
    questions = set()
    original = AgentGraphService.put_writes
    heartbeat = executor.run_with_lease

    async def quick_heartbeat(coroutine, **kwargs):
        return await heartbeat(coroutine, **{**kwargs, "interval": 0.1})

    async def blocked(runtime: ToolRuntime):
        assert runtime.context.scope_id != runtime.context.root_scope_id
        entered.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 25)
        finally:
            cancelled.set()

    def record(self, **kwargs):
        result = original(self, **kwargs)
        questions.update(
            item["operation_id"]
            for item in kwargs.get("interrupts", ())
            if item["kind"] == "user_input"
        )
        if len(questions) == 2:
            persisted.set()
        return result

    def child(name, _body):
        return (
            calls(("policy", "runtime__get_generation_policy", {}))
            if name == "runner"
            else calls(("question", "runtime__ask_user", QUESTION))
        )

    monkeypatch.setattr(TOOL, "coroutine", blocked)
    monkeypatch.setattr(executor, "run_with_lease", quick_heartbeat)
    monkeypatch.setattr(AgentGraphService, "put_writes", record)
    transport = SubtaskTransport(["runner", "ask-one", "ask-two"], child_reply=child)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(runtime_for(factory, settings, transport).execute, accepted.run.id, 1)
        assert entered.wait(20) and persisted.wait(20), "运行与持久问题分支没有同时进入"
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.status == "running" and run.lease_token
            rows = session.scalars(
                select(AgentOperation).where(AgentOperation.id.in_(questions))
            ).all()
            assert len(rows) == 2
            assert all(row.wait_state["interrupt_id"] for row in rows)
        with factory() as session:
            task_service(session, settings).control_task(
                accepted.task.id, {"kind": "stop"}, uuid4().hex
            )
        running.result(timeout=15)
    assert cancelled.is_set()
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled" and run.lease_token is None
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert all(row.status == "failed" for row in operations)
        assert all(
            row.status in {"failed", "cancelled"}
            for row in session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id))
        )
        assert all(
            session.get(AgentToolCall, row.tool_call_id).status == "cancelled"
            for row in operations
            if row.id in {int(identifier) for identifier in questions}
        )
    for identifier in questions:
        with pytest.raises(BusinessError) as rejected:
            answer(factory, settings, identifier)
        assert rejected.value.code == "conflict" and rejected.value.status_code == 409
    with factory() as session:
        service = task_service(session, settings)
        current = service.get_conversation(conversation.id)
        service.delete_global_conversation(
            conversation.id, {"row_version": current.row_version}, uuid4().hex
        )
    assert purge_one(factory, None)
    with factory() as session:
        assert all(
            row.arguments == {} and row.result is None and row.wait_state is None
            for row in session.scalars(select(AgentOperation))
        )
        assert all(
            row.request_messages == [] and row.response is None
            for row in session.scalars(select(AgentTurn))
        )
