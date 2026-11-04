"""Framework helper models use the same protected SDK and durable parent Run budget."""

import asyncio
import json
import time

import pytest
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from sqlalchemy import select

from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from tests.integration.standard.assistant.test_agent_context_compaction import runtime
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    new_task,
)
from tests.support.assistant_http import provider as provider

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("termination", ["stop", "worker_loss"])
def test_interrupted_sent_call_preserves_confirmation_requirement(harness_workspace, termination):
    from datetime import timedelta

    from short_drama.agent.coordinator.recovery import recover
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.core.exceptions import WorkflowError
    from short_drama.core.time import utcnow
    from short_drama.service.assistant.execution.run import AgentRunService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_service

    conversation, accepted = new_task(harness_workspace)
    _, context = runtime(harness_workspace, accepted)
    RunModelLedger(context).before_send(
        {
            "model_call_id": "interrupted-call",
            "source": "assistant",
            "body": {"max_tokens": 20},
        }
    )
    if termination == "worker_loss":
        with context.factory.begin() as session:
            session.get(AgentRun, context.run_id).lease_until = utcnow() - timedelta(seconds=1)
        assert recover(context.factory, context.settings) == 1
    else:
        from types import SimpleNamespace

        with context.factory() as session:
            assert AgentRunService(session, context.settings).finish(
                SimpleNamespace(run_id=context.run_id, fence=context.fence),
                status="cancelled",
                error={"code": "agent_stopped"},
            )
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        assert run.error["accepted_unknown"] is True
        assert run.usage["reserved_output_tokens"] == 20
    with context.factory() as session:
        service = task_service(session, context.settings)
        state = service.task_runtime_state(conversation.id)
        assert state.runs[-1].error["accepted_unknown"] is True
        session.commit()
        with pytest.raises(WorkflowError) as caught:
            service.send_task_message(
                conversation.id,
                {
                    "task_id": str(accepted.task.id),
                    "content": "继续核对已保存成果",
                },
                "unconfirmed-interrupted-call",
            )
        assert caught.value.code == "assistant_acceptance_check_required"


def test_connection_retry_is_durable_without_a_sent_turn_or_reserved_tokens(harness_workspace):
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.service.assistant.execution.run import read_run

    _, accepted = new_task(harness_workspace)
    _, context = runtime(harness_workspace, accepted)
    ledger = RunModelLedger(context)
    with context.factory.begin() as session:
        session.get(AgentRun, context.run_id).status = "running"
    with context.factory() as session:
        before = dict(session.get(AgentRun, context.run_id).usage)
    ledger.connection_state(
        {
            "model_call_id": "connection-only",
            "status": "retrying",
            "retry_no": 2,
            "max_retries": 5,
            "timeout_seconds": 90,
        }
    )
    with context.factory() as session:
        from short_drama.dao.assistant.execution.run import AgentRunDAO

        run = session.get(AgentRun, context.run_id)
        public = read_run(session, run, queue_position=0, stopping=False)
        assert public.connection_retries[0].retry_no == 2
        projected = AgentRunDAO(session).projections(run.conversation_id, run_ids=[run.id])[0]
        assert (
            read_run(session, projected, queue_position=0, stopping=False).connection_retries
            == public.connection_retries
        )
        assert run.usage == before
        assert session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all() == []
    ledger.save_error(
        {
            "model_call_id": "connection-only",
            "code": "agent_connection_timeout",
            "accepted_unknown": False,
            "not_accepted": True,
        }
    )
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        assert read_run(session, run, queue_position=0, stopping=False).connection_retries == []


@pytest.mark.parametrize("source", ["assistant", "tool_selection", "summarization"])
def test_local_context_rejection_creates_no_turn_and_spends_no_parent_usage(
    harness_workspace, source
):
    from tests.support.assistant_runtime import RuntimeTransport

    _, accepted = new_task(harness_workspace)
    _, context = runtime(harness_workspace, accepted)
    transport = RuntimeTransport([])
    model = protected(context, transport)
    with context.factory() as session:
        before = dict(session.get(AgentRun, context.run_id).usage)
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            model.ainvoke(
                [HumanMessage(content="PRIVATE_UNSENT_SOURCE_" + "雨" * 60000)],
                config={"metadata": {"lc_source": source}},
            )
        )
    assert caught.value.context_budget["source"] == source
    assert transport.requests == []
    with context.factory() as session:
        assert session.get(AgentRun, context.run_id).usage == before
        assert (
            session.scalars(select(AgentTurn).where(AgentTurn.run_id == context.run_id)).all() == []
        )


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_bound_summary_preserves_sdk_shape_and_parent_ledger(harness_workspace, provider, protocol):
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.messages import SystemMessage

    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.ai.gateway.transport import SafeTransport
    from tests.support.assistant_http import response
    from tests.unit.standard.agent.test_assistant_model_protocols import (
        SETTINGS,
        claude_response,
        configured,
    )

    _, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    base, wire = provider
    wire["responses"] = [
        (200, claude_response() if protocol == "claude" else response(protocol, text="understood"))
        for _ in range(2)
    ]
    with context.factory() as session:
        snapshot = session.get(AgentRun, context.run_id).config_snapshot
    model = build_chat_model(
        {**snapshot, **configured(base, protocol)},
        "fixture-key",
        callbacks=RunModelLedger(context).callbacks(stream=False, max_output_tokens=64),
        transport=SafeTransport(SETTINGS),
    )

    class FinalSystem(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            return await handler(
                request.override(
                    system_message=SystemMessage(content="FINAL_BOUND_SYSTEM：保留当轮真实工具。")
                )
            )

    tool = StructuredTool.from_function(
        lambda value: value, name="bound_tool", description="当轮实际工具"
    )
    graph = build_agent(
        model=model,
        tools=[tool],
        middleware=[
            SkillSummarizationMiddleware(
                model=model, trigger=("messages", 5), keep=("messages", 2)
            ),
            FinalSystem(),
        ],
        checkpointer=saver,
        system_prompt="初始系统",
    )
    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content=f"历史{i}") for i in range(8)]},
            {"configurable": {"thread_id": saver.thread_id}},
            context=context,
        )
    )
    assert result["messages"][-1].text == "understood"
    assert any(
        message.additional_kwargs.get("lc_source") == "summarization"
        for message in result["messages"]
    )
    assert len(wire["calls"]) == 2
    body = wire["calls"][-1][2]
    assert "FINAL_BOUND_SYSTEM" in json.dumps(body, ensure_ascii=False)
    assert "bound_tool" in json.dumps(body["tools"], ensure_ascii=False)
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        turns = session.scalars(
            select(AgentTurn).where(AgentTurn.run_id == run.id).order_by(AgentTurn.turn_no)
        ).all()
        assert [turn.usage["source"] for turn in turns] == ["summarization", "assistant"]
        assert {turn.scope_id for turn in turns} == {context.scope_id}
        assert run.usage["decision_calls"] == len(turns) == 2
        assert run.usage["output_tokens"] == 6 and run.usage["reserved_output_tokens"] == 0


class FrameworkTransport:
    def __init__(self, factory):
        self.factory, self.requests = factory, []
        self.deadlines, self.started = [], []

    def request(self, method, url, *, body, on_send, deadline, **kwargs):
        self.started.append(time.monotonic())
        self.deadlines.append(deadline)
        on_send()
        # Checkpoint I/O may run concurrently; the model admission must release its Run lock.
        with self.factory.begin() as session:
            session.scalar(select(AgentRun.id).with_for_update())
        self.requests.append(body)
        function = body.get("tools", [{}])[0].get("function", {})
        message = {"role": "assistant", "content": "受控讨论或摘要"}
        finish = "stop"
        if function and "tools" in function.get("parameters", {}).get("properties", {}):
            message["tool_calls"] = [
                {
                    "id": "select",
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": '{"tools":[]}',
                    },
                }
            ]
            finish = "tool_calls"
        content = json.dumps(
            {
                "id": "controlled",
                "object": "chat.completion",
                "created": 1,
                "model": "controlled",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            }
        ).encode()
        return 200, {"Content-Type": "application/json"}, content


def protected(context, transport):
    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.model_ledger import RunModelLedger

    with context.factory() as session:
        snapshot = session.get(AgentRun, context.run_id).config_snapshot
    ledger = RunModelLedger(context)
    return build_chat_model(
        snapshot, "", callbacks=ledger.callbacks(stream=False), transport=transport
    )


@pytest.mark.parametrize("summary_text", ["", " \t\n "], ids=["empty", "whitespace"])
def test_empty_summary_keeps_original_checkpoint_and_sent_helper_usage(
    harness_workspace, summary_text
):
    from langchain_core.messages import messages_to_dict

    from short_drama.agent.resources.graph import GraphResources
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import (
        ExecutionMiddleware,
        SkillSummarizationMiddleware,
    )
    from tests.support.assistant_runtime import RuntimeTransport

    _, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    transport = RuntimeTransport([{"text": summary_text}, {"text": "不应发送主请求"}])
    model = protected(context, transport)
    with context.factory() as session:
        snapshot = session.get(AgentRun, context.run_id).config_snapshot
    graph = build_agent(
        model=model,
        tools=[],
        middleware=[
            ExecutionMiddleware(GraphResources(context, snapshot, None)),
            SkillSummarizationMiddleware(
                model=model, trigger=("messages", 5), keep=("messages", 2)
            ),
        ],
        checkpointer=saver,
        system_prompt="讨论",
    )
    original = [HumanMessage(content=f"唯一原始事实{i}", id=f"original:{i}") for i in range(8)]
    original_values = messages_to_dict(original)
    config = {"configurable": {"thread_id": saver.thread_id}}
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(graph.ainvoke({"messages": original}, config, context=context))
    assert caught.value.code == "agent_summary_empty"
    assert caught.value.requests == 1
    saved = saver.get_tuple(config)
    assert messages_to_dict(saved.checkpoint["channel_values"]["messages"]) == original_values
    assert len(transport.requests) == 1
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert run.usage["decision_calls"] == len(turns) == 1
        assert run.usage["input_tokens"] == 12 and run.usage["output_tokens"] == 3
        assert run.usage["reserved_output_tokens"] == 0
        assert turns[0].status == "succeeded" and turns[0].usage["source"] == "summarization"
        assert turns[0].response["raw"] and turns[0].usage["provider_usage"]
        assert all(not (turn.response or {}).get("graph_compaction") for turn in turns)
        assert all(not (turn.response or {}).get("graph_message") for turn in turns)


@pytest.mark.parametrize("configured_timeout", [120, 300])
def test_selector_summary_and_main_share_durable_parent_budget(
    harness_workspace, configured_timeout
):
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware

    _, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    context.settings.agent_model_timeout_seconds = configured_timeout
    transport = FrameworkTransport(context.factory)
    model = protected(context, transport)
    tool = StructuredTool.from_function(lambda value: value, name="unused", description="Unused")
    graph = build_agent(
        model=model,
        tools=[tool],
        middleware=[
            SkillSummarizationMiddleware(
                model=model,
                trigger=("messages", 5),
                keep=("messages", 2),
                trim_tokens_to_summarize=None,
            ),
            LLMToolSelectorMiddleware(model=model, max_tools=16),
        ],
        checkpointer=saver,
        system_prompt="讨论",
    )
    asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content=f"来源{i}") for i in range(8)]},
            {"configurable": {"thread_id": saver.thread_id}},
            context=context,
        )
    )
    assert len(transport.deadlines) == 3
    remaining = [
        deadline - started
        for deadline, started in zip(transport.deadlines, transport.started, strict=True)
    ]
    assert all(0 < value <= configured_timeout for value in remaining)
    if configured_timeout == 300:
        assert all(value > 120 for value in remaining)
    assert transport.deadlines[0] < transport.deadlines[1] < transport.deadlines[2]
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == context.run_id)).all()
        assert run.usage["decision_calls"] == len(transport.requests) == len(turns) == 3
        assert run.usage["output_tokens"] == 9
        assert run.usage["cached_tokens"] == 12
        assert run.usage["reserved_output_tokens"] == 0
        assert {turn.usage["source"] for turn in turns} == {
            "summarization",
            "tool_selection",
            "assistant",
        }
        assert all(
            turn.scope_id == context.scope_id and turn.status == "succeeded" for turn in turns
        )
        assert all(turn.response["raw"] for turn in turns)


def test_formal_cumulative_budget_does_not_block_but_cancel_still_denies(harness_workspace):
    _, accepted = new_task(harness_workspace)
    _saver, context = runtime(harness_workspace, accepted)
    transport = FrameworkTransport(context.factory)
    model = protected(context, transport)
    with context.factory.begin() as session:
        run = session.get(AgentRun, context.run_id)
        run.budget = {**run.budget, "decision_calls": 1}
    asyncio.run(model.ainvoke([HumanMessage(content="一次")]))
    asyncio.run(model.ainvoke([HumanMessage(content="超过旧累计上限仍可继续")]))
    assert len(transport.requests) == 2
    with context.factory.begin() as session:
        run = session.get(AgentRun, context.run_id)
        run.budget = {**run.budget, "decision_calls": 3}
        run.cancel_requested = 1
    with pytest.raises(AgentGatewayError) as cancelled:
        asyncio.run(model.ainvoke([HumanMessage(content="已停止")]))
    assert cancelled.value.code == "agent_admission_denied"
    assert len(transport.requests) == 2


def test_late_receipts_deduplicate_input_without_reported_output(harness_workspace):
    from datetime import timedelta

    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.core.time import utcnow

    _, accepted = new_task(harness_workspace)
    _saver, context = runtime(harness_workspace, accepted)
    ledger = RunModelLedger(context)
    ledger.before_send({"model_call_id": "late", "source": "assistant", "body": {"max_tokens": 20}})
    with context.factory.begin() as session:
        session.get(AgentRun, context.run_id).lease_until = utcnow() - timedelta(seconds=1)
    ledger.save_raw({"model_call_id": "late", "codec": "agent.raw-response"})
    payload = {"model_call_id": "late", "input_tokens": 12, "cached_tokens": 4}
    ledger.settle_usage(payload)
    ledger.settle_usage(payload)
    with context.factory() as session:
        usage = session.get(AgentRun, context.run_id).usage
        assert usage["input_tokens"] == 12 and usage["cached_tokens"] == 4

        assert usage["reserved_output_tokens"] == 20
    payload.update(output_tokens=3, output_tokens_reported=True)
    ledger.settle_usage(payload)
    ledger.settle_usage(payload)
    with context.factory() as session:
        usage = session.get(AgentRun, context.run_id).usage
        assert usage["output_tokens"] == 3 and usage["reserved_output_tokens"] == 0
        assert usage["input_tokens"] == 12 and usage["cached_tokens"] == 4


@pytest.mark.parametrize(
    "failure,reported",
    [
        ("length", True),
        ("invalid_tool_calls", True),
        ("length", False),
        ("rejected", False),
    ],
)
def test_failed_response_cost_remains_in_shared_run_budget(harness_workspace, failure, reported):
    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.runtime.context import execution_scope
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from tests.integration.standard.assistant.test_execution_scopes import calls_for
    from tests.unit.standard.agent.test_assistant_model_factory import UnusableTransport

    _, accepted = new_task(harness_workspace)
    _saver, context = runtime(harness_workspace, accepted)
    call_id = calls_for(context.factory, accepted, context.scope_id)[0]
    child = context.scopes.child(context.scope_id, call_id)
    ledger = RunModelLedger(context)
    with context.factory.begin() as session:
        run = session.get(AgentRun, context.run_id)
        run.budget = {**run.budget, "output_tokens": 10}
        snapshot = run.config_snapshot
    transport = UnusableTransport(failure, reported=reported)
    model = build_chat_model(
        snapshot,
        "",
        callbacks=ledger.callbacks(
            stream=False,
            max_output_tokens=10,
        ),
        transport=transport,
    )
    if failure == "length":
        message = asyncio.run(model.ainvoke([HumanMessage(content="检查持久截断回执")]))
        assert message.response_metadata["output_recovery"]
    else:
        with pytest.raises(AgentGatewayError):
            asyncio.run(model.ainvoke([HumanMessage(content="检查持久失败回执")]))
    with context.factory() as session:
        turn = session.scalar(
            select(AgentTurn)
            .where(
                AgentTurn.run_id == context.run_id,
                AgentTurn.usage["model_call_id"].as_string().is_not(None),
            )
            .order_by(AgentTurn.turn_no.desc())
        )
        usage = session.get(AgentRun, context.run_id).usage
        expected_output = 7 if reported else 0
        expected_reservation = 10 if failure == "length" and not reported else 0
        assert usage["decision_calls"] == len(transport.requests) == 1
        assert usage["output_tokens"] == expected_output
        assert usage["reserved_output_tokens"] == expected_reservation
        assert turn.status == ("succeeded" if failure == "length" else "failed")
        assert turn.response["raw"]
        error, receipt = dict(turn.error or {}), dict(turn.usage.get("provider_usage", {}))
    if error:
        ledger.save_error(error)
    if receipt:
        ledger.settle_usage(receipt)
    with context.factory() as session:
        assert session.get(AgentRun, context.run_id).usage == usage
        assert session.get(AgentTurn, turn.id).status == turn.status
    sibling = RunModelLedger(context)
    request = {
        "model_call_id": "child-after-failure",
        "source": "assistant",
        "body": {"max_tokens": 4},
    }
    token = execution_scope.set(child.id)
    try:
        sibling.before_send(request)
    finally:
        execution_scope.reset(token)
