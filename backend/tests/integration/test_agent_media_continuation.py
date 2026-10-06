"""Approved media adoption/continuation on disposable MySQL and local protocols."""

from copy import deepcopy

import pytest
import test_agent_conversations as conversation_tests
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import func, select
from test_agent_artifacts import adopt_body
from test_agent_conversations import actor
from test_agent_native_tasks import Provider, admit, drain, executor, new_tool, prepared
from test_agent_runtime import MockGateway
from test_agent_services import runs, send

from short_drama.agent import model_gateway
from short_drama.agent.model_gateway import deserialize_history, serialize_history
from short_drama.agent.native_tasks import collect_native_results
from short_drama.agent.runtime import AgentRuntime
from short_drama.agent.tools import execute_tools
from short_drama.core.exceptions import Conflict
from short_drama.domain import (
    AgentArtifact,
    AgentRun,
    AgentToolCall,
    AIGenerationRecord,
    AIModelConfig,
    Asset,
    AsyncTask,
    ShotImage,
)
from short_drama.service.agent_artifact_service import AgentArtifactService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def seed(workspace, kind):
    flow = prepared(workspace, kind)
    with flow.factory() as session:
        flow.bootstrap_run_id = flow.run_id
        flow.media_model_id = session.get(AgentRun, flow.run_id).checkpoint["authorization"][
            "steps"
        ][0]["model_config_id"]
        session.rollback()
        runs(session).stop(flow.run_id)
    return flow


def segment(flow, monkeypatch, name, args, call_id):
    """Exercise real durable hooks and tool execution using a single fake decision."""
    with flow.factory() as session:
        run = session.get(AgentRun, flow.run_id)
        version = run.message_version
        previous = deepcopy(run.checkpoint.get("history"))
        pending = deepcopy(run.checkpoint.get("pending_results")) if previous else None
        names = {
            tool.provider_call_id: tool.tool_name
            for tool in session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id))
        }
    messages = (
        deserialize_history(previous)
        if previous
        else [ModelRequest(parts=[UserPromptPart("Execute the explicitly approved media work.")])]
    )
    if pending and pending["calls"]:
        messages.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(names[key], result, key)
                    for key, result in pending["calls"].items()
                ]
            )
        )
    messages.append(ModelResponse(parts=[ToolCallPart(name, args, call_id)]))
    result = {
        "output_kind": "tool_requests",
        "output": {
            "calls": [{"tool_call_id": call_id, "tool_name": name, "args": args}],
            "approvals": [],
        },
        "history": serialize_history(messages),
        "usage": {"output_tokens": 1, "usage_reported": True, "output_tokens_reported": True},
        "protocol": "openai_chat.v1",
        "requests": 1,
    }

    class Gateway(MockGateway):
        async def run_segment(self, snapshot, credential, **kwargs):
            assert kwargs["history"] == previous
            assert kwargs["deferred_results"] == pending
            return await super().run_segment(snapshot, credential, **kwargs)

    monkeypatch.setattr(model_gateway, "serialize_segment_result", lambda value: value)
    gateway = Gateway(result)
    runtime = AgentRuntime(flow.factory, flow.settings, gateway)
    runtime.execute_one(flow.run_id, version)
    runtime.execute_one(flow.run_id, version)  # duplicate delivery must not decide again
    assert gateway.posts == 1
    with flow.factory() as session:
        tool = session.scalar(
            select(AgentToolCall).where(
                AgentToolCall.run_id == flow.run_id, AgentToolCall.provider_call_id == call_id
            )
        )
        assert tool is not None
        return tool.id


def approve(flow, monkeypatch, tasks):
    accepted = send(
        flow.factory,
        flow.conversation_id,
        flow.decision_id,
        key="media-plan",
        content="Generate these media candidates after reviewing the plan.",
        mode="generate",
    )
    flow.run_id = int(accepted.run.id)
    tool_id = segment(
        flow,
        monkeypatch,
        "propose_plan",
        {
            "title": "Two media candidates",
            "summary": "Adopt the first, then continue.",
            "steps": tasks,
        },
        "media-plan-call",
    )
    with flow.factory() as session:
        tool = session.get(AgentToolCall, tool_id)
        assert tool.status == "waiting_review"
        review = {
            "review_version": tool.review_version,
            "review_hash": tool.review_hash,
            "decision": "approved",
        }
        session.rollback()
        runs(session).review(
            flow.run_id,
            tool_id,
            review,
        )
    execute_tools(flow.factory, flow.settings, flow.run_id)


def complete_first(flow, monkeypatch):
    flow.tool_id = segment(
        flow, monkeypatch, "create_candidate", {"step_id": "step-1"}, "first-media"
    )
    with flow.factory() as session:
        tool = session.get(AgentToolCall, flow.tool_id)
        assert tool.status == "waiting_generation", tool.error
        task_id = tool.generation_task_id
    provider = Provider("image")
    assert drain(flow, task_id, executor(flow, provider)) == "succeeded"
    assert len(provider.posts) == 1
    assert collect_native_results(flow.factory, flow.settings) == 1
    assert collect_native_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        run = session.get(AgentRun, flow.run_id)
        assert run.status == "waiting_review"
        artifact_id = int(run.checkpoint["awaiting_artifacts"][0])
        scope = deepcopy(run.checkpoint["authorization"])
        session.rollback()
        session.info["actor"] = actor(1)
        service = AgentArtifactService(session, flow.settings)
        detail = service.get(flow.project_id, flow.episode_id, artifact_id)
        applied = service.adopt(
            flow.project_id,
            flow.episode_id,
            artifact_id,
            {**adopt_body(detail), "confirm_shared": True},
        )
    return artifact_id, applied, scope, provider


def test_media_adoption_continues_only_its_run_then_admits_next_approved_image(
    workspace, monkeypatch
):
    flow = seed(workspace, "image")
    task = {
        "kind": "image",
        "target_id": str(flow.target_id),
        "model_config_id": flow.media_model_id,
        "instructions": "An approved hero image.",
        "parameters": {"target_kind": "asset"},
    }
    approve(flow, monkeypatch, [task, {**task, "instructions": "A second approved hero image."}])
    artifact_id, applied, frozen, provider = complete_first(flow, monkeypatch)
    payload = {"artifact_id": artifact_id, "artifact_row_version": applied["row_version"]}
    with flow.factory() as session:
        with pytest.raises(Conflict):
            runs(session).continue_after_adoption(flow.bootstrap_run_id, payload)
        with pytest.raises(Conflict):
            runs(session).continue_after_adoption(
                flow.run_id, {**payload, "artifact_id": next_id()}
            )
        resumed = runs(session).continue_after_adoption(flow.run_id, payload)
        replay = runs(session).continue_after_adoption(flow.run_id, payload)
        assert replay.row_version == resumed.row_version and resumed.phase == "model"
    with flow.factory() as session:
        run = session.get(AgentRun, flow.run_id)
        authorization = run.checkpoint["authorization"]
        assert authorization["approved_plan"] == frozen["approved_plan"]
        assert authorization["steps"][1]["parameters"] == frozen["steps"][1]["parameters"]
        assert (
            str(authorization["steps"][1]["source"]["target_row_version"])
            == applied["apply_receipt"]["target_row_version"]
        )
        assert run.usage["images"] == 1 and run.budget["images"] == 2
        assert session.get(AgentRun, flow.bootstrap_run_id).status == "cancelled"
        assert session.get(Asset, flow.target_id).media_id == int(applied["media_id"])
    flow.tool_id = segment(
        flow, monkeypatch, "create_candidate", {"step_id": "step-2"}, "next-approved-image"
    )
    with flow.factory() as session:
        tool = session.get(AgentToolCall, flow.tool_id)
        assert tool.status == "waiting_generation", tool.error
        task_id = tool.generation_task_id
    assert drain(flow, task_id, executor(flow, provider)) == "succeeded"
    assert collect_native_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        run = session.get(AgentRun, flow.run_id)
        assert run.status == "waiting_review"
        assert run.usage["images"] == run.budget["images"] == 2
        assert run.checkpoint["authorization"]["consumed_steps"] == ["step-1", "step-2"]
        assert str(artifact_id) not in run.checkpoint["awaiting_artifacts"]
        assert session.scalar(select(func.count(AsyncTask.id))) == 2
        assert session.scalar(select(func.count(AgentArtifact.id))) == 2
        assert session.get(Asset, flow.target_id).media_id == int(applied["media_id"])
    assert len(provider.posts) == 2


def test_adopted_replacement_image_cannot_change_old_video_authority(workspace, monkeypatch):
    flow = seed(workspace, "video")
    with flow.factory.begin() as session:
        image_model = AIModelConfig(
            id=next_id(),
            owner_user_id=1,
            service_type="image",
            name="Image fixture",
            provider="ark",
            model_key="fixture",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )
        session.add(image_model)
    image_task = {
        "kind": "image",
        "target_id": str(flow.target_id),
        "model_config_id": str(image_model.id),
        "instructions": "An approved replacement shot image.",
        "parameters": {"target_kind": "shot"},
    }
    video_task = {
        "kind": "video",
        "target_id": str(flow.target_id),
        "model_config_id": flow.media_model_id,
        "instructions": "An approved video with the reviewed reference.",
        "parameters": {"resolution": "480p", "duration_ms": 5000},
    }
    approve(flow, monkeypatch, [image_task, video_task])
    artifact_id, applied, frozen, _ = complete_first(flow, monkeypatch)
    reviewed_video = frozen["steps"][1]
    assert reviewed_video["source"]["reference_media_id"] != applied["media_id"]
    with flow.factory() as session:
        runs(session).continue_after_adoption(
            flow.run_id,
            {"artifact_id": artifact_id, "artifact_row_version": applied["row_version"]},
        )
    rejected_tool = segment(
        flow, monkeypatch, "create_candidate", {"step_id": "step-2"}, "old-approved-video"
    )
    with flow.factory() as session:
        tool = session.get(AgentToolCall, rejected_tool)
        assert tool.status == "failed" and tool.error == {"code": "shot_version_conflict"}
        assert tool.generation_task_id is None
        run = session.get(AgentRun, flow.run_id)
        scope = run.checkpoint["authorization"]
        assert scope["approved_plan"] == frozen["approved_plan"]
        assert (
            scope["steps"][1]["source"]["reference_media_id"]
            == reviewed_video["source"]["reference_media_id"]
        )
        assert (
            scope["steps"][1]["source"]["context_hash"] == reviewed_video["source"]["context_hash"]
        )
        assert scope["steps"][1]["parameters"] == reviewed_video["parameters"]
        assert scope["consumed_steps"] == ["step-1"] and run.usage["videos"] == 0
        assert session.scalar(select(func.count(AsyncTask.id))) == 1
        assert session.scalar(select(ShotImage.media_id)) == int(applied["media_id"])
        old_run_id = run.id
        session.rollback()
        runs(session).stop(old_run_id)
    # A fresh explicit user authorization freezes the newly adopted image instead.
    accepted = send(
        flow.factory,
        flow.conversation_id,
        flow.decision_id,
        key="fresh-video-reference",
        content="Generate one video from the newly adopted image.",
        mode="generate",
        task=video_task,
    )
    flow.run_id = int(accepted.run.id)
    assert flow.run_id != old_run_id
    flow.tool_id = new_tool(flow)
    task_id = admit(flow)
    with flow.factory() as session:
        run = session.get(AgentRun, flow.run_id)
        assert (
            run.checkpoint["authorization"]["steps"][0]["source"]["reference_media_id"]
            == applied["media_id"]
        )
        assert run.usage["videos"] == run.budget["videos"] == 1
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        assert record.request_data["source"]["reference_media_id"] == applied["media_id"]
        assert record.request_data["parameters"]["resolution"] == "480p"
        assert record.request_data["parameters"]["duration_ms"] == 5000
        assert session.get(AgentRun, old_run_id).usage["videos"] == 0
    video_provider = Provider("video")
    assert drain(flow, task_id, executor(flow, video_provider)) == "succeeded"
    assert collect_native_results(flow.factory, flow.settings) == 1
    assert len(video_provider.posts) == 1
