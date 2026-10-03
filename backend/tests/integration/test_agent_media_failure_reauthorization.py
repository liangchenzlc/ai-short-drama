"""Failed media need fresh explicit authorization; local providers/disposable MySQL."""

import json
from copy import copy, deepcopy

import pytest
import test_agent_conversations as conversation_tests
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from sqlalchemy import func, select
from test_agent_native_tasks import Provider, admit, drain, executor, new_tool, prepared
from test_agent_runtime import MockGateway, normalized
from test_agent_services import runs, send

from short_drama.agent import model_gateway
from short_drama.agent.authorization import digest
from short_drama.agent.model_gateway import serialize_history
from short_drama.agent.native_tasks import collect_native_results
from short_drama.agent.runtime import AgentRuntime
from short_drama.agent.tools import execute_tools
from short_drama.ai import GenerationError
from short_drama.domain import (
    AgentArtifact,
    AgentRun,
    AgentToolCall,
    AgentTurn,
    AIGenerationRecord,
    AsyncTask,
)

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def task_spec(flow):
    with flow.factory() as session:
        step = session.get(AgentRun, flow.run_id).checkpoint["authorization"]["steps"][0]
        parameters = deepcopy(step["parameters"])
        # Video references are server-filled; a new request never overrides them.
        if flow.kind == "video":
            parameters.pop("reference_media_ids", None)
        return {
            "kind": flow.kind,
            "target_id": step["target_id"],
            "model_config_id": step["model_config_id"],
            "count": 1,
            "instructions": "Explicitly approved new paid candidate after the failed request.",
            "parameters": parameters,
        }


def replace_tool(flow, tool_id, name, arguments):
    with flow.factory.begin() as session:
        tool = session.get(AgentToolCall, tool_id)
        raw = json.dumps(arguments)
        tool.tool_name = name
        tool.arguments = {"raw": raw, "parsed": arguments}
        tool.arguments_hash = digest(raw)


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("authorization", ["single", "plan"])
def test_failed_media_settles_without_resubmit_and_new_authorization_has_independent_quota(
    workspace, monkeypatch, kind, authorization
):
    old = prepared(workspace, kind)
    old_tool_id = old.tool_id
    repeated_intent_id = new_tool(old)
    old.tool_id = old_tool_id
    # Both intents came from one durable model segment before any native request.
    # Keep a real protocol history so the failure-summary segment receives results.
    with old.factory.begin() as session:
        history = serialize_history(
            [
                ModelRequest(parts=[UserPromptPart("Generate the approved one media candidate")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "create_candidate",
                            {"step_id": "single", "content": "", "patch": {}},
                            call_id,
                        )
                        for call_id in ("call-1", "call-2")
                    ]
                ),
            ]
        )
        run = session.get(AgentRun, old.run_id)
        checkpoint = deepcopy(run.checkpoint)
        checkpoint["history"] = history
        run.checkpoint = checkpoint
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == old.run_id))
        turn.response = {
            **turn.response,
            "normalized": {"output_kind": "tool_requests", "history": history},
        }
    old_task_id = admit(old)
    meter = "images" if kind == "image" else "videos"
    # An uncertain accepted POST must not become a silent retry. A fresh explicit
    # authorization below is a new possible paid request, with its own evidence.
    failed_provider = Provider(
        kind, error=GenerationError("upstream_unavailable", accepted_unknown=True)
    )
    old_worker = executor(old, failed_provider)
    assert drain(old, old_task_id, old_worker) == "failed"
    assert failed_provider.polls == 0 and len(failed_provider.posts) == 1
    with old.factory() as session:
        run = session.get(AgentRun, old.run_id)
        frozen_authorization = deepcopy(run.checkpoint["authorization"])
        old_budget, old_usage = deepcopy(run.budget), deepcopy(run.usage)
        assert old_budget[meter] == old_usage[meter] == 1
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == old_task_id)
        )
        assert record.status == "unknown" and record.call_no == 1
        assert run.status == "waiting_generation"

    assert collect_native_results(old.factory, old.settings) == 1
    assert collect_native_results(old.factory, old.settings) == 0
    with old.factory() as session:
        tool = session.get(AgentToolCall, old_tool_id)
        assert tool.status == "failed" and tool.result["settled"] is True
        assert tool.result["status"] == "failed" and tool.result["artifact_ids"] == []
        assert tool.result["error"] == {"code": "upstream_unavailable"}
        run = session.get(AgentRun, old.run_id)
        assert run.status == "running" and run.phase == "tools"
        assert run.message_status == "pending" and run.lease_token is None
        assert run.budget == old_budget and run.usage == old_usage
        assert run.checkpoint["authorization"] == frozen_authorization
        assert session.scalar(select(func.count(AgentArtifact.id))) == 0

    # A second creative intent inside the already-consumed old step cannot revive
    # its quantity or allocate another task, even before the assistant closes it.
    execute_tools(old.factory, old.settings, old.run_id)
    with old.factory() as session:
        repeat = session.get(AgentToolCall, repeated_intent_id)
        assert repeat.status == "failed" and repeat.error["code"] == "agent_step_not_authorized"
        run = session.get(AgentRun, old.run_id)
        assert run.checkpoint["pending_results"]["calls"]["call-1"]["status"] == "failed"
        assert run.checkpoint["authorization"] == frozen_authorization
        assert run.budget == old_budget and run.usage == old_usage
        assert session.scalar(select(func.count(AsyncTask.id))) == 1
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 1
        old_version = run.message_version
    # Finish through the real runtime with a durable local model reply, allowing
    # another explicit message in the same private conversation, without stopping.
    monkeypatch.setattr(model_gateway, "serialize_segment_result", lambda result: result)
    closing = normalized()
    closing["output"] = "生成未成功；提交状态待核对。重新生成需要新的明确付费授权。"

    class FailureSummary(MockGateway):
        async def run_segment(self, snapshot, credential, **kwargs):
            assert kwargs["deferred_results"]["calls"]["call-1"]["error"] == {
                "code": "upstream_unavailable"
            }
            return await super().run_segment(snapshot, credential, **kwargs)

    summary = FailureSummary(closing)
    AgentRuntime(old.factory, old.settings, summary).execute_one(old.run_id, old_version)
    assert summary.posts == 1  # Local fake protocol only, never HTTP.
    old_worker.execute(old_task_id, 1)
    assert collect_native_results(old.factory, old.settings) == 0
    assert len(failed_provider.posts) == 1
    with old.factory() as session:
        previous = session.get(AgentRun, old.run_id)
        assert previous.status == "succeeded"  # The failure summary is complete.
        old_final_usage = deepcopy(previous.usage)
        assert previous.usage[meter] == 1

    payload = task_spec(old)
    accepted = send(
        old.factory,
        old.conversation_id,
        old.decision_id,
        key="new-paid-authorization",
        content="已核对旧请求状态，明确授权新增一次可能收费的生成，请创建独立的新候选。",
        mode="generate",
        **({"task": payload} if authorization == "single" else {}),
    )
    fresh = copy(old)
    fresh.run_id = int(accepted.run.id)
    assert fresh.run_id != old.run_id
    with fresh.factory() as session:
        run = session.get(AgentRun, fresh.run_id)
        assert run.conversation_id == old.conversation_id and run.initiated_by == 1
        assert run.usage[meter] == 0
        assert run.budget[meter] == (1 if authorization == "single" else 0)
        assert run.checkpoint["authorization"]["consumed_steps"] == []

    if authorization == "plan":
        plan_tool = new_tool(fresh)
        replace_tool(
            fresh,
            plan_tool,
            "propose_plan",
            {
                "title": "Explicit new media request",
                "summary": "Approve one new paid candidate; the old request stays consumed.",
                "steps": [payload],
            },
        )
        execute_tools(fresh.factory, fresh.settings, fresh.run_id)
        with fresh.factory() as session:
            run, tool = session.get(AgentRun, fresh.run_id), session.get(AgentToolCall, plan_tool)
            assert run.status == "waiting_review" and run.budget[meter] == run.usage[meter] == 0
            assert session.scalar(select(func.count(AsyncTask.id))) == 1
            review = {
                "review_version": tool.review_version,
                "review_hash": tool.review_hash,
                "decision": "approved",
            }
        with fresh.factory() as session:
            approved = runs(session).review(fresh.run_id, plan_tool, review)
            assert approved.mode == "workflow" and approved.budget[meter] == 1
        step_id = "step-1"
    else:
        step_id = "single"

    fresh.tool_id = new_tool(fresh)
    replace_tool(
        fresh, fresh.tool_id, "create_candidate", {"step_id": step_id, "content": "", "patch": {}}
    )
    new_task_id = admit(fresh)
    assert new_task_id != old_task_id
    with fresh.factory() as session:
        run, previous = session.get(AgentRun, fresh.run_id), session.get(AgentRun, old.run_id)
        assert run.usage[meter] == run.budget[meter] == 1
        assert run.checkpoint["authorization"]["admitted_quantities"] == {step_id: 1}
        assert run.checkpoint["authorization"]["consumed_steps"] == [step_id]
        assert previous.budget == old_budget and previous.usage == old_final_usage
        assert previous.checkpoint["authorization"] == frozen_authorization
        assert session.get(AgentToolCall, old_tool_id).generation_task_id == old_task_id
        assert session.get(AgentToolCall, fresh.tool_id).generation_task_id == new_task_id
        assert session.scalar(select(func.count(AsyncTask.id))) == 2
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 2
        assert session.get(AsyncTask, old_task_id).status == "failed"

    successful_provider = Provider(kind)
    assert drain(fresh, new_task_id, executor(fresh, successful_provider)) == "succeeded"
    assert collect_native_results(fresh.factory, fresh.settings) == 1
    assert collect_native_results(fresh.factory, fresh.settings) == 0
    assert len(successful_provider.posts) == len(failed_provider.posts) == 1
    with fresh.factory() as session:
        previous = session.get(AgentRun, old.run_id)
        assert previous.checkpoint["authorization"] == frozen_authorization
        assert previous.usage == old_final_usage and previous.budget == old_budget
        assert session.scalar(select(func.count(AgentArtifact.id))) == 1
        assert len(session.get(AgentToolCall, fresh.tool_id).result["artifact_ids"]) == 1
