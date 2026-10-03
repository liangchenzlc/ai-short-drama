"""Agent/native admission, cancellation and archive contracts on disposable MySQL."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
import test_agent_conversations as conversation_tests
from PIL import Image
from sqlalchemy import func, select
from test_agent_artifacts import linked_asset
from test_agent_conversations import actor
from test_agent_services import runs, send, settings, setup
from test_asset_image_generation import MemoryStorage

from short_drama.agent import native_tasks
from short_drama.agent.artifacts import create_candidate_locked
from short_drama.agent.authorization import digest
from short_drama.agent.native_tasks import collect_native_results
from short_drama.agent.runtime import lock_run
from short_drama.agent.tools import CreateCandidate, execute_tools
from short_drama.ai import GenerationError, GenerationResult
from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.domain import (
    AgentArtifact,
    AgentRun,
    AgentToolCall,
    AgentTurn,
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    Episode,
    EpisodeScript,
    MediaFile,
    Project,
    ShotImage,
    ShotScript,
    User,
)
from short_drama.service.agent_model_service import capability_evidence
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.base import utcnow
from short_drama.service.generation_context_service import GenerationContextService
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.service.shot_script_service import ShotScriptService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace
SCRIPT = "林晚拿起红伞，走进老宅客厅。"


def prepared(workspace, kind="image", count=1):
    factory, p, e, _ = workspace
    cfg = settings()
    conversation_id, decision_id = setup(factory, p, e)
    target_id, media_model = None, None
    if kind in {"extract", "storyboard"}:
        with factory.begin() as session:
            script = EpisodeScript(
                id=next_id(), episode_id=e, position=1, state="confirmed", content=SCRIPT
            )
            session.add(script)
            session.flush()
            session.get(Episode, e).editing_script_id = script.id
    else:
        with factory.begin() as session:
            media_model = AIModelConfig(
                id=next_id(),
                owner_user_id=1,
                service_type=kind,
                name="Native fixture",
                provider="ark" if kind == "image" else "modelhub",
                model_key="fixture" if kind == "image" else "seedance-2.0-mini",
                base_url="https://ark.cn-beijing.volces.com/api/v3"
                if kind == "image"
                else "https://api.modelhub.cc",
            )
            session.add(media_model)
        if kind == "image":
            target_id = linked_asset(factory, p, e)
            from short_drama.domain import Asset

            with factory.begin() as session:
                session.get(Asset, target_id).prompt = "Hero in a blue linen coat."
        else:
            with factory() as session:
                session.info["actor"] = actor(1)
                shot = ShotScriptService(session).create(
                    {"episode_id": e, "position": 1, "script": SCRIPT, "duration_ms": 3000}
                )
            target_id = shot.id
            with factory.begin() as session:
                _, _, _, _, context_hash = GenerationContextService(session).locked_shot_context(
                    target_id
                )
                media = MediaFile(
                    id=next_id(),
                    project_id=p,
                    format_code="image/png",
                    storage_locator=f"minio://{cfg.minio_image_bucket}/frame.png",
                    width=16,
                    height=9,
                )
                session.add(media)
                session.flush()
                session.add(
                    ShotImage(
                        id=next_id(),
                        episode_id=e,
                        shot_id=target_id,
                        media_id=media.id,
                        context_hash=context_hash,
                        aspect="16:9",
                    )
                )
    task = {"kind": kind, "instructions": "Generate approved candidate.", "count": count}
    if media_model:
        task.update(
            target_id=str(target_id),
            model_config_id=str(media_model.id),
            parameters={"target_kind": "asset"}
            if kind == "image"
            else {"resolution": "480p", "duration_ms": 5000},
        )
    accepted = send(factory, conversation_id, decision_id, mode="generate", task=task)
    value = SimpleNamespace(
        factory=factory,
        settings=cfg,
        project_id=p,
        episode_id=e,
        run_id=int(accepted.run.id),
        conversation_id=conversation_id,
        decision_id=decision_id,
        target_id=target_id,
        kind=kind,
    )
    value.tool_id = new_tool(value)
    if kind == "video":
        with factory() as session:
            value.editor_video_settings = deepcopy(
                session.get(ShotScript, target_id).video_settings
            )
    return value


def new_tool(flow):
    with flow.factory.begin() as session:
        run = session.get(AgentRun, flow.run_id)
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == run.id))
        if turn is None:
            turn = AgentTurn(
                id=next_id(),
                run_id=run.id,
                turn_no=1,
                status="succeeded",
                response={
                    "raw": {},
                    "normalized": {"output_kind": "tool_requests"},
                    "applied": True,
                },
            )
            session.add(turn)
            session.flush()
        index = (
            session.scalar(
                select(func.count(AgentToolCall.id)).where(AgentToolCall.run_id == run.id)
            )
            + 1
        )
        args = {"step_id": "single", "content": "", "patch": {}}
        tool = AgentToolCall(
            id=next_id(),
            run_id=run.id,
            turn_id=turn.id,
            call_index=index,
            provider_call_id=f"call-{index}",
            tool_name="create_candidate",
            arguments={"raw": json.dumps(args), "parsed": args},
            arguments_hash=digest(args),
            idempotency_key=digest([run.id, index]),
        )
        session.add(tool)
        run.status, run.phase, run.message_status = "running", "tools", "idle"
        session.flush()
        return tool.id


def admit(flow):
    execute_tools(flow.factory, flow.settings, flow.run_id)
    with flow.factory() as session:
        tool = session.get(AgentToolCall, flow.tool_id)
        assert tool.status == "waiting_generation", tool.error
        return tool.generation_task_id


class Provider:
    def __init__(self, kind, *, deferred=False, error=None):
        self.kind, self.deferred, self.error = kind, deferred, error
        self.posts, self.polls = [], 0

    def validate(self, *_):
        return {}

    def result(self, adapter, count=1):
        if self.kind == "extract":
            text = json.dumps({"schema_version": 1, "items": []})
        elif self.kind == "storyboard":
            text = json.dumps(
                {
                    "shots": [
                        {
                            "title": "Action",
                            "source_excerpt": SCRIPT,
                            "story_beat": "Hero enters",
                            "script": SCRIPT,
                            "duration_ms": 3000,
                            "asset_ids": [],
                        }
                    ]
                }
            )
        else:
            outputs = []
            for _ in range(count):
                if self.kind == "image":
                    stream = BytesIO()
                    Image.new("RGB", (4, 4), "blue").save(stream, "PNG")
                    data = stream.getvalue()
                else:
                    data = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 24
                outputs.append({"base64": base64.b64encode(data).decode()})
            return GenerationResult(status="succeeded", adapter=adapter, outputs=outputs)
        return GenerationResult(
            status="succeeded", adapter=adapter, text=text, finish_reason="stop"
        )

    def submit(self, snapshot, request, credential, *, adapter, **kwargs):
        self.posts.append(deepcopy(request))
        if self.error:
            raise self.error
        if self.deferred:
            return GenerationResult(
                status="submitted", adapter=adapter, provider_task_id="accepted-fixture"
            )
        return self.result(adapter, request.get("parameters", {}).get("count", 1))

    def poll(self, snapshot, identifier, credential, adapter):
        assert identifier == "accepted-fixture"
        self.polls += 1
        return self.result(adapter)


def executor(flow, provider):
    return GenerationExecutionService(flow.factory, flow.settings, provider, MemoryStorage())


def drain(flow, task_id, worker):
    for _ in range(6):
        with flow.factory.begin() as session:
            task = session.get(AsyncTask, task_id)
            if task.status in {"succeeded", "failed", "cancelled"}:
                return task.status
            task.next_run_at = utcnow()
            version = task.message_version
        worker.execute(task_id, version)
        worker.execute(task_id, version)
    pytest.fail("Fixture task did not finish")


@pytest.mark.parametrize("kind,count,batches", [("image", 3, [2, 1]), ("video", 2, [1, 1])])
def test_native_quantities_are_bounded_per_task_and_exactly_match_authorized_count(
    workspace, kind, count, batches
):
    flow = prepared(workspace, kind, count)
    provider = Provider(kind)
    worker = executor(flow, provider)
    for index, quantity in enumerate(batches):
        if index:
            flow.tool_id = new_tool(flow)
        task_id = admit(flow)
        with flow.factory() as session:
            tool = session.get(AgentToolCall, flow.tool_id)
            assert tool.result["quantity"] == quantity
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
            )
            assert record.config_snapshot["agent_managed"] is True
            assert (
                "run_id" not in record.config_snapshot
                and "tool_call_id" not in record.config_snapshot
            )
            if kind == "video":
                assert record.request_data["parameters"]["resolution"] == "480p"
                assert record.request_data["parameters"]["duration_ms"] == 5000
                assert (
                    session.get(ShotScript, flow.target_id).video_settings
                    == flow.editor_video_settings
                )
        assert drain(flow, task_id, worker) == "succeeded"
        assert collect_native_results(flow.factory, flow.settings) == 1
        assert collect_native_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        run = session.get(AgentRun, flow.run_id)
        meter = "images" if kind == "image" else "videos"
        assert run.usage[meter] == count and run.budget[meter] == count
        assert run.checkpoint["authorization"]["admitted_quantities"]["single"] == count
        assert run.checkpoint["authorization"]["consumed_steps"] == ["single"]
        assert session.scalar(select(func.count(AgentArtifact.id))) == count
    flow.tool_id = new_tool(flow)
    execute_tools(flow.factory, flow.settings, flow.run_id)
    with flow.factory() as session:
        assert session.get(AgentToolCall, flow.tool_id).error["code"] == "agent_step_not_authorized"
        assert session.scalar(select(func.count(AsyncTask.id))) == len(batches)
    assert len(provider.posts) == len(batches)


def test_native_admission_failure_rolls_back_quantity_task_record_and_link(workspace, monkeypatch):
    flow = prepared(workspace, count=2)
    original = AIGenerationService.create_locked

    def fail_after_insert(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise WorkflowError("fixture_admission_failed", "Failure after insert", 422)

    monkeypatch.setattr(AIGenerationService, "create_locked", fail_after_insert)
    execute_tools(flow.factory, flow.settings, flow.run_id)
    with flow.factory() as session:
        run, tool = session.get(AgentRun, flow.run_id), session.get(AgentToolCall, flow.tool_id)
        assert run.usage["images"] == 0
        assert run.checkpoint["authorization"].get("admitted_quantities", {}) == {}
        assert tool.generation_task_id is None and tool.status == "failed"
        assert tool.error["code"] == "fixture_admission_failed"
        assert session.scalar(select(func.count(AsyncTask.id))) == 0
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 0


@pytest.mark.parametrize("change", ["stop", "user", "project", "feature"])
def test_new_native_submit_is_denied_after_parent_or_authority_revocation(workspace, change):
    flow = prepared(workspace)
    task_id = admit(flow)
    if change == "stop":
        with flow.factory() as session:
            runs(session).stop(flow.run_id)
    elif change == "feature":
        flow.settings.agent_enabled = False
    else:
        with flow.factory.begin() as session:
            if change == "user":
                session.get(User, 1).status = "disabled"
            else:
                session.get(Project, flow.project_id).archived_at = utcnow()
    provider = Provider("image")
    assert drain(flow, task_id, executor(flow, provider)) == "cancelled"
    assert provider.posts == []


@pytest.mark.parametrize("change", ["stop", "user", "project", "feature"])
def test_revoked_parent_never_reserves_or_inserts_a_new_native_task(workspace, change):
    flow = prepared(workspace)
    if change == "stop":
        with flow.factory() as session:
            runs(session).stop(flow.run_id)
    elif change == "feature":
        flow.settings.agent_enabled = False
    else:
        with flow.factory.begin() as session:
            if change == "user":
                session.get(User, 1).status = "disabled"
            else:
                session.get(Project, flow.project_id).archived_at = utcnow()
    execute_tools(flow.factory, flow.settings, flow.run_id)
    with flow.factory() as session:
        assert session.get(AgentRun, flow.run_id).status == "cancelled"
        assert session.get(AgentRun, flow.run_id).usage["images"] == 0
        assert session.scalar(select(func.count(AsyncTask.id))) == 0
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 0


def test_native_submit_rechecks_stop_after_local_provider_validation(workspace):
    flow = prepared(workspace)
    task_id = admit(flow)

    class StoppingProvider(Provider):
        def validate(self, *_):
            with flow.factory() as session:
                runs(session).stop(flow.run_id)
            return {}

    provider = StoppingProvider("image")
    assert drain(flow, task_id, executor(flow, provider)) == "cancelled"
    assert provider.posts == []


@pytest.mark.parametrize("change", ["stop", "user"])
def test_direct_locked_candidate_bridge_denies_new_effects_after_revocation(workspace, change):
    flow = prepared(workspace)
    if change == "stop":
        with flow.factory() as session:
            runs(session).stop(flow.run_id)
    else:
        with flow.factory.begin() as session:
            session.get(User, 1).status = "disabled"
    with flow.factory.begin() as session:
        _, conversation, run = lock_run(session, flow.run_id)
        tool = session.get(AgentToolCall, flow.tool_id)
        with pytest.raises(BusinessError):
            create_candidate_locked(
                session,
                conversation,
                run,
                tool,
                CreateCandidate(step_id="single"),
                settings=flow.settings,
            )
    with flow.factory() as session:
        assert session.get(AgentRun, flow.run_id).usage["images"] == 0
        assert session.scalar(select(func.count(AsyncTask.id))) == 0


def test_collector_sees_outputs_completed_after_its_repeatable_read_view(workspace, monkeypatch):
    flow = prepared(workspace)
    task_id = admit(flow)
    ready, resume = Event(), Event()
    original = native_tasks.lock_run

    def old_view(session, *args, **kwargs):
        session.scalar(select(AgentToolCall.id).where(AgentToolCall.id == flow.tool_id))
        ready.set()
        assert resume.wait(timeout=10)
        return original(session, *args, **kwargs)

    monkeypatch.setattr(native_tasks, "lock_run", old_view)
    provider = Provider("image")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(collect_native_results, flow.factory, flow.settings)
        try:
            assert ready.wait(timeout=5)
            assert drain(flow, task_id, executor(flow, provider)) == "succeeded"
        finally:
            resume.set()
        assert future.result(timeout=10) == 1
    with flow.factory() as session:
        tool = session.get(AgentToolCall, flow.tool_id)
        assert tool.result["settled"] and len(tool.result["artifact_ids"]) == 1
        assert session.scalar(select(func.count(AgentArtifact.id))) == 1


def test_accepted_native_poll_and_save_survive_parent_cancellation_without_new_submit(workspace):
    flow = prepared(workspace, "video")
    task_id = admit(flow)
    provider = Provider("video", deferred=True)
    worker = executor(flow, provider)
    worker.execute(task_id, 1)
    assert len(provider.posts) == 1
    with flow.factory() as session:
        runs(session).stop(flow.run_id)
    assert drain(flow, task_id, worker) == "succeeded"
    assert collect_native_results(flow.factory, flow.settings) == 1
    assert collect_native_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        run, tool = session.get(AgentRun, flow.run_id), session.get(AgentToolCall, flow.tool_id)
        assert run.status == "cancelled" and tool.status == "cancelled"
        assert tool.result["settled"] and len(tool.result["artifact_ids"]) == 1
        assert session.scalar(select(func.count(AgentArtifact.id))) == 1
    assert len(provider.posts) == 1 and provider.polls == 1


@pytest.mark.parametrize("kind", ["extract", "storyboard"])
def test_native_text_collects_business_candidate_without_adoption_or_reverification(
    workspace, kind
):
    flow = prepared(workspace, kind)
    task_id = admit(flow)
    provider = Provider(kind)
    assert drain(flow, task_id, executor(flow, provider)) == "succeeded"
    assert collect_native_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        artifact = session.scalar(select(AgentArtifact))
        assert (
            artifact.kind
            == {"extract": "extraction_candidate", "storyboard": "storyboard_candidate"}[kind]
        )
        assert artifact.generation_task_id == task_id and artifact.status == "ready"
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        assert record.response_data["business_result"]
        assert session.get(Episode, flow.episode_id).storyboard_version == 1
        assert session.scalar(select(func.count(ShotScript.id))) == 0
        assert capability_evidence(session.get(AIModelConfig, flow.decision_id))["tool_calling"]


def test_agent_native_protocol_failure_never_falls_back_or_allows_generic_retry(workspace):
    flow = prepared(workspace, "extract")
    task_id = admit(flow)
    provider = Provider(
        "extract", error=GenerationError("protocol_mismatch", protocol_mismatch=True)
    )
    assert drain(flow, task_id, executor(flow, provider)) == "failed"
    with flow.factory() as session:
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 1
        session.info["actor"] = actor(1)
        session.rollback()
        with pytest.raises(WorkflowError) as retry:
            AIGenerationService(session, flow.settings).retry(task_id, {}, "generic-retry")
        assert retry.value.code == "agent_retry_requires_plan"
    assert len(provider.posts) == 1
