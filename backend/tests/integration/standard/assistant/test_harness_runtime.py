"""MySQL durability with the real graph and LangChain, local model protocol only."""

import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.harness.checkpointer import MySQLCheckpointSaver
from short_drama.agent.harness.runtime import HarnessRuntime
from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.agent.resources.resolver import ResourceResolver
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.core.time import utcnow
from short_drama.domain.assistant.execution.graph import AgentGraphCheckpoint, AgentGraphWrite
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.assistant.execution.graph import AgentGraphService
from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    subject_assets,
    task_actor,
    task_service,
)
from tests.unit.standard.agent.test_agent_model_gateway import provider as provider
from tests.unit.standard.agent.test_agent_model_gateway import response, sse_response
from tests.unit.standard.agent.test_harness_resources import png

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def configured(workspace, provider):
    factory, settings, model = workspace
    base, state = provider
    settings.generation_allowed_hosts = ["127.0.0.1"]
    settings.generation_max_response_bytes = 1024 * 1024
    with factory() as session:
        session.info["actor"] = task_actor()
        AIModelConfigService(session).update(
            model, {"row_version": 1, "base_url": base + "/chat/completions"}
        )
    return state


def submit_review(runtime, accepted, state, *, text="", report=None):
    # This fixture checks the submitted answer only. Execution evidence is
    # asserted by each scenario; semantic review uses a real model in live tests.
    report = report or {
        "intent": "discussion",
        "requirements": [
            {
                "key": "answer",
                "description": "提供讨论答复",
                "kind": "answer",
                "source_message_id": str(accepted.message.id),
                "source_quote": accepted.message.content[:100],
                "satisfied": True,
                "answer_quote": text[:2000],
            }
        ],
    }
    with runtime.factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        frozen = {
            item["key"]
            for item in task.effective_state.get("task_contract", {}).get("requirements", [])
        }
    if "requirements" in report:
        fields = {"key", "satisfied", "evidence_operation_ids", "answer_quote", "reason"}
        report = {
            "intent": report["intent"],
            "checks": [
                {key: value for key, value in item.items() if key in fields}
                for item in report["requirements"]
                if item["key"] in frozen
            ],
        }
    state["responses"] = [
        (200, response("chat", calls=[("review", "submit_completion_review", report)]))
    ]
    with runtime.factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)


def context_frame(wire):
    for message in reversed(wire["messages"]):
        content = message.get("content")
        if isinstance(content, str) and content.startswith("{"):
            frame = json.loads(content)
            if "inputs" in frame:
                return frame
    raise AssertionError("Expected a dynamic context frame")


def advance(runtime, accepted, state, *, calls=None, text="", finish=True):
    state["responses"] = [(200, sse_response("chat", calls=calls, text=text), "text/event-stream")]
    with runtime.factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)
    wire = state["calls"][-1][2]
    if text and not calls and finish:
        with runtime.factory() as session:
            candidate = session.get(AgentRun, accepted.run.id).checkpoint.get(
                "completion_candidate"
            )
        if candidate:
            submit_review(runtime, accepted, state, text=text)
    return wire


def pause_with_truncated_response(runtime, accepted, state):
    state["responses"] = [
        (
            200,
            sse_response("chat", text="待续").replace(
                b'"finish_reason": "stop"', b'"finish_reason": "length"'
            ),
            "text/event-stream",
        )
    ]
    with runtime.factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)
    with runtime.factory() as session:
        assert session.get(AgentTask, accepted.task.id).status == "paused_error"


def test_stop_receipt_remains_replayable_after_policy_changes(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "stop-receipt")
        assert stopped.task.status == "stopped" and stopped.run.status == "cancelled"
        settings.agent_stop_at_boundary = False
        replay = service.control_task(accepted.task.id, {"kind": "stop"}, "stop-receipt")
        assert replay.control_id == stopped.control_id
        with pytest.raises(Conflict):
            service.control_task(
                accepted.task.id,
                {"kind": "stop", "expected_control_seq": "1"},
                "stop-receipt",
            )
        with pytest.raises(WorkflowError) as unavailable:
            service.control_task(accepted.task.id, {"kind": "stop"}, "different-stop")
        assert unavailable.value.code == "assistant_stop_unavailable"
        settings.agent_stop_at_boundary = True
        body = {"task_id": accepted.task.id, "content": "补充要求并继续"}
        continued = service.send_task_message(conversation.id, body, "continue-message")
        replayed_message = service.send_task_message(conversation.id, body, "continue-message")
        assert continued.task.id == accepted.task.id and continued.run.id != accepted.run.id
        assert replayed_message.message.id == continued.message.id
        assert replayed_message.run.id == continued.run.id
        replayed_stop = service.control_task(accepted.task.id, {"kind": "stop"}, "stop-receipt")
        assert replayed_stop.control_id == stopped.control_id
        assert replayed_stop.run.id == continued.run.id and replayed_stop.run.status == "queued"
        with pytest.raises(Conflict):
            service.send_task_message(
                conversation.id, {**body, "content": "不同的要求"}, "continue-message"
            )


def test_graph_tool_failure_correction_and_duplicate_delivery(task_workspace, provider):
    factory, settings, model = task_workspace
    base, state = provider
    settings.generation_allowed_hosts = ["127.0.0.1"]
    settings.generation_max_response_bytes = 1024 * 1024
    with factory() as session:
        session.info["actor"] = task_actor()
        AIModelConfigService(session).update(
            model, {"row_version": 1, "base_url": base + "/chat/completions"}
        )
    _conversation, accepted = new_task(task_workspace)
    state["responses"] = [
        (
            200,
            sse_response(
                "chat",
                calls=[("invalid", "read_resource", {"ref_id": "missing", "untrusted": "private"})],
            ),
            "text/event-stream",
        ),
        (
            200,
            sse_response(
                "chat",
                calls=[("bad", "read_resource", {"ref_id": "missing", "purpose": "读取资料"})],
            ),
            "text/event-stream",
        ),
        (
            200,
            sse_response("chat", calls=[("search", "search_skills", {"query": ""})]),
            "text/event-stream",
        ),
        (200, sse_response("chat", text="已核对任务目录并完成节奏建议"), "text/event-stream"),
        (
            200,
            response(
                "chat",
                calls=[
                    (
                        "review",
                        "submit_completion_review",
                        {
                            "intent": "discussion",
                            "additional_requirements": [
                                {
                                    "key": "answer",
                                    "description": "讨论",
                                    "kind": "answer",
                                    "source_message_id": str(accepted.message.id),
                                    "source_quote": accepted.message.content,
                                    "satisfied": True,
                                    "answer_quote": "节奏建议",
                                }
                            ],
                        },
                    )
                ],
            ),
        ),
    ]
    runtime = HarnessRuntime(factory, settings)
    versions = []
    for _ in range(5):
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.checkpoint["engine"] == ENGINE_VERSION
            versions.append(run.message_version)
        runtime.execute_one(accepted.run.id, versions[-1])
        before = len(state["calls"])
        runtime.execute_one(accepted.run.id, versions[-1])
        assert len(state["calls"]) == before
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert session.get(AgentTask, accepted.task.id).status == "succeeded"
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert [item.status for item in sorted(operations, key=lambda item: item.id)] == [
            "failed",
            "failed",
            "succeeded",
        ]
        error = min(operations, key=lambda item: item.id).error
        assert error["code"] == "invalid_tool_arguments"
        fields = error["details"]["validation_errors"]
        assert any(item["loc"] == ["purpose"] and item["type"] == "missing" for item in fields)
        assert "private" not in json.dumps(fields)
        assert all("input" not in item and "ctx" not in item for item in fields)
        calls = session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id)).all()
        assert all(item.result["codec"] == "agent.operation-ref" for item in calls)
        checkpoints = session.scalars(select(AgentGraphCheckpoint)).all()
        writes = session.scalars(select(AgentGraphWrite)).all()
        assert checkpoints and writes
        for row in checkpoints:
            assert_reference_payload(row.checkpoint)
            assert len(json.dumps(row.checkpoint)) < 8192
            assert "context_frame" not in json.dumps(row.checkpoint)
        for row in writes:
            assert_reference_payload(row.payload)
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        # A discussion without confirmed requirements publishes directly under evidence.v3.
        assert len(turns) == 4
        assert all(item.response["applied"] for item in turns)
    assert len(state["calls"]) == 4
    assert "前置依赖" in state["calls"][0][2]["messages"][-1]["content"]
    assert "missing" in json.dumps(state["calls"][1][2])
    assert "search_skills" in json.dumps(state["calls"][2][2])
    assert any(
        item["role"] == "tool" and "validation_errors" in item["content"]
        for item in state["calls"][1][2]["messages"]
    )
    assert any(
        item["role"] == "tool" and "read_skill_resource" in item["content"]
        for item in state["calls"][2][2]["messages"]
    )


def test_harness_advances_beyond_former_cumulative_limits(task_workspace, provider):
    from short_drama.domain.assistant.conversations.models import AgentMessage

    state = configured(task_workspace, provider)
    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.usage = {**run.usage, "output_tokens": 131073, "active_seconds": 1801}
        session.get(AgentMessage, accepted.message.id).content += (
            "。依次读取这些线索：" + "铜钥匙在旧车站。" * 60
        )
    runtime = HarnessRuntime(factory, settings)
    for index in range(40):
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"page-{index}-{part}",
                    "read_conversation_history",
                    {
                        "from_seq": 1,
                        "to_seq": 1,
                        "character_offset": index * 4 + part,
                        "character_limit": 1,
                    },
                )
                for part in range(4)
            ],
        )
    advance(runtime, accepted, state, text="长任务已完成")
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert run.usage["decision_calls"] == 41
        assert run.usage["tool_calls"] == 160
        assert run.usage["output_tokens"] > 131072 and run.usage["active_seconds"] > 1800
        assert len(state["calls"]) == 41


def test_selected_image_directory_provides_complete_read_identity_and_ephemeral_pixels(
    task_workspace,
    provider,
):
    import hashlib

    from short_drama.agent.context.subjects import subject_key
    from short_drama.domain import MediaFile
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        image_group_workspace,
    )
    from tests.unit.standard.agent.test_harness_resources import ObjectStore

    state = configured(task_workspace, provider)
    reference, groups = image_group_workspace(task_workspace)
    factory, settings, model = task_workspace
    storage = ObjectStore()
    item = groups[0]["items"][0]
    with factory.begin() as session:
        media = session.get(MediaFile, int(item["media_id"]))
        media.byte_size = len(png())
        media.checksum_sha256 = hashlib.sha256(png()).hexdigest()
        storage.objects[media.storage_locator] = png()
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "分析选入场景的画面细节",
                "model_config_id": model,
                "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
            },
            uuid4().hex,
        )
    key = subject_key(
        reference["project_id"],
        reference["source_kind"],
        reference["source_id"],
        reference["kind"],
        reference["id"],
    )
    runtime = HarnessRuntime(factory, settings, resolver_factory=lambda: ResourceResolver(storage))
    advance(
        runtime,
        accepted,
        state,
        calls=[("catalog", "read_image_group", {"subject_key": key, "view": "current"})],
    )
    with factory() as session:
        catalog = session.scalar(
            select(AgentOperation.result).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "read_image_group",
            )
        )
        arguments = json.loads(catalog["content"])["groups"][0]["items"][0]["read_content_args"]
        assert arguments == {
            "subject_key": key,
            "group_id": groups[0]["id"],
            **{key: item[key] for key in ("media_id",)},
            "item_id": item["id"],
        }
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "missing-slot",
                "read_image_content",
                {"media_id": item["media_id"], "purpose": "辨认画面"},
            )
        ],
    )
    assert storage.reads == 0
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "bad-crop",
                "read_image_content",
                {
                    **arguments,
                    "purpose": "辨认局部画面",
                    "crop": [0, 0, 1000, 1000],
                },
            )
        ],
    )
    failed = advance(runtime, accepted, state, calls=[("diagnose", "get_generation_policy", {})])
    errors = [
        json.loads(message["content"])
        for message in failed["messages"]
        if message["role"] == "tool"
        and isinstance(message["content"], str)
        and "agent_resource_crop" in message["content"]
    ]
    assert errors and errors[-1]["pixels_delivered"] is False
    assert errors[-1]["error"]["details"] == {"width": 64, "height": 40}
    with factory() as session:
        assert not session.get(AgentTask, accepted.task.id).effective_state["media_evidence"]
    advance(
        runtime,
        accepted,
        state,
        calls=[("pixels", "read_image_content", {**arguments, "purpose": "辨认画面"})],
    )
    body = advance(runtime, accepted, state, text="实际画面为红色。")
    assert "data:image/jpeg;base64," in json.dumps(body)
    with factory() as session:
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)).all()
        for turn in turns:
            assert_reference_payload(turn.request_messages)
        assert session.get(AgentTask, accepted.task.id).status == "succeeded"


def test_skill_progression_compaction_and_ephemeral_pixels(task_workspace, provider, monkeypatch):
    import base64
    import io

    import short_drama.agent.context.transcript as transcript
    import short_drama.agent.skill.runtime as skill_runtime
    from short_drama.service.skills.catalog.skill import AgentSkillService

    factory, settings, model = task_workspace
    state = configured(task_workspace, provider)
    settings.minio_image_bucket = "fixture-skills"
    storage = _Storage(factory)
    body = "先核对角色动机，再核对线索因果。正文只加载一次。"
    skill = create_skill(
        (*task_workspace, storage),
        instructions=body,
        resources=[
            {"path": "references/long.md", "content": "必须保留的线索。" * 600},
            {"path": "references/check.md", "content": "检查两名角色和三个分镜。"},
            {
                "path": "assets/example.png",
                "kind": "image",
                "encoding": "base64",
                "content": base64.b64encode(png()).decode(),
            },
        ],
        required_tools=["read_resource"],
    )

    class Resources:
        @contextmanager
        def open(self, uri):
            bucket, key = uri.removeprefix("minio://").split("/", 1)
            with storage.open(bucket, key) as stream:
                yield stream

        def freeze_agent_resource(self, data, *, invocation_id, content_type):
            key = f"derived/{uuid4().hex}"
            storage.put(settings.minio_image_bucket, key, io.BytesIO(data), len(data), content_type)
            return SimpleNamespace(storage_locator=f"minio://{settings.minio_image_bucket}/{key}")

    monkeypatch.setattr(
        skill_runtime,
        "read_resource_bytes",
        lambda configured, manifest: AgentSkillService(None, configured, storage)._resource_bytes(
            manifest
        ),
    )
    monkeypatch.setattr(transcript, "RECENT_TOOL_TOKENS", 200)
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "依据雨夜方法，逐段核对线索资料、检查清单和示例图片，然后给出建议。",
                "model_config_id": model,
                "skills": [{"id": skill.id, "content_version": skill.content_version}],
            },
            uuid4().hex,
        )
        assert accepted.task.interruptions == {"stop": True, "redirect": False, "resume": False}
    runtime = HarnessRuntime(
        factory, settings, resolver_factory=lambda: ResourceResolver(Resources(), settings)
    )
    load = {
        "name": skill.name,
        "content_version": str(skill.content_version),
        "purpose": "核对线索需要此 SOP",
    }
    initial = advance(
        runtime,
        accepted,
        state,
        calls=[("load1", "load_skill", load)],
        text="先加载核对线索所需的方法",
    )
    assert body not in json.dumps(initial, ensure_ascii=False) and storage.reads == 3
    repeated = advance(runtime, accepted, state, calls=[("load2", "load_skill", load)])
    assert body in json.dumps(repeated, ensure_ascii=False)
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        activation = task.effective_state["skill_activations"][0]["id"]
        assert "instructions" not in json.dumps(task.effective_state)
    for index in range(3):
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"text{index}",
                    "read_skill_resource",
                    {
                        "activation_id": activation,
                        "path": "references/long.md",
                        "offset": index * 100,
                        "limit": 100,
                        "purpose": "逐段核对线索",
                    },
                )
            ],
        )
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "checklist",
                "read_skill_resource",
                {
                    "activation_id": activation,
                    "path": "references/check.md",
                    "purpose": "读取检查规则",
                },
            )
        ],
    )
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "image",
                "read_skill_resource",
                {
                    "activation_id": activation,
                    "path": "assets/example.png",
                    "purpose": "核对示例画面",
                },
            )
        ],
    )
    pixels = advance(runtime, accepted, state, calls=[("again", "search_skills", {})])
    assert "data:image/jpeg;base64," in json.dumps(pixels)
    final = advance(runtime, accepted, state, text="已核对资料、检查清单与示例图，完成线索建议。")
    assert "data:image/" not in json.dumps(final)
    assert body in json.dumps(final, ensure_ascii=False)
    progress = context_frame(final)["tool_progress"]
    assert progress["items"] and progress["estimated_tokens"] <= progress["quota_tokens"]
    reads = [read for item in progress["items"] for read in item["reads"]]
    assert any(
        read["path"] == "references/long.md" and "必须保留的线索" in read["excerpt"]["text"]
        for read in reads
    )
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id)
        ).all()
        assert all(item.status == "succeeded" for item in operations), [
            (i.tool_name, i.error) for i in operations
        ]
        loaded = [item for item in operations if item.tool_name == "load_skill"]
        assert loaded[0].result["instructions"] == body
        assert "instructions" not in loaded[1].result and loaded[1].result["already_loaded"]
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)).all()
        for turn in turns:
            assert_reference_payload(turn.request_messages)
            assert body not in json.dumps(turn.request_messages, ensure_ascii=False)
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert run.checkpoint["context_metrics"]["dropped_groups"] >= 1
        assert run.checkpoint["context_metrics"]["retained_skills"] == 1


def test_raw_response_recovery_does_not_send_again(task_workspace, provider, monkeypatch):
    state = configured(task_workspace, provider)
    _conversation, accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    runtime = HarnessRuntime(factory, settings)
    original = runtime.store.save_result

    def interrupted(*args, **kwargs):
        raise RuntimeError("simulate worker death after durable raw response")

    monkeypatch.setattr(runtime.store, "save_result", interrupted)
    with pytest.raises(RuntimeError, match="database recovery"):
        advance(runtime, accepted, state, text="完整任务结论")
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.lease_until = utcnow() - timedelta(seconds=1)
    assert recover(factory, settings) == 1
    monkeypatch.setattr(runtime.store, "save_result", original)
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)
    assert len(state["calls"]) == 1
    submit_review(runtime, accepted, state, text="完整任务结论")
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert (
            session.scalar(
                select(AgentTurn).where(AgentTurn.run_id == accepted.run.id).order_by(AgentTurn.id)
            ).response["normalized"]["requests"]
            == 0
        )


def test_autonomous_standard_save_uses_scope_versions_and_real_receipts(
    task_workspace, provider, monkeypatch
):
    import short_drama.agent.context.assembly as assembly
    from short_drama.domain import Asset, Project, ShotScript

    monkeypatch.setattr(assembly, "MAX_OPERATION_RECEIPTS", 20)
    state = configured(task_workspace, provider)
    references = subject_assets(task_workspace)
    reference = references[0]
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "这场雨夜戏还缺一个乘客的视角和开场画面，帮我补齐到作品里。",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
            },
            uuid4().hex,
        )
        version = session.get(Project, int(reference["project_id"])).storyboard_version
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, calls=[("directory", "describe_creation_target", {})])
    advance(
        runtime,
        accepted,
        state,
        calls=[("enable", "enable_tool_group", {"group": "standard_write"})],
    )
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "save",
                "apply_standard_patch",
                {
                    "patch": {
                        "storyboard_version": str(version),
                        "actions": [
                            {
                                "action": "create_asset",
                                "key": "passenger",
                                "values": {"kind": "character", "name": "雨夜乘客"},
                            },
                            {
                                "action": "update_asset",
                                "asset": reference["id"],
                                "patch": {
                                    "row_version": reference["revision"],
                                    "description": "任务写入后可核对的角色设定",
                                },
                            },
                            {
                                "action": "create_shot",
                                "key": "opening",
                                "values": {"title": "雨夜站台"},
                                "assets": ["$passenger"],
                            },
                        ],
                    }
                },
            )
        ],
    )
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "apply_standard_patch",
            )
        )
        assert operation.status == "succeeded", operation.error
        assert len(operation.changes) == 3 and len(operation.result_refs) == 3
        assert session.scalar(select(Asset.id).where(Asset.name == "雨夜乘客"))
        assert session.scalar(select(ShotScript.id).where(ShotScript.title == "雨夜站台"))
        source_id = str(operation.id)
        assert operation.result["receipt_operation_id"] == source_id
    # Recent reads must not displace the original save batch from its directory.
    for index in range(3):
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"read-{index}-{part}",
                    "read_selected_subjects",
                    {
                        "ids": [reference["id"]],
                        "character_offset": index * 8 + part,
                        "character_limit": 1,
                    },
                )
                for part in range(8)
            ],
        )
    body = advance(
        runtime,
        accepted,
        state,
        calls=[("read-saved", "read_task_results", {"operation_id": source_id})],
    )
    frame = context_frame(body)
    assert all(item["id"] != source_id for item in frame["operation_receipts"])
    assert frame["result_sources"]["items"][0]["operation_id"] == source_id
    with factory() as session:
        read = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "read_task_results",
            )
        )
        assert read.status == "succeeded", read.error
        assert len(read.result["results"]) == 3
        assert "任务写入后可核对的角色设定" in str(read.result)
        read_id = str(read.id)
    # Using the read operation as a save source gives actionable source identities.
    advance(
        runtime,
        accepted,
        state,
        calls=[("wrong-source", "read_task_results", {"operation_id": read_id})],
    )
    with factory() as session:
        bad = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.status == "failed",
            )
        )
        assert bad.error["code"] == "invalid_tool_arguments"
        assert bad.error["details"]["result_sources"]["items"][0]["operation_id"] == source_id
    # A real truncated provider response pauses the attempt, then a normal message
    # starts a new attempt on the same logical task without old control APIs.
    pause_with_truncated_response(runtime, accepted, state)
    with factory() as session:
        service = task_service(session, settings)
        request = {"content": "继续", "task_id": str(accepted.task.id)}
        resumed = service.send_task_message(accepted.task.conversation_id, request, "continue")
        repeated = service.send_task_message(accepted.task.conversation_id, request, "continue")
        assert resumed.task.id == accepted.task.id and resumed.run.id != accepted.run.id
        assert resumed.run.id == repeated.run.id
        assert resumed.task.interruptions == {"stop": True, "redirect": False, "resume": False}
    body = advance(
        runtime,
        resumed,
        state,
        calls=[("verify-after-continue", "read_task_results", {"operation_id": source_id})],
    )
    assert any(
        item["role"] == "tool" and "雨夜乘客" in item["content"] for item in body["messages"]
    )
    with factory() as session:
        task_service(session, settings).send_task_message(
            accepted.task.conversation_id,
            {
                "task_id": accepted.task.id,
                "content": "移除原先选入的角色引用，保留本任务新建成果。",
                "subject_selection": {
                    "expected_input_revision": resumed.task.input_revision,
                    "retain_keys": [],
                },
            },
            "remove-original-subject",
        )
    wire = advance(
        runtime,
        resumed,
        state,
        calls=[
            (
                "withdrawn-result",
                "read_task_results",
                {"operation_id": source_id, "ids": [reference["id"]]},
            )
        ],
    )
    assert "任务写入后可核对的角色设定" not in json.dumps(wire, ensure_ascii=False)
    with factory() as session:
        withdrawn = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id.desc())
        ).first()
        assert withdrawn.error["code"] == "assistant_result_scope"
    advance(runtime, resumed, state, text="已核对原任务保存的乘客、场景设定和关联分镜。")
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert task.status == "succeeded"
        writes = session.scalars(
            select(AgentOperation).where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == "apply_standard_patch",
            )
        ).all()
        assert len(writes) == 1


def test_original_save_directory_pages_across_batches(task_workspace, provider):
    state = configured(task_workspace, provider)
    factory, settings, model = task_workspace
    reference = subject_assets(task_workspace)[0]
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "分批保存两个场景设定并核对批次目录。",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
            },
            uuid4().hex,
        )
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, calls=[("directory", "describe_creation_target", {})])
    advance(
        runtime,
        accepted,
        state,
        calls=[("enable", "enable_tool_group", {"group": "standard_write"})],
    )
    for index in range(2):
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"batch-{index}",
                    "apply_standard_patch",
                    {
                        "patch": {
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "scene",
                                    "values": {"kind": "scene", "name": f"场景{index}"},
                                }
                            ]
                        }
                    },
                )
            ],
        )
    with factory() as session:
        sources = list(
            session.scalars(
                select(AgentOperation.id)
                .where(
                    AgentOperation.task_id == accepted.task.id,
                    AgentOperation.tool_name == "apply_standard_patch",
                    AgentOperation.status == "succeeded",
                )
                .order_by(AgentOperation.id.desc())
            )
        )
        assert len(sources) == 2
    cursor = None
    for index, source_id in enumerate(sources):
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"page-{index}",
                    "describe_creation_target",
                    {"result_before_id": cursor, "result_limit": 1},
                )
            ],
        )
        with factory() as session:
            result = session.scalars(
                select(AgentOperation.result)
                .where(AgentOperation.task_id == accepted.task.id)
                .order_by(AgentOperation.id.desc())
            ).first()["result_sources"]
        assert [item["operation_id"] for item in result["items"]] == [str(source_id)]
        cursor = result["next_result_before_id"]
        assert cursor == (str(source_id) if index == 0 else None)
    advance(runtime, accepted, state, text="两个保存批次均可通过目录找到。")


def test_graph_pending_writes_resume_filtering_concurrency_and_scope(task_workspace):
    from concurrent.futures import ThreadPoolExecutor

    from langgraph.graph import END, START, StateGraph

    from short_drama.agent.harness.runtime import HarnessState
    from short_drama.core.exceptions import NotFound

    _conversation, accepted = new_task(task_workspace)
    factory, _settings, _model = task_workspace
    service = AgentGraphService(
        factory,
        owner=1,
        task=accepted.task.id,
        run=accepted.run.id,
        namespace="resume-test",
        version=ENGINE_VERSION,
    )
    saver = MySQLCheckpointSaver(service)
    counter = {"first": 0, "second": 0}

    def first(state):
        counter["first"] += 1
        return {"phase": "tools"}

    def second(state):
        counter["second"] += 1
        if counter["second"] == 1:
            raise RuntimeError("simulated node failure")
        return {"phase": "done"}

    builder = StateGraph(HarnessState)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile(checkpointer=saver)
    config = saver._config()
    with pytest.raises(RuntimeError):
        graph.invoke(
            {
                "run_id": accepted.run.id,
                "task_id": accepted.task.id,
                "turn_id": None,
                "phase": "model",
            },
            config,
            durability="sync",
        )
    assert saver.get_tuple(config).pending_writes
    assert graph.invoke(None, config, durability="sync")["phase"] == "done"
    assert counter == {"first": 1, "second": 2}
    assert len(list(saver.list(config, filter={"source": "input"}, limit=1))) == 1
    latest = saver.get_tuple(config).config
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(lambda _: saver.put_writes(latest, [("phase", "done")], "duplicate"), range(2))
        )
    entries = [item for item in saver.get_tuple(latest).pending_writes if item[0] == "duplicate"]
    assert len(entries) == 1
    with pytest.raises(WorkflowError):
        saver.put_writes(latest, [("phase", b"entity")], "bad")
    with pytest.raises(NotFound):
        AgentGraphService(
            factory,
            owner=2,
            task=accepted.task.id,
            run=accepted.run.id,
            namespace="resume-test",
            version=ENGINE_VERSION,
        ).load()


def test_state_projects_large_task_and_operation_bodies(task_workspace, provider):
    from sqlalchemy import event

    state = configured(task_workspace, provider)
    conversation, accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, calls=[("search", "search_skills", {})])
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        task.context_snapshot = {
            "schema_version": 2,
            "subjects": [
                {
                    "subject_key": "subject:" + "a" * 64,
                    "project_id": "1",
                    "project_name": "作品",
                    "source_kind": "project",
                    "source_id": "1",
                    "kind": "asset",
                    "id": "1",
                    "snapshot_id": "1",
                    "revision": "1",
                    "checksum": "a" * 64,
                    "name": "主角",
                    "allowed_fields": ["name"],
                    "fields": {"prompt": "x" * 600000},
                    "media": [{"ignored": "y" * 400000}],
                }
            ],
        }
        task.selected_skills = [
            {
                "id": "skill1",
                "name": "方法",
                "content_version": "1",
                "builtin": False,
                "instructions": "z" * 600000,
            }
        ]
        task.effective_state = {**task.effective_state, "old_snapshot": "q" * 600000}
        operation = session.scalar(select(AgentOperation).where(AgentOperation.task_id == task.id))
        operation.arguments = {"large": "a" * 600000}
        operation.result = {**operation.result, "large": "b" * 600000}
        operation.changes = [{"large": "c" * 600000}]
        session.commit()
        session.connection().exec_driver_sql("SET SESSION sort_buffer_size = 262144")
        session.commit()
        statements = []
        event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))
        view = task_service(session, settings).task_runtime_state(conversation.id)
        assert view.tasks[0].selected_subjects[0].name == "主角"
        assert len(view.model_dump_json()) < 20000
        forbidden = {
            "agent_tasks.context_snapshot",
            "agent_tasks.effective_state",
            "agent_tasks.selected_skills",
            "agent_operations.arguments",
            "agent_operations.result",
            "agent_operations.changes",
        }
        for statement in statements:
            assert not forbidden.intersection(str(c) for c in statement.selected_columns)


def test_harness_discussion_revision_is_read_only_and_excludes_future_history(
    task_workspace, provider
):
    from short_drama.domain.assistant.conversations.models import AgentMessage

    state = configured(task_workspace, provider)
    conversation, accepted = new_task(task_workspace)
    factory, settings, model = task_workspace
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, text="原讨论回复")
    with factory() as session:
        service = task_service(session, settings)
        later = service.send_task_message(
            conversation.id,
            {"content": "以后才知道的机密事实", "model_config_id": model},
            uuid4().hex,
        )
    advance(runtime, later, state, text="未来回复")
    with factory() as session:
        revised = task_service(session, settings).revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "edit", "row_version": "1", "content": "只讨论三秒开场"},
            uuid4().hex,
        )
        # New tasks persist only the Skill index, never eager instruction text.
        task = session.get(AgentTask, revised.task.id)
        task.selected_skills = [{"id": "fixture", "name": "手选方法", "content_version": "1"}]
        session.commit()
    wire = advance(runtime, revised, state, text="修订后的讨论")
    assert not wire.get("tools")
    assert "以后才知道" not in json.dumps(wire, ensure_ascii=False)
    assert "未来回复" not in json.dumps(wire, ensure_ascii=False)
    with factory() as session:
        assert session.get(AgentRun, revised.run.id).status == "succeeded"
        assert not session.scalar(
            select(AgentOperation.id).where(AgentOperation.run_id == revised.run.id)
        )
        assert (
            session.scalar(
                select(AgentMessage.content).where(
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.seq == 2,
                )
            )
            == "修订后的讨论"
        )


def test_harness_purge_removes_graph_and_only_owned_turn_derivatives(task_workspace, provider):
    from short_drama.agent.session.purge import purge_one

    state = configured(task_workspace, provider)
    conversation, accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    advance(HarnessRuntime(factory, settings), accepted, state, text="完成讨论")
    with factory() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id))
        owned = f"minio://test/agent-harness/turn/{turn.id}/owned.jpg"
        original = "minio://test/business/original.jpg"
        sibling = "minio://test/agent-harness/turn/999/unrelated.jpg"
        turn.request_messages[0]["kwargs"]["hydration_intents"] = [
            {"resource": {"source": "hydrated_image", "uri": uri}}
            for uri in (owned, original, sibling)
        ]
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(turn, "request_messages")
        session.commit()
        service = task_service(session, settings)
        current = service.get_conversation(conversation.id)
        service.delete_global_conversation(
            conversation.id, {"row_version": current.row_version}, uuid4().hex
        )
    deleted = []
    assert purge_one(factory, SimpleNamespace(delete=deleted.append))
    assert deleted == [owned]
    with factory() as session:
        assert not session.scalar(select(AgentGraphCheckpoint.id))
        assert not session.scalar(select(AgentGraphWrite.id))


def test_autonomous_canvas_patch_keeps_versions_and_selected_scope(task_workspace, provider):
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        canvas_patch_workspace,
    )

    state = configured(task_workspace, provider)
    project_id, canvas_id, _saved = canvas_patch_workspace(task_workspace)
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "在画布写入一段雨夜开场",
                "model_config_id": model,
                "execution_target": {
                    "mode": "infinite_canvas",
                    "project_id": str(project_id),
                    "canvas_id": _saved["source_key"],
                },
            },
            uuid4().hex,
        )
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, calls=[("directory", "describe_creation_target", {})])
    with factory() as session:
        directory = session.scalar(
            select(AgentOperation.result).where(AgentOperation.task_id == accepted.task.id)
        )
    revision = directory["canvas_revision"]
    create = {
        "expected_revision": revision,
        "actions": [
            {
                "action": "create_node",
                "key": "opening",
                "values": {"type": "text", "title": "雨夜开场", "content": "铜钥匙落在站台边。"},
            }
        ],
    }
    advance(
        runtime, accepted, state, calls=[("enable", "enable_tool_group", {"group": "canvas_write"})]
    )
    advance(runtime, accepted, state, calls=[("create", "apply_canvas_patch", {"patch": create})])
    # The previous revision cannot create another node after the first commit.
    stale = {**create, "actions": [{**create["actions"][0], "key": "another"}]}
    advance(runtime, accepted, state, calls=[("stale", "apply_canvas_patch", {"patch": stale})])
    advance(runtime, accepted, state, text="已保存一段开场，旧版本写入已拒绝。")
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "apply_canvas_patch",
            )
            .order_by(AgentOperation.id)
        ).all()
        assert operations[0].status == "succeeded", operations[0].error
        assert operations[1].status == "conflict", operations[1].error


@pytest.mark.parametrize("resume", [False, True])
def test_harness_l5_forgetting_removes_previously_read_memory_from_next_context(
    task_workspace, provider, resume
):
    from short_drama.service.assistant.memory.memory import AgentMemoryService

    state = configured(task_workspace, provider)
    conversation, accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        memory = AgentMemoryService(session).create_item(
            {
                "scope_type": "user",
                "kind": "preference",
                "memory_key": "harness-style",
                "title": "旧风格",
                "content": "harness-forgotten-private-detail",
            },
            uuid4().hex,
        )
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, calls=[("memory", "read_memory", {"ids": [str(memory.id)]})])
    if resume:
        pause_with_truncated_response(runtime, accepted, state)
    with factory() as session:
        task_service(session, settings)
        AgentMemoryService(session).forget_item(
            memory.id, {"content_version": memory.content_version}
        )
    if resume:
        with factory() as session:
            accepted = task_service(session, settings).send_task_message(
                conversation.id, {"task_id": accepted.task.id, "content": "继续"}, uuid4().hex
            )
    wire = advance(runtime, accepted, state, text="只依据仍可用的当前输入完成讨论。")
    assert "harness-forgotten-private-detail" not in json.dumps(wire)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"


@pytest.mark.parametrize("change", ["remove", "version", "forget_source"])
@pytest.mark.parametrize("resume", [False, True])
def test_skill_selection_and_source_changes_revoke_old_body(
    task_workspace, provider, change, resume
):
    from short_drama.service.assistant.memory.memory import AgentMemoryService
    from short_drama.service.skills.catalog.skill import AgentSkillService

    factory, settings, model = task_workspace
    state = configured(task_workspace, provider)
    settings.minio_image_bucket = "fixture-skills"
    storage = _Storage(factory)
    skill = create_skill(
        (*task_workspace, storage), instructions="retired-skill-body-marker", resources=[]
    )
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "使用所选方法核对节奏。",
                "model_config_id": model,
                "skills": [{"id": skill.id, "content_version": skill.content_version}],
            },
            uuid4().hex,
        )
    runtime = HarnessRuntime(factory, settings)
    load = {
        "name": skill.name,
        "content_version": str(skill.content_version),
        "purpose": "核对 SOP",
    }
    advance(
        runtime,
        accepted,
        state,
        calls=[("load-old", "load_skill", load)],
        text="先加载方法 retired-skill-body-marker",
    )
    if resume:
        pause_with_truncated_response(runtime, accepted, state)
    with factory() as session:
        service = task_service(session, settings)
        if change == "forget_source":
            memory = AgentMemoryService(session).create_item(
                {
                    "scope_type": "user",
                    "kind": "preference",
                    "memory_key": "skill-source",
                    "title": "方法选择来源",
                    "content": "本任务的方法选择来自这条用户消息。",
                    "source_conversation_id": conversation.id,
                    "source_message_ids": [accepted.message.id],
                },
                uuid4().hex,
            )
            AgentMemoryService(session).forget_item(
                memory.id, {"content_version": memory.content_version}
            )
            if resume:
                accepted = service.send_task_message(
                    conversation.id,
                    {"task_id": accepted.task.id, "content": "继续"},
                    uuid4().hex,
                )
        else:
            selected = []
            if change == "version":
                current = AgentSkillService(session, settings, storage).patch(
                    skill.id,
                    {"row_version": skill.row_version, "instructions": "current-skill-body-marker"},
                )
                selected = [{"id": current.id, "content_version": current.content_version}]
            accepted = service.send_task_message(
                conversation.id,
                {"task_id": accepted.task.id, "content": "按当前选择继续。", "skills": selected},
                uuid4().hex,
            )
    wire = advance(runtime, accepted, state, calls=[("next", "search_skills", {"query": ""})])
    assert "retired-skill-body-marker" not in json.dumps(wire)
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert not any(item["active"] for item in task.effective_state["skill_activations"])
        assert not session.get(AgentRun, accepted.run.id).checkpoint["skill_body_operations"]
    if change == "remove":
        advance(runtime, accepted, state, calls=[("reload", "load_skill", load)])
        with factory() as session:
            operation = session.scalars(
                select(AgentOperation)
                .where(AgentOperation.task_id == accepted.task.id)
                .order_by(AgentOperation.id.desc())
            ).first()
            assert operation.error["code"] == "assistant_skill_excluded"
    elif change == "version":
        load["content_version"] = str(current.content_version)
        advance(runtime, accepted, state, calls=[("load-new", "load_skill", load)])
    final = advance(runtime, accepted, state, text="已依据当前有效输入核对节奏。")
    assert "retired-skill-body-marker" not in json.dumps(final)
    if change == "version":
        assert "current-skill-body-marker" in json.dumps(final)
