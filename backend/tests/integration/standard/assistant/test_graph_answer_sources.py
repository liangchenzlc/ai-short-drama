"""Real answered sources survive group revocation without widening execution scopes."""

import json
from collections import Counter

import pytest
from sqlalchemy import select

from short_drama.agent.gateway.langchain import ProtectedChatModel
from short_drama.agent.runtime import executor
from short_drama.agent.runtime.context import execution_scope
from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from tests.integration.standard.assistant.test_agent_questions import QUESTION, answer, execute_next
from tests.integration.standard.assistant.test_agent_runtime import runtime_for, source_payload
from tests.integration.standard.assistant.test_agent_subtasks import child_name, delegation
from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import new_task, task_service
from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

pytestmark = pytest.mark.integration
task_workspace = harness_workspace

ANSWER = "ELIGIBLE_ANSWER_CONTINUE_1029"
BODY = "WITHDRAWN_SKILL_BODY_1029"
INFERENCE = "WITHDRAWN_AI_ECHO_1029"
PROMPT = "QUESTION_PROMPT_NOT_AN_ANSWER_1029"
GOAL = "讨论镜头节奏"


def wire_text(body):
    return json.dumps(body["messages"], ensure_ascii=False)


def assert_complete_groups(body):
    calls = [call["id"] for row in body["messages"] for call in row.get("tool_calls", [])]
    results = [row["tool_call_id"] for row in body["messages"] if row["role"] == "tool"]
    assert Counter(calls) == Counter(results)


def user_sources(body):
    return source_payload(body)["task_user_sources"]


def current_activations(body):
    return source_payload(body)["current_skill_activations"]


def observe_admissions(monkeypatch):
    original = ProtectedChatModel._agenerate
    observed = []

    async def observe(model, messages, stop=None, run_manager=None, **kwargs):
        observed.append(
            {
                "source": (getattr(run_manager, "metadata", None) or {}).get(
                    "lc_source", "assistant"
                ),
                "scope_id": str(execution_scope.get()),
                "pinned_source_ids": [
                    message.additional_kwargs["source_message_ids"]
                    for message in messages
                    if message.id and message.id.startswith("task-sources:")
                ],
                "text": json.dumps([message.content for message in messages], ensure_ascii=False),
            }
        )
        return await original(model, messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr(ProtectedChatModel, "_agenerate", observe)
    return observed


def test_reload_exposes_current_activation_without_rewriting_its_historical_receipt(task_workspace):
    factory, settings, _ = task_workspace
    skill = create_skill((*task_workspace, _Storage(factory)), instructions=BODY, resources=[])
    _, accepted = new_task(task_workspace)
    mains, identities = [], {}
    load = {
        "name": skill.name,
        "content_version": "1",
        "purpose": "PURPOSE_NOT_CURRENT_STATE",
    }

    def reply(body):
        mains.append(body)
        step = len(mains)
        if step == 1:
            return calls(("load", "runtime__load_skill", load))
        if step == 2:
            historical = tool_result(body, "load")
            identities.update(old=historical["activation_id"], body=historical["body_operation_id"])
            return {
                **calls(
                    ("release", "runtime__release_skill", {"activation_id": identities["old"]})
                ),
                "text": INFERENCE,
            }
        if step == 3:
            return calls(("reload", "runtime__load_skill", load))
        assert step == 4
        identities["new"] = tool_result(body, "reload")["activation_id"]
        return {"text": "已区分历史加载回执与当前方法状态。"}

    runtime_for(factory, settings, RuntimeTransport([reply] * 4)).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        releases = session.scalars(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "runtime__release_skill",
            )
        ).all()
        assert len(releases) == 1 and releases[0].result["active"] is False
        original = session.get(AgentOperation, int(identities["body"])).result
        assert original["activation_id"] == identities["old"] and original["active"] is True
    assert BODY not in wire_text(mains[2]) and INFERENCE not in wire_text(mains[2])
    assert not any(row.get("tool_call_id") == "release" for row in mains[2]["messages"])
    historical = tool_result(mains[3], "load")
    reloaded = tool_result(mains[3], "reload")
    assert historical["activation_id"] == identities["old"] and historical["active"] is True
    assert reloaded["activation_id"] != historical["activation_id"]
    assert reloaded["body_operation_id"] == identities["body"]
    assert "instructions" not in reloaded and historical["instructions"] == BODY
    for body in mains:
        assert_complete_groups(body)
    states = current_activations(mains[3])
    assert {row["activation_id"]: row["active"] for row in states} == {
        identities["old"]: False,
        identities["new"]: True,
    }, "current state disappeared while the historical canonical receipt remained active=true"
    assert all(row["body_operation_id"] == identities["body"] for row in states)
    assert all(
        set(row) == {"activation_id", "skill_id", "content_version", "body_operation_id", "active"}
        for row in states
    )


@pytest.mark.parametrize("same_group,forgotten", [(True, False), (False, False), (True, True)])
def test_release_keeps_only_eligible_answer_sources_through_summary_and_reload(
    task_workspace, monkeypatch, same_group, forgotten
):
    factory, settings, model = task_workspace
    skill = create_skill((*task_workspace, _Storage(factory)), instructions=BODY, resources=[])
    conversation, accepted = new_task(task_workspace)
    observed = observe_admissions(monkeypatch)
    mains, summaries, facts, activations = [], [], {}, {}
    original = executor.assistant_middleware

    class OneSummary(SkillSummarizationMiddleware):
        async def _summarize(self, request, handler):
            if len(mains) < 3 or summaries:
                return await handler(request)
            return await super()._summarize(request, handler)

    def middleware(**kwargs):
        items = original(**kwargs)
        items[1] = OneSummary(model=kwargs["model"], trigger=("messages", 3), keep=("messages", 1))
        return items

    monkeypatch.setattr(executor, "assistant_middleware", middleware)
    load = (
        "load",
        "runtime__load_skill",
        {"name": skill.name, "content_version": "1", "purpose": "核对答案来源"},
    )
    question = ("question", "runtime__ask_user", {**QUESTION, "title": PROMPT})
    initial = RuntimeTransport(
        [calls(load, question)] if same_group else [calls(load), calls(question)]
    )
    runtime_for(factory, settings, initial).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input", run.error
        operation_id = session.scalar(
            select(AgentOperation.id).where(
                AgentOperation.run_id == run.id, AgentOperation.tool_name == "runtime__ask_user"
            )
        )
    receipt = answer(factory, settings, operation_id, values={"ending": ANSWER})
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        assert not operation.wait_state["consumed"]
        assert not AgentTranscriptDAO(session).scope_answer_messages(
            accepted.task.id, accepted.run.id, operation.scope_id, set()
        )

    def reply(body):
        if not body.get("tools"):
            summaries.append(body)
            return {"text": "已归纳策略，摘要不复述答案或方法正文。"}
        mains.append(body)
        step = len(mains)
        facts[step] = next(row for row in reversed(observed) if row["source"] == "tool_selection")
        assert source_payload(body)["current_run"] == {
            "run_id": str(accepted.run.id),
            "trigger_message_id": str(accepted.message.id),
            "trigger_seq": accepted.message.seq,
        }
        if step == 1:
            activations["old"] = tool_result(body, "load")["activation_id"]
            if forgotten:
                from tests.integration.standard.assistant.test_assistant_memory import (
                    memory,
                    service,
                )

                with factory() as session:
                    manager = service(session)
                    item = manager.create_item(
                        memory(
                            source_conversation_id=conversation.id,
                            source_message_ids=[receipt.answer_message_id],
                        ),
                        "forget-answer-source",
                    )
                    manager.forget_item(item.id, {"content_version": "1"})
            return {
                **calls(
                    ("release", "runtime__release_skill", {"activation_id": activations["old"]})
                ),
                "text": INFERENCE,
            }
        if step in {2, 3}:
            return calls((f"policy-{step}", "runtime__get_generation_policy", {}))
        if step == 4:
            return calls(("reload", "runtime__load_skill", load[2]))
        assert step == 5
        activations["new"] = tool_result(body, "reload")["activation_id"]
        return {"text": "已依据当前合格来源完成核对。"}

    resumed = RuntimeTransport([reply] * 8)
    execute_next(task_workspace, accepted, resumed)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
    assert len(mains) == 5 and summaries
    for body in resumed.selection_requests:
        assert source_payload(body)["current_run"] == {
            "run_id": str(accepted.run.id),
            "trigger_message_id": str(accepted.message.id),
            "trigger_seq": accepted.message.seq,
        }
    assert ANSWER in wire_text(mains[0])
    for step in (2, 4, 5):
        assert (ANSWER in wire_text(mains[step - 1])) is (not forgotten), f"main step {step}"
        assert (ANSWER in facts[step]["text"]) is (not forgotten), f"selector step {step}"
        assert GOAL in facts[step]["text"]
        assert_complete_groups(mains[step - 1])
    assert BODY not in wire_text(mains[1]) and INFERENCE not in wire_text(mains[1])
    if same_group:
        assert PROMPT not in wire_text(mains[1])
    sources = user_sources(mains[-1])
    projected = [row for row in sources["items"] if row["id"] == str(receipt.answer_message_id)]
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        operation = session.get(AgentOperation, operation_id)
        assert operation.wait_state["consumed"] is True and operation.status == "succeeded"
        message = session.get(AgentMessage, receipt.answer_message_id)
        assert message.kind == "tool_answer" and ANSWER in message.content
        assert len(projected) == (0 if forgotten else 1)
        if projected:
            assert projected[0]["content"] == message.content and not projected[0]["truncated"]
        assert len(session.scalars(select(AgentTaskInput)).all()) == 1
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(bool((turn.response or {}).get("graph_compaction")) for turn in turns) == 1
    assert activations["new"] != activations["old"]
    assert (
        answer(factory, settings, operation_id, values={"ending": ANSWER}, key="retry") == receipt
    )


def test_real_child_answer_sources_keep_frozen_goal_and_do_not_reach_parent_or_sibling(
    task_workspace, monkeypatch
):
    factory, settings, model_id = task_workspace
    skill = create_skill((*task_workspace, _Storage(factory)), instructions=BODY, resources=[])
    private_goal = "ROOT_GOAL_PRIVATE_1029"
    goal = "讨论镜头节奏。" * 40 + private_goal
    with factory() as session:
        manager = task_service(session, settings)
        conversation = manager.create_global_conversation({}, "private-child-sources")
        accepted = manager.send_task_message(
            conversation.id, {"content": goal, "model_config_id": model_id}, "private-root-goal"
        )
    observed = observe_admissions(monkeypatch)
    names = ("one", "two")
    rounds, summaries, mains = {name: 0 for name in names}, set(), {name: [] for name in names}
    scopes, answers = {}, {}
    original = executor.assistant_middleware

    class ChildSummary(SkillSummarizationMiddleware):
        async def _summarize(self, request, handler):
            scope_id = str(request.runtime.context.scope_id)
            name = scopes.get(scope_id)
            if name is None or rounds[name] < 3 or scope_id in summaries:
                return await handler(request)
            return await super()._summarize(request, handler)

    def middleware(**kwargs):
        items = original(**kwargs)
        items[1] = ChildSummary(
            model=kwargs["model"], trigger=("messages", 3), keep=("messages", 1)
        )
        return items

    monkeypatch.setattr(executor, "assistant_middleware", middleware)
    runtime_for(
        factory,
        settings,
        RuntimeTransport([calls(("root-question", "runtime__ask_user", QUESTION))]),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        root_question = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        root_scope = root_question.scope_id
        root_question_id = root_question.id
    answer(factory, settings, root_question_id, values={"ending": "ROOT_ANSWER_PRIVATE"})
    parent_round = 0

    def reply(body):
        nonlocal parent_round
        if not body.get("tools"):
            summaries.add(str(execution_scope.get()))
            return {"text": "已归纳子任务策略，不复述答案或目标。"}
        name = child_name(body) or scopes.get(str(execution_scope.get()))
        if name is None:
            parent_round += 1
            return (
                calls(*(delegation(name) for name in names))
                if parent_round == 1
                else {"text": "父任务已整合两份独立交付。"}
            )
        rounds[name] += 1
        step = rounds[name]
        mains[name].append(body)
        load = (
            "load",
            "runtime__load_skill",
            {"name": skill.name, "content_version": "1", "purpose": "独立核对子任务答案"},
        )
        if step == 1:
            return calls(load, ("question", "runtime__ask_user", {**QUESTION, "title": PROMPT}))
        if step == 2:
            activation = tool_result(body, "load")["activation_id"]
            return {
                **calls(("release", "runtime__release_skill", {"activation_id": activation})),
                "text": INFERENCE,
            }
        if step == 3:
            return calls(("policy", "runtime__get_generation_policy", {}))
        if step == 4:
            return calls(("reload", "runtime__load_skill", load[2]))
        assert step == 5
        return {"text": "完成:" + name}

    waiting = RuntimeTransport([reply] * 12)
    execute_next(task_workspace, accepted, waiting)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input", run.error
        questions = session.scalars(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "runtime__ask_user",
                AgentOperation.scope_id != root_scope,
            )
        ).all()
        assert len(questions) == 2 and len({row.wait_state["namespace"] for row in questions}) == 2
        for question in questions:
            scope = session.get(AgentExecutionScope, question.scope_id)
            parent = session.scalar(
                select(AgentOperation).where(
                    AgentOperation.tool_call_id == scope.parent_tool_call_id
                )
            )
            name = parent.arguments["description"].split("；", 1)[0].removeprefix("子任务:")
            scopes[str(scope.id)] = name
            answers[name] = question.id
    for name in names:
        answer(factory, settings, answers[name], values={"ending": "CHILD_ANSWER_PRIVATE_" + name})
    with factory() as session:
        sources = AgentTranscriptDAO(session).scope_answer_messages(
            accepted.task.id, accepted.run.id, root_scope, set()
        )
        assert len(sources) == 1 and "ROOT_ANSWER_PRIVATE" in sources[0].content
        for scope_id in scopes:
            assert not AgentTranscriptDAO(session).scope_answer_messages(
                accepted.task.id, accepted.run.id, int(scope_id), set()
            )
    resumed = RuntimeTransport([reply] * 20)
    execute_next(task_workspace, accepted, resumed)
    for name in names:
        assert rounds[name] == 5
        for body in mains[name]:
            payload = source_payload(body)
            assert not {"current_run", "trigger_message_id", "trigger_seq"}.intersection(payload)
            assert str(accepted.message.id) not in json.dumps(payload)
            assert goal not in wire_text(body) and private_goal not in wire_text(body)
            assert "ROOT_ANSWER_PRIVATE" not in wire_text(body)
        for body in mains[name][2:]:
            text = wire_text(body)
            assert "CHILD_ANSWER_PRIVATE_" + name in text
            assert "ROOT_ANSWER_PRIVATE" not in text
            assert (
                "CHILD_ANSWER_PRIVATE_" + next(other for other in names if other != name)
                not in text
            )
            assert "子任务:" + name in text
            assert_complete_groups(body)
        assert BODY not in wire_text(mains[name][2])
        assert INFERENCE not in wire_text(mains[name][2]) and PROMPT not in wire_text(
            mains[name][2]
        )
        sources = user_sources(mains[name][-1])
        assert len(sources["items"]) == 1
        assert "CHILD_ANSWER_PRIVATE_" + name in sources["items"][0]["content"]
        assert [row["active"] for row in current_activations(mains[name][2])] == [False]
        assert [row["active"] for row in current_activations(mains[name][-1])] == [False, True]
    for scope_id, name in scopes.items():
        selectors = [
            row
            for row in observed
            if row["scope_id"] == scope_id and row["source"] == "tool_selection"
        ]
        assert len(selectors) == 5
        for row in selectors[2:]:
            assert "子任务:" + name in row["text"]
            assert "CHILD_ANSWER_PRIVATE_" + name in row["text"]
            assert "ROOT_ANSWER_PRIVATE" not in row["text"]
            assert (
                "CHILD_ANSWER_PRIVATE_" + next(other for other in names if other != name)
                not in row["text"]
            )
        assert scope_id in summaries
    for body in [*waiting.selection_requests, *resumed.selection_requests]:
        payload = source_payload(body)
        if "task_delegation_sources" in payload:
            assert not {"current_run", "trigger_message_id", "trigger_seq"}.intersection(payload)
            assert str(accepted.message.id) not in json.dumps(payload)
            assert goal not in wire_text(body) and private_goal not in wire_text(body)
            assert "ROOT_ANSWER_PRIVATE" not in wire_text(body)
        else:
            assert payload["current_run"] == {
                "run_id": str(accepted.run.id),
                "trigger_message_id": str(accepted.message.id),
                "trigger_seq": accepted.message.seq,
            }
    final_parent = resumed.main_requests[-1]
    assert source_payload(final_parent)["current_run"] == {
        "run_id": str(accepted.run.id),
        "trigger_message_id": str(accepted.message.id),
        "trigger_seq": accepted.message.seq,
    }
    assert goal in wire_text(final_parent) and private_goal in wire_text(final_parent)
    assert "ROOT_ANSWER_PRIVATE" in wire_text(final_parent)
    assert "CHILD_ANSWER_PRIVATE_" not in wire_text(final_parent)
    assert not current_activations(final_parent)
    assert not {row["activation_id"] for row in current_activations(mains["one"][-1])}.intersection(
        row["activation_id"] for row in current_activations(mains["two"][-1])
    )
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(bool((turn.response or {}).get("graph_compaction")) for turn in turns) == 2


@pytest.mark.parametrize("withdraw_trigger", [False, True])
def test_current_run_sources_survive_summary_and_worker_rebuild_without_reviving_withdrawal(
    task_workspace, monkeypatch, withdraw_trigger
):
    factory, settings, _ = task_workspace
    conversation, accepted = new_task(task_workspace)
    mains, summaries, supplements = [], [], []
    observed = observe_admissions(monkeypatch)
    original = executor.assistant_middleware

    class OneSummary(SkillSummarizationMiddleware):
        async def _summarize(self, request, handler):
            if len(mains) < 2 or summaries:
                return await handler(request)
            return await super()._summarize(request, handler)

    def middleware(**kwargs):
        items = original(**kwargs)
        items[1] = OneSummary(model=kwargs["model"], trigger=("messages", 3), keep=("messages", 1))
        return items

    monkeypatch.setattr(executor, "assistant_middleware", middleware)
    expected = {
        "run_id": str(accepted.run.id),
        "trigger_message_id": str(accepted.message.id),
        "trigger_seq": accepted.message.seq,
    }

    def reply(body):
        if not body.get("tools"):
            summaries.append(body)
            return {"text": "已归纳工具核对过程，不复述启动消息、身份或后补。"}
        mains.append(body)
        if len(mains) == 1:
            with factory() as session:
                item = task_service(session, settings).send_task_message(
                    conversation.id,
                    {
                        "task_id": accepted.task.id,
                        "content": "LATEST_ELIGIBLE_CORRECTION_1029：继续只读核对",
                    },
                    "source-latest-correction",
                )
                assert item.run.id == accepted.run.id
                supplements.append(item.message)
        if len(mains) < 3:
            return calls((f"policy-{len(mains)}", "runtime__get_generation_policy", {}))
        assert len(mains) == 3
        return calls(("question", "runtime__ask_user", QUESTION))

    initial = RuntimeTransport([reply] * 8)
    runtime_for(factory, settings, initial).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input", run.error
        question = session.scalar(
            select(AgentOperation).where(AgentOperation.tool_name == "runtime__ask_user")
        )
        question_id = question.id
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(bool((turn.response or {}).get("graph_compaction")) for turn in turns) == 1
    assert len(summaries) == 1
    for body in [
        *initial.selection_requests,
        *[row for row in initial.main_requests if row.get("tools")],
    ]:
        assert source_payload(body)["current_run"] == expected
    before_resume = len(observed)
    receipt = answer(factory, settings, question_id, values={"ending": ANSWER})
    if withdraw_trigger:
        from tests.integration.standard.assistant.test_assistant_memory import memory, service

        with factory() as session:
            manager = service(session)
            item = manager.create_item(
                memory(
                    source_conversation_id=conversation.id, source_message_ids=[accepted.message.id]
                ),
                "forget-current-run-trigger",
            )
            manager.forget_item(item.id, {"content_version": "1"})
    resumed = RuntimeTransport(
        [
            calls(("resumed-policy", "runtime__get_generation_policy", {})),
            {"text": "已按合格补充和真实答案完成只读核对。"},
        ]
    )
    execute_next(task_workspace, accepted, resumed)
    assert len(resumed.selection_requests) == len(resumed.main_requests) == 2
    for body in [*resumed.selection_requests, *resumed.main_requests]:
        payload = source_payload(body)
        assert payload["current_run"] == (
            {"run_id": str(accepted.run.id), "trigger_source_withheld": True}
            if withdraw_trigger
            else expected
        )
        rows = payload["task_user_sources"]["items"]
        assert len([row for row in rows if row["id"] == str(receipt.answer_message_id)]) == 1
        assert len([row for row in rows if row["id"] == str(supplements[0].id)]) == 1
        assert ANSWER in wire_text(body) and supplements[0].content in wire_text(body)
        if withdraw_trigger:
            assert str(accepted.message.id) not in wire_text(body) and GOAL not in wire_text(body)
        else:
            assert len([row for row in rows if row["id"] == str(accepted.message.id)]) == 1
    for row in observed[before_resume:]:
        assert len(row["pinned_source_ids"]) == 1
        assert (str(accepted.message.id) in row["pinned_source_ids"][0]) is (not withdraw_trigger)
    for body in resumed.main_requests:
        assert_complete_groups(body)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert run.trigger_message_id == accepted.message.id
        question = session.get(AgentOperation, question_id)
        assert question.status == "succeeded" and question.wait_state["consumed"] is True
        assert len(session.scalars(select(AgentTaskInput)).all()) == 2


def test_current_activation_projection_filters_eligibility_and_bounds_identity_only():
    from short_drama.agent.context.graph import _current_skill_activations

    original = {
        "id": "101",
        "task_id": "10",
        "scope_id": "20",
        "source_message_id": "30",
        "body_operation_id": "40",
        "active": False,
        "purpose": "PRIVATE_PURPOSE",
        "issue": "PRIVATE_ISSUE",
        "skill": {"id": "skill", "content_version": "1", "name": "PRIVATE_NAME"},
    }
    state = {
        "excluded_skill_ids": ["excluded"],
        "skill_activations": [
            original,
            {**original, "id": "102", "task_id": "11"},
            {**original, "id": "103", "scope_id": "21"},
            {**original, "id": "104", "source_message_id": "31"},
            {**original, "id": "105", "skill": {"id": "excluded", "content_version": "1"}},
        ],
    }
    assert _current_skill_activations(10, 20, state, {31}) == [
        {
            "activation_id": "101",
            "skill_id": "skill",
            "content_version": "1",
            "body_operation_id": "40",
            "active": False,
        }
    ]
    bounded = {
        "skill_activations": [{**original, "id": str(identifier)} for identifier in range(101, 141)]
    }
    result = _current_skill_activations(10, 20, bounded, set())
    assert len(result) == 32
    assert result[0]["activation_id"] == "109" and result[-1]["activation_id"] == "140"
