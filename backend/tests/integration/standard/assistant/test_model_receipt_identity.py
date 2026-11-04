"""Real graph tasks distinguish new decisions from durable model receipt recovery."""

import json

import pytest
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import message_to_dict
from sqlalchemy import select

from short_drama.agent.runtime import executor
from short_drama.agent.runtime.middleware import ExecutionMiddleware, SkillSummarizationMiddleware
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.utils.json_digest import digest
from tests.integration.standard.assistant.test_agent_runtime import expire, runtime_for
from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import new_task
from tests.support.agent_runtime_live import controlled_case as controlled_case
from tests.support.assistant_http import provider as provider
from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def test_inactive_skill_release_after_summary_reaches_a_fresh_model_task(
    task_workspace, monkeypatch
):
    factory, settings, _ = task_workspace
    settings.minio_image_bucket = "fixture-skill-text"
    skill = create_skill(
        (*task_workspace, _Storage(factory)),
        instructions="WITHDRAWN_SKILL_BODY_只允许活动的新激活保留这段规范正文。",
        resources=[
            {"path": "references/evidence.md", "kind": "reference", "content": "冻结原始证据"}
        ],
    )
    _, accepted = new_task(task_workspace)
    with factory.begin() as session:
        session.get(AgentConversation, accepted.run.conversation_id).title = "模型任务身份验收"
    middleware_factory = executor.assistant_middleware
    original_wrap = ExecutionMiddleware.awrap_model_call
    tasks, activations, mains, summaries = [], {}, [], []

    class OneSummary(SkillSummarizationMiddleware):
        async def _summarize(self, request, handler):
            if summaries:
                return await handler(request)
            return await super()._summarize(request, handler)

    def middleware(**kwargs):
        items = middleware_factory(**kwargs)
        items = [
            OneSummary(model=kwargs["model"], trigger=("messages", 3), keep=("messages", 1))
            if isinstance(item, SkillSummarizationMiddleware)
            else item
            for item in items
        ]
        items.append(ModelCallLimitMiddleware(thread_limit=12, exit_behavior="error"))
        return items

    async def observe(item, request, handler):
        info = request.runtime.execution_info
        tasks.append(
            {
                "identity": (info.thread_id, info.checkpoint_id, info.checkpoint_ns, info.task_id),
                "content_hash": digest([message_to_dict(row) for row in request.state["messages"]]),
                "messages": list(request.state["messages"]),
                "skill_active": {
                    row["id"]: row["active"] for row in request.state.get("skill_activations", [])
                },
            }
        )
        return await original_wrap(item, request, handler)

    def reply(body):
        if not body.get("tools"):
            summaries.append(body)
            return {"text": "已归纳策略核验，继续核对冻结版本。"}
        mains.append(body)
        step = len(mains)
        if step in {1, 5}:
            return calls(
                (
                    "load" if step == 1 else "reload",
                    "runtime__load_skill",
                    {
                        "name": skill.name,
                        "content_version": skill.content_version,
                        "purpose": "核对冻结方法",
                    },
                )
            )
        if step == 2:
            activations["old"] = tool_result(body, "load")["activation_id"]
            return calls(("policy", "runtime__get_generation_policy", {}))
        if step == 3:
            return calls(("advance", "runtime__get_generation_policy", {}))
        if step in {4, 7}:
            assert summaries, "必须先真实完成一次框架摘要"
            return {
                **calls(
                    (
                        "release" if step == 4 else "release-again",
                        "runtime__release_skill",
                        {"activation_id": activations["old"]},
                    )
                ),
                "text": "WITHDRAWN_RELEASE_INFERENCE_本句推导依赖旧激活，须随完整组撤回。",
            }
        if step == 6:
            activations["new"] = tool_result(body, "reload")["activation_id"]
            assert activations["new"] != activations["old"]
            return calls(
                (
                    "stale-read",
                    "runtime__read_skill_resource",
                    {"activation_id": activations["old"], "path": "references/evidence.md"},
                )
            )
        assert step == 8
        return {"text": "已识别旧激活无效，保留当前新激活并完成核对"}

    monkeypatch.setattr(executor, "assistant_middleware", middleware)
    monkeypatch.setattr(ExecutionMiddleware, "awrap_model_call", observe)
    transport = RuntimeTransport([reply] * 20)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        releases = [row for row in operations if row.tool_name == "runtime__release_skill"]
        again = max(releases, key=lambda row: row.id)
        assert again.status == "succeeded" and again.result["already_applied"]
        stale = next(row for row in operations if row.tool_name == "runtime__read_skill_resource")
        assert stale.status == "failed" and stale.error["code"] == "not_found"
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(bool((row.response or {}).get("graph_compaction")) for row in turns) == 1
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        assert run.usage["reserved_output_tokens"] == 0
    assert tasks[6]["content_hash"] == tasks[7]["content_hash"]
    assert tasks[6]["identity"] != tasks[7]["identity"]
    assert [message_to_dict(row) for row in tasks[6]["messages"]] == [
        message_to_dict(row) for row in tasks[7]["messages"]
    ]
    assert run.status == "succeeded", {"error": run.error, "new_handler_calls": len(mains)}
    assert len(mains) == 8
    assert not tasks[-1]["skill_active"][activations["old"]]
    assert tasks[-1]["skill_active"][activations["new"]]
    assert "WITHDRAWN_RELEASE_INFERENCE" not in json.dumps(mains[-1], ensure_ascii=False)
    assert all(
        message.id not in {row.id for row in tasks[-1]["messages"]}
        for message in tasks[-2]["messages"]
        if message.additional_kwargs.get("skill_activation_ids") == [activations["old"]]
    )


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_each_public_model_task_field_is_required_but_invoke_attempt_is_not(
    task_workspace, provider, protocol
):
    import asyncio
    from contextvars import ContextVar
    from types import SimpleNamespace

    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import HumanMessage
    from langgraph.runtime import ExecutionInfo, Runtime

    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.ai.gateway.transport import SafeTransport
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime
    from tests.support.assistant_http import response
    from tests.unit.standard.agent.test_assistant_model_protocols import (
        SETTINGS,
        claude_response,
        configured,
    )

    _, accepted = new_task(task_workspace)
    _, context = runtime(task_workspace, accepted)
    base, wire = provider
    wire["responses"] = [
        (
            200,
            claude_response() if protocol == "claude" else response(protocol, text="新图任务答复"),
        )
        for _ in range(6)
    ]
    with context.factory() as session:
        snapshot = session.get(AgentRun, context.run_id).config_snapshot
    model = build_chat_model(
        {**snapshot, **configured(base, protocol)},
        "fixture-key",
        callbacks=RunModelLedger(context).callbacks(stream=False, max_output_tokens=64),
        transport=SafeTransport(SETTINGS),
    )
    middleware = ExecutionMiddleware(SimpleNamespace(model_state=ContextVar("receipt-state")))
    info = ExecutionInfo(
        checkpoint_id="checkpoint-a",
        checkpoint_ns="child:full-parent|model:task-a",
        task_id="task-a",
        thread_id="thread-a",
    )

    async def invoke(identity, content="逐字相同的用户输入"):
        messages = [HumanMessage(content=content, id="same-message")]
        request = ModelRequest(
            model=model,
            messages=messages,
            state={"messages": messages},
            runtime=Runtime(context=context, execution_info=identity),
        )

        async def handler(current):
            return await current.model.ainvoke(current.messages)

        return await middleware.awrap_model_call(request, handler)

    async def scenario():
        original = await invoke(info)
        call_id = original.response_metadata["protected_usage"]["model_call_id"]
        resumed = await invoke(
            info.patch(run_id="new-invoke", node_attempt=3, node_first_attempt_time=1.0)
        )
        assert resumed.response_metadata["protected_usage"]["model_call_id"] == call_id
        assert len(wire["calls"]) == 1
        for field in ("thread_id", "checkpoint_id", "checkpoint_ns", "task_id"):
            changed = (
                "child:other-parent|model:task-a" if field == "checkpoint_ns" else "new-" + field
            )
            new = await invoke(info.patch(**{field: changed}))
            assert new.response_metadata["protected_usage"]["model_call_id"] != call_id, field
        changed = await invoke(info, "同一图任务的内容已经变化")
        assert changed.response_metadata["protected_usage"]["model_call_id"] != call_id
        original_again = await invoke(info)
        assert original_again.response_metadata["protected_usage"]["model_call_id"] == call_id

    asyncio.run(scenario())
    assert len(wire["calls"]) == 6
    with context.factory() as session:
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == context.run_id)).all()
        assert len(turns) == 6
        assert all(row.usage["source"] == "assistant" for row in turns)
        assert len({row.response["graph_input_hash"] for row in turns}) == 6
        run = session.get(AgentRun, context.run_id)
        assert run.usage["decision_calls"] == 6 and run.usage["output_tokens"] == 18
        assert run.usage["input_tokens"] == 42
        assert run.usage["reserved_output_tokens"] == 0


@pytest.mark.parametrize("child", [False, True], ids=["root", "nested-child"])
def test_committed_model_recovers_same_public_task_in_a_new_worker(controlled_case, child):
    case = controlled_case
    _, accepted, _ = case.create("恢复同一步模型回执，再核对一次真实策略")
    with case.factory.begin() as session:
        session.get(AgentConversation, accepted.run.conversation_id).title = "Worker恢复验收"
    policy = calls(("policy", "runtime__get_generation_policy", {}))
    first = [policy]
    second = [{"text": "已完成策略核对"}]
    if child:
        first = {
            "routes": {
                "root": [
                    calls(
                        (
                            "delegate",
                            "task",
                            {"subagent_type": "workbench-task", "description": "子任务:identity"},
                        )
                    )
                ],
                "identity": [policy],
            }
        }
        second = {
            "routes": {
                "root": [{"text": "已接收子任务核对结果"}],
                "identity": [{"text": "子范围核对完成"}],
            }
        }
    first_run = case.execute(
        accepted,
        fault="child_model_committed" if child else "model_committed",
        controlled_replies=first,
    )
    assert first_run.status == "running"
    assert case.facts["worker_deliveries"][0]["crash_injected"]
    with case.factory() as session:
        committed = session.scalars(
            select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)
        ).all()
        assert all(row.status == "succeeded" and row.usage["settled"] for row in committed)
        before_usage = dict(session.get(AgentRun, accepted.run.id).usage)
    expire((case.factory, case.settings, case.model_id), accepted)
    final = case.execute(accepted, fault="model_identity", controlled_replies=second)
    assert final.status == "succeeded", final.error
    first_delivery, resumed = case.facts["worker_deliveries"]
    assert first_delivery["pid"] != resumed["pid"]
    assert first_delivery["model_tasks"][-1] == resumed["model_tasks"][0]
    assert resumed["controlled_requests"] == (2 if child else 1)
    assert len(resumed["model_tasks"]) == (3 if child else 2)
    assert resumed["model_tasks"][0]["identity"] != resumed["model_tasks"][1]["identity"]
    if child:
        assert "|" in resumed["model_tasks"][0]["identity"]["checkpoint_ns"]
        assert resumed["model_tasks"][0]["scope_id"] != resumed["model_tasks"][-1]["scope_id"]
    with case.factory() as session:
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)).all()
        assert len(turns) == (8 if child else 4)
        assert all(row.status == "succeeded" and row.usage["settled"] for row in turns)
        assert final.usage["decision_calls"] == len(turns)
        assert final.usage["input_tokens"] == 12 * len(turns)
        assert final.usage["output_tokens"] == 3 * len(turns)
        assert final.usage["reserved_output_tokens"] == 0
        assert final.usage["decision_calls"] - before_usage["decision_calls"] == (4 if child else 2)
        for source in ("assistant", "tool_selection"):
            assert sum(row.usage["source"] == source for row in turns) == (4 if child else 2)
        assistant = [row for row in turns if row.usage["source"] == "assistant"]
        assert len({row.response["graph_input_hash"] for row in assistant}) == len(assistant)
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        ).all()
        assert len(operations) == (2 if child else 1)
        assert all(row.status == "succeeded" for row in operations)
