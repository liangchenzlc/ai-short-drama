"""Trace replay through production graph / SDK / MySQL, with controlled provider wire."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from short_drama.agent.runtime.events import save_block_locked
from short_drama.agent.session.purge import purge_one
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.core.time import utcnow
from short_drama.domain.assistant.conversations.models import AgentEvent, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.identity.accounts.user import UserSession
from short_drama.service.assistant.tasks.trace import AssistantTraceService
from short_drama.service.media.files.storage import StorageService
from short_drama.service.skills.catalog.skill import AgentSkillService
from tests.integration.standard.assistant.test_agent_runtime import runtime_for
from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    task_actor,
    task_service,
)
from tests.support.assistant_runtime import RuntimeTransport, calls
from tests.support.assistant_trace import TraceWireTransport

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def trace_service(session, settings, storage=None, owner=1):
    session.info["actor"] = task_actor(owner)
    return AssistantTraceService(session, settings, storage)


class TraceStorage(_Storage):
    def stat(self, bucket, key):
        if (bucket, key) not in self.objects:
            raise NotFound("object missing")
        return SimpleNamespace(bucket=bucket, object_name=key)

    def put(self, bucket, key, source, size, _mime):
        self.objects[bucket, key] = source.read()
        assert len(self.objects[bucket, key]) == size
        return self.stat(bucket, key)

    def open(self, bucket, key):
        import io
        from contextlib import contextmanager

        @contextmanager
        def opened():
            with io.BytesIO(self.objects[bucket, key]) as value:
                yield value

        return opened()

    def close(self):
        pass

    def presigned_get(self, bucket, key, _expiry):
        assert (bucket, key) in self.objects
        return "https://files.invalid/authorized-detail"

    def remove(self, bucket, key, **_kwargs):
        self.objects.pop((bucket, key), None)


@pytest.fixture
def resources(task_workspace, monkeypatch):
    factory, settings, model = task_workspace
    settings.minio_image_bucket, settings.minio_video_bucket, settings.minio_audio_bucket = (
        "trace-images",
        "trace-video",
        "trace-audio",
    )
    settings.minio_presign_expiry, settings.auth_enabled = 60, False
    storage = TraceStorage(factory)
    monkeypatch.setattr("short_drama.storage.minio.MinioStorage", lambda _settings: storage)
    monkeypatch.setattr(
        "short_drama.agent.sandbox.resources.MinioStorage", lambda _settings: storage
    )
    monkeypatch.setattr(
        "short_drama.agent.skill.runtime.read_resource_bytes",
        lambda _settings, manifest: AgentSkillService(None, settings, storage)._resource_bytes(
            manifest
        ),
    )
    return factory, settings, model, storage


def test_formal_runtime_streams_root_text_and_keeps_hidden_tool_trace(task_workspace):
    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(("policy", "runtime__get_generation_policy", {})),
                {"text": "雨🙂声"},
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        events = session.scalars(
            select(AgentEvent)
            .where(AgentEvent.conversation_id == conversation.id)
            .order_by(AgentEvent.seq)
        ).all()
        deltas = [row.payload["delta"] for row in events if row.event_type == "assistant.delta"]
        assert "".join(deltas) == "雨🙂声"
        blocks = [row.payload for row in events if row.event_type == "trace.block"]
        assert any(block["trace_visibility"] == "hidden" for block in blocks)
        assert not any(block["kind"] == "reasoning" for block in blocks)
        assert [
            row.content
            for row in session.scalars(
                select(AgentMessage).where(
                    AgentMessage.task_id == accepted.task.id, AgentMessage.role == "assistant"
                )
            )
        ] == ["雨🙂声"]


def freeze_responses(factory, run_id):
    with factory.begin() as session:
        run = session.get(AgentRun, run_id)
        run.config_snapshot = {
            **run.config_snapshot,
            "runtime_profile": {"api_format": "openai", "protocol": "openai-response"},
            "assistant_settings": {
                **run.config_snapshot["assistant_settings"],
                "tool_descriptions": {},
            },
        }
        run.checkpoint = {**run.checkpoint, "permitted_tool_names": []}


def test_reasoning_flush_writes_one_latest_block_with_contiguous_utf8_replay(
    resources, monkeypatch
):
    from short_drama.agent.runtime.events import AgentEventAdapter
    from short_drama.dao.assistant.execution.trace import AssistantTraceDAO

    factory, settings, model, _storage = resources
    conversation, accepted = new_task((factory, settings, model))
    freeze_responses(factory, accepted.run.id)
    text = "雨🙂" * 6000
    batches, original = [], AgentEventAdapter.save_delta

    def capture(adapter, event, content, offset):
        result = original(adapter, event, content, offset)
        if event["channel"] == "reasoning":
            batches.append((offset, len(content.encode())))
        return result

    monkeypatch.setattr(AgentEventAdapter, "save_delta", capture)
    runtime_for(factory, settings, TraceWireTransport(thoughts=(text,), texts=("完成",))).execute(
        accepted.run.id, 1
    )
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        rows = session.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.seq)
        ).all()
        deltas = [row for row in rows if row.event_type == "trace.delta"]
        blocks = [
            row
            for row in rows
            if row.event_type == "trace.block" and row.payload["kind"] == "reasoning"
        ]
        assert len(blocks) == len(batches) + 4  # empty start, completed, archive pending/available
        assert "".join(row.payload["delta"] for row in deltas) == text
        offset = 0
        for row in deltas:
            assert row.payload["offset"] == offset and len(row.payload["delta"]) <= 2048
            offset += len(row.payload["delta"].encode())
            following = next(block for block in blocks if block.seq > row.seq)
            assert row.payload["block_version"] == following.payload["block_version"]
        assert offset == len(text.encode())
        assert [int(row.payload["block_version"]) for row in blocks] == list(
            range(1, len(blocks) + 1)
        )
        final = AssistantTraceDAO(session).page(conversation.id, run.id, rows[-1].seq).items[0]
        assert final.status == "succeeded" and final.channels["reasoning"].total_bytes == offset
        assert "\ufffd" not in final.channels["reasoning"].preview
        # A disconnect after any delta must resume from the same byte watermark.
        for row in deltas:
            before = [block for block in blocks if block.seq < row.seq][-1]
            trailing = [item for item in deltas if before.seq < item.seq <= row.seq]
            replay = AssistantTraceDAO(session).block(before, trailing)
            assert replay.channels["reasoning"].total_bytes == row.payload["offset"] + len(
                row.payload["delta"].encode()
            )
            assert "\ufffd" not in replay.channels["reasoning"].preview


@pytest.mark.parametrize("channel", ["reasoning", "output"])
def test_reasoning_archive_outage_preserves_successful_model_receipt(
    resources, monkeypatch, channel
):
    from short_drama.core.exceptions import StorageUnavailable
    from short_drama.domain.assistant.execution.models import AgentTurn

    factory, settings, model, storage = resources
    skill = (
        create_skill(resources, instructions="大正文雨🙂" * 1400) if channel == "output" else None
    )
    conversation, accepted = new_task((factory, settings, model))
    if channel == "reasoning":
        freeze_responses(factory, accepted.run.id)
        transport = TraceWireTransport(thoughts=("思考🙂" * 5000,), texts=("完整正文",))
    else:
        transport = RuntimeTransport(
            [
                calls(
                    (
                        "large",
                        "runtime__load_skill",
                        {"name": skill.name, "content_version": "1", "purpose": "归档故障核对"},
                    )
                ),
                {"text": "正文正常交付"},
            ]
        )

    def unavailable(*_args, **_kwargs):
        raise StorageUnavailable("controlled object storage outage")

    monkeypatch.setattr(storage, "put", unavailable)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert run.status == "succeeded", run.error
        assert turns and all(turn.status == "succeeded" and turn.error is None for turn in turns)
        assert all(turn.response["raw"] and turn.usage["settled"] for turn in turns)
        assert run.usage["output_tokens"] > 0 and run.usage["reserved_output_tokens"] == 0
        session.rollback()
        page = trace_service(session, settings, storage).page(conversation.id, accepted.run.id)
        archive = next(
            item for block in page.items for item in block.resources if item.kind == "archive"
        )
        assert archive.available is False


@pytest.mark.parametrize("control", ["stop", "delete"])
def test_cancelled_formal_stream_restores_complete_interrupted_text(
    task_workspace, control, monkeypatch
):
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.domain.assistant.execution.graph import AgentGraphCheckpoint

    factory, settings, _ = task_workspace
    conversation, accepted = new_task(task_workspace)
    freeze_responses(factory, accepted.run.id)
    settings.agent_lease_seconds = 180
    shown, release, cancelled = Event(), Event(), Event()
    original = RunModelLedger.save_error

    def record(self, payload):
        original(self, payload)
        if payload.get("code") == "agent_model_cancelled":
            cancelled.set()

    monkeypatch.setattr(RunModelLedger, "save_error", record)
    text = "长正文雨🙂" * 1500

    class PartialTransport(TraceWireTransport):
        def request(self, _method, _url, *, body, on_send, on_headers, on_chunk, **_kwargs):
            on_send()
            self.requests.append(body)
            on_headers(200, {"Content-Type": "text/event-stream"})
            for data in self.parts:
                if b"response.output_item.done" in data and b"message" in data:
                    shown.set()
                    assert release.wait(12)
                on_chunk(data)
            return 200, {"Content-Type": "text/event-stream"}, b"".join(self.parts)

    transport = PartialTransport(thoughts=(), texts=(text,))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime_for(factory, settings, transport).execute, accepted.run.id, 1)
        try:
            assert shown.wait(10)
            deadline = time.monotonic() + 5
            while True:
                with factory() as session:
                    visible = session.scalars(
                        select(AgentEvent)
                        .where(
                            AgentEvent.run_id == accepted.run.id,
                            AgentEvent.event_type == "assistant.delta",
                        )
                        .order_by(AgentEvent.seq)
                    ).all()
                displayed = "".join(row.payload["delta"] for row in visible)
                if displayed == text:
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
            with factory() as session:
                service = task_service(session, settings)
                if control == "stop":
                    result = service.control_task(accepted.task.id, {"kind": "stop"}, uuid4().hex)
                    assert result.run.status == "running"
                    assert result.run.stopping is True
                else:
                    version = service._conversation(conversation.id).row_version
                    session.rollback()
                    service.delete_global_conversation(
                        conversation.id, {"row_version": version}, uuid4().hex
                    )
            assert cancelled.wait(4), "default lease must cancel model within four seconds"
        finally:
            release.set()
        future.result(timeout=15)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled"
        assert (
            session.scalar(
                select(AgentGraphCheckpoint).where(AgentGraphCheckpoint.run_id == run.id)
            )
            is not None
        )
        if control == "stop":
            session.rollback()
            snapshot = task_service(session, settings).task_runtime_state(conversation.id)
            interrupted = [m for m in snapshot.messages.items if m.role == "assistant"]
            assert len(interrupted) == 1 and interrupted[0].content == text
            assert interrupted[0].interrupted is True
            assert interrupted[0].text_parts[0].content == text
            assert len(interrupted[0].text_parts) == 1
            from short_drama.agent.memory.sources import message_sources

            source = next(
                item
                for item in message_sources(session, conversation.id, 1, 1000)
                if item["id"] == str(interrupted[0].id)
            )
            assert source["interrupted"] is True and text not in source["content"]
            from short_drama.agent.memory.context import enqueue_job_locked

            current = session.get(AgentRun, accepted.run.id)
            conv = task_service(session, settings)._conversation(conversation.id)
            summary = enqueue_job_locked(session, conv, current, "summary", [source])
            assert summary.source_snapshot["messages"][0]["interrupted"] is True
            assert text not in json.dumps(summary.source_snapshot, ensure_ascii=False)
        else:
            messages = session.scalars(
                select(AgentMessage).where(
                    AgentMessage.task_id == accepted.task.id, AgentMessage.role == "assistant"
                )
            ).all()
            assert len(messages) == 1 and messages[0].content == text
    if control == "stop":
        with factory() as session:
            newer = task_service(session, settings).send_task_message(
                conversation.id,
                {"task_id": accepted.task.id, "content": "仅回复当前状态"},
                uuid4().hex,
            )
        next_transport = RuntimeTransport([{"text": "新轮次完成"}])
        runtime_for(factory, settings, next_transport).execute(newer.run.id, 1)
        assert next_transport.requests and text not in json.dumps(
            next_transport.requests, ensure_ascii=False
        )


def test_failure_after_display_keeps_full_text_and_existing_narration_is_not_duplicated(
    task_workspace, monkeypatch
):
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original = AgentToolExecutionService.model_result
    text = "中断正文雨🙂" * 1200

    def fail_final(self, signature, message):
        if not message.tool_calls:
            raise ValueError("controlled failure after display")
        return original(self, signature, message)

    monkeypatch.setattr(AgentToolExecutionService, "model_result", fail_final)
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                {**calls(("policy", "runtime__get_generation_policy", {})), "text": "已核对🙂"},
                {"text": text},
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        snapshot = task_service(session, settings).task_runtime_state(conversation.id)
        messages = [m for m in snapshot.messages.items if m.role == "assistant"]
        assert [m.content for m in messages if m.kind == "narration"] == ["已核对🙂", text]
        assert [m.interrupted for m in messages if m.kind == "narration"] == [False, True]
        assert next(run for run in snapshot.runs if run.id == accepted.run.id).status == "failed"


@pytest.mark.parametrize(
    "code",
    [
        "invalid_agent_configuration",
        "agent_budget_exhausted",
        "agent_execution_error",
        "invalid_agent_tool_selection",
        "agent_acceptance_unknown",
        "agent_summary_empty",
    ],
)
def test_public_run_errors_use_safe_distinct_messages(task_workspace, code):
    from short_drama.agent.core.state import failure_message

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory.begin() as session:
        session.get(AgentRun, accepted.run.id).error = {
            "code": code,
            "message": "SQL and provider-private-text",
        }
    with factory() as session:
        error = task_service(session, settings).get_run(accepted.run.id).error
    assert error == {"code": code, "message": failure_message(code)}
    assert "provider-private-text" not in str(error)


def test_public_timeout_error_retains_unknown_acceptance_warning(task_workspace):
    from short_drama.ai.types import GenerationError

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace

    def timeout(_body, _number):
        raise GenerationError("timeout", accepted_unknown=True)

    runtime_for(
        factory, settings, RuntimeTransport([{"text": "unused"}], before_reply=timeout)
    ).execute(accepted.run.id, 1)
    with factory() as session:
        error = task_service(session, settings).get_run(accepted.run.id).error
    assert error["code"] == "timeout" and error["accepted_unknown"] is True
    assert "重复生成和计费" in error["message"]


def test_running_refresh_replay_and_utf8_reasoning_use_same_watermark(task_workspace):
    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    freeze_responses(factory, accepted.run.id)
    gate = Event()
    transport = TraceWireTransport(gate=gate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(runtime_for(factory, settings, transport).execute, accepted.run.id, 1)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                with factory() as session:
                    snapshot = task_service(session, settings).task_runtime_state(conversation.id)
                if snapshot.trace_blocks:
                    break
                time.sleep(0.03)
            assert snapshot.trace_blocks and snapshot.trace_blocks[0].status == "running"
            assert snapshot.trace_blocks[0].channels["reasoning"].preview == "雨🙂"
        finally:
            gate.set()
        running.result(timeout=25)
    with factory() as session:
        service = trace_service(session, settings)
        page = service.page(conversation.id, accepted.run.id)
        assert len(page.items) == 1 and page.items[0].status == "succeeded"
        assert page.items[0].channels["reasoning"].preview == "雨🙂声"
        later = service.events(conversation.id, snapshot.snapshot_cursor)
        blocks = {item.block_id: item.model_dump(mode="json") for item in snapshot.trace_blocks}
        for event in [*later, *later]:
            if event.event_type == "trace.block":
                old = blocks.get(int(event.payload["block_id"]))
                if not old or event.payload["block_version"] > old["block_version"]:
                    blocks[int(event.payload["block_id"])] = event.payload
        assert blocks[page.items[0].block_id] == page.items[0].model_dump(mode="json")
        all_events = service.events(conversation.id)
        thoughts = [event.payload for event in all_events if event.event_type == "trace.delta"]
        assert [(item["offset"], item["delta"]) for item in thoughts] == [(0, "雨🙂"), (7, "声")]
        assert (
            "".join(
                event.payload["delta"]
                for event in all_events
                if event.event_type == "assistant.delta"
            )
            == "正文雨🙂声"
        )
        assert "private-cipher" not in json.dumps(
            [event.model_dump(mode="json") for event in all_events]
        )


def test_skill_titles_frozen_versions_resources_and_failure_targets(resources):
    factory, settings, model, storage = resources
    first = create_skill(resources)
    second = create_skill(resources, name="海边对白", slug="coast-dialogue")
    conversation, accepted = new_task((factory, settings, model))
    with factory() as session:
        session.info["actor"] = task_actor()
        newer = AgentSkillService(session, settings, storage).patch(
            first.id, {"row_version": first.row_version, "instructions": "最新正文不应替换旧版轨迹"}
        )

    def reads(body):
        from tests.support.assistant_runtime import tool_result

        activation = tool_result(body, "rain")["activation_id"]
        return calls(
            (
                "resource-a",
                "runtime__read_skill_resource",
                {"activation_id": activation, "path": "assets/method.txt", "offset": 3, "limit": 5},
            ),
            (
                "missing-path",
                "runtime__read_skill_resource",
                {"activation_id": activation, "path": "assets/missing.txt"},
            ),
        )

    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    (
                        "rain",
                        "runtime__load_skill",
                        {"name": first.name, "content_version": "1", "purpose": "冻结核对"},
                    ),
                    (
                        "coast",
                        "runtime__load_skill",
                        {"name": second.name, "content_version": "1", "purpose": "另一方法"},
                    ),
                ),
                reads,
                {"text": "已读取冻结版本"},
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        service = trace_service(session, settings, storage)
        page = service.page(conversation.id, accepted.run.id)
        assert {item.title for item in page.items if item.action == "加载 Skill"} == {
            "加载 Skill《雨夜方法》 · v1",
            "加载 Skill《海边对白》 · v1",
        }
        body = next(item for item in page.items if item.title == "加载 Skill《雨夜方法》 · v1")
        detail = service.resource(
            conversation.id, accepted.run.id, body.block_id, body.resources[0].resource_id
        )
        assert detail.content == first.instructions
        successful = next(item for item in page.items if item.target.path == "assets/method.txt")
        assert successful.target.offset == 3 and successful.target.length == 5
        detail = service.resource(
            conversation.id,
            accepted.run.id,
            successful.block_id,
            successful.resources[0].resource_id,
        )
        assert detail.content == "角色目标：旧版参考。"[3:8]
        failed = next(item for item in page.items if item.target.path == "assets/missing.txt")
        assert failed.status == "failed" and failed.target.display_name == first.name
        assert failed.tool_name == "runtime__read_skill_resource" and failed.duration_ms is not None
        state = task_service(session, settings).task_runtime_state(conversation.id)
        assert {item.title for item in state.operations} == {item.title for item in page.items}
        assert (
            task_service(session, settings).operation_detail(failed.operation_id).target.path
            == "assets/missing.txt"
        )
        next_run = task_service(session, settings).send_task_message(
            conversation.id,
            {"content": "使用新版本", "new_task": True, "model_config_id": model},
            uuid4().hex,
        )
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    (
                        "new",
                        "runtime__load_skill",
                        {
                            "name": first.name,
                            "content_version": str(newer.content_version),
                            "purpose": "新版",
                        },
                    )
                ),
                {"text": "已核对新版"},
            ]
        ),
    ).execute(next_run.run.id, 1)
    with factory() as session:
        page = trace_service(session, settings).page(conversation.id, next_run.run.id)
        assert any(item.title == "加载 Skill《雨夜方法》 · v2" for item in page.items)
        session.info["actor"] = task_actor()
        AgentSkillService(session, settings, storage).delete(
            first.id, {"row_version": newer.row_version}
        )
        with pytest.raises(NotFound):
            trace_service(session, settings, storage).resource(
                conversation.id, accepted.run.id, body.block_id, body.resources[0].resource_id
            )


def test_replacement_worker_interrupts_old_block_and_archives_large_visible_reasoning(resources):
    from short_drama.agent.core.access import lock_run
    from short_drama.domain.assistant.execution.models import AgentTurn
    from short_drama.service.assistant.execution.scope import root_scope_locked
    from short_drama.utils.snowflake import next_id

    factory, settings, model, storage = resources
    conversation, accepted = new_task((factory, settings, model))
    freeze_responses(factory, accepted.run.id)
    old_id = next_id()
    # 模拟进程退出后已提交的未终态快照；恢复后的请求仍走真实 SDK / ledger。
    with factory.begin() as session:
        _, current, run = lock_run(session, accepted.run.id)
        scope = root_scope_locked(session, run)
        save_block_locked(
            session,
            current,
            run,
            {
                "block_id": str(old_id),
                "model_call_id": str(old_id),
                "run_id": str(run.id),
                "execution_scope_id": str(scope.id),
                "kind": "reasoning",
                "source": "assistant",
                "action": "模型思考",
                "title": "模型提供的思考",
                "target": {"kind": "model", "id": str(model), "display_name": "冻结模型"},
                "status": "running",
                "started_at": utcnow(),
                "updated_at": utcnow(),
                "channels": {"reasoning": {"preview": "崩溃前", "total_bytes": 9}},
            },
        )
    prefix = "a" * 16382 + "雨"
    thought = prefix + "z"
    runtime_for(
        factory, settings, TraceWireTransport(thoughts=(prefix, "z"), texts=("新调用完成",))
    ).execute(accepted.run.id, 1)
    with factory() as session:
        service = trace_service(session, settings, storage)
        page = service.page(conversation.id, accepted.run.id)
        old = next(item for item in page.items if item.block_id == old_id)
        fresh = next(item for item in page.items if item.block_id != old_id)
        assert old.status == "interrupted" and old.channels["reasoning"].preview == "崩溃前"
        assert fresh.status == "succeeded" and fresh.model_call_id == fresh.block_id
        assert fresh.channels["reasoning"].total_bytes == len(thought.encode("utf-8"))
        assert len(fresh.channels["reasoning"].preview.encode("utf-8")) <= 16384
        assert fresh.channels["reasoning"].preview == "a" * 16382
        archive = next(item for item in fresh.resources if item.kind == "archive")
        assert (
            archive.available
            and not archive.truncated
            and archive.byte_size == len(thought.encode("utf-8"))
        )
        assert service.resource(
            conversation.id, accepted.run.id, fresh.block_id, archive.resource_id
        ).url
        turn_ids = set(
            session.scalars(select(AgentTurn.id).where(AgentTurn.run_id == accepted.run.id))
        )
        assert fresh.model_call_id in turn_ids and old_id not in turn_ids
    assert any(content.decode("utf-8") == thought for content in storage.objects.values())


def test_three_subtasks_do_not_mix_parent_calls_or_publish_child_text(task_workspace):
    from tests.integration.standard.assistant.test_agent_subtasks import SubtaskTransport

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    runtime_for(
        factory, settings, SubtaskTransport(["一 token=trace-secret fixture", "二", "三"])
    ).execute(accepted.run.id, 1)
    with factory() as session:
        page = trace_service(session, settings).page(conversation.id, accepted.run.id)
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == accepted.run.id)
        ).all()
        session.commit()
        children = [scope for scope in scopes if scope.parent_tool_call_id]
        assert len(children) == 3
        for scope in children:
            child_blocks = [block for block in page.items if block.execution_scope_id == scope.id]
            assert child_blocks and all(
                block.parent_call_id == scope.parent_tool_call_id for block in child_blocks
            )
        text = "".join(
            event.payload["delta"]
            for event in trace_service(session, settings).events(conversation.id)
            if event.event_type == "assistant.delta"
        )
        assert text == "已整合子任务成果"
        snapshot = task_service(session, settings).task_runtime_state(conversation.id)
        operations = task_service(session, settings).operation_page(accepted.task.id)
        subtask = next(
            block
            for block in page.items
            if block.kind == "subtask" and "fixture" in block.target.display_name
        )
        detail = task_service(session, settings).operation_detail(subtask.operation_id)
        events = trace_service(session, settings).events(conversation.id)
        for public in [page, snapshot, operations, detail, *events]:
            assert "trace-secret" not in json.dumps(public.model_dump(mode="json"))
        assert any("[已隐藏]" in block.target.display_name for block in page.items)


def test_large_detail_is_bounded_authorized_and_purged(resources):
    factory, settings, model, storage = resources
    skill = create_skill(resources, instructions="大正文雨🙂" * 1400)
    conversation, accepted = new_task((factory, settings, model))
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    (
                        "large",
                        "runtime__load_skill",
                        {"name": skill.name, "content_version": "1", "purpose": "归档核对"},
                    )
                ),
                {"text": "归档完成"},
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        service = trace_service(session, settings, storage)
        page = service.page(conversation.id, accepted.run.id)
        block = page.items[0]
        assert all(len(item.preview.encode("utf-8")) <= 16384 for item in block.channels.values())
        assert block.channels["output"].truncated
        archive = next(item for item in block.resources if item.kind == "archive")
        assert service.resource(
            conversation.id, accepted.run.id, block.block_id, archive.resource_id
        ).url
        assert storage.objects
        events = service.events(conversation.id)
        assert "agent-harness" not in json.dumps(
            [event.model_dump(mode="json") for event in events]
        )
        with pytest.raises(NotFound):
            trace_service(session, settings, storage, owner=2).resource(
                conversation.id, accepted.run.id, block.block_id, archive.resource_id
            )
        with pytest.raises(NotFound):
            service.resource(conversation.id, accepted.run.id, block.block_id, "9")
        manager = task_service(session, settings)
        current = manager.get_conversation(conversation.id)
        manager.delete_global_conversation(
            conversation.id, {"row_version": current.row_version}, uuid4().hex
        )
        with pytest.raises(NotFound):
            trace_service(session, settings, storage).resource(
                conversation.id, accepted.run.id, block.block_id, archive.resource_id
            )
    assert purge_one(factory, StorageService(storage, settings))
    assert not any("agent-harness/operation/" in key for _, key in storage.objects)


def test_paging_uses_latest_snapshot_with_bounded_default_and_terminal_versions(task_workspace):
    from short_drama.agent.core.access import lock_run
    from short_drama.schemas.assistant.operations.trace import TraceBlock
    from short_drama.utils.snowflake import next_id

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(("policy", "runtime__get_generation_policy", {})),
                {"text": "分页"},
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory.begin() as session:
        _, current, run = lock_run(session, accepted.run.id)
        original = session.scalar(
            select(AgentEvent)
            .where(AgentEvent.event_type == "trace.block")
            .order_by(AgentEvent.seq.desc())
        )
        block = {key: value for key, value in original.payload.items() if not key.startswith("_")}
        for _ in range(104):
            save_block_locked(session, current, run, {**block, "block_id": str(next_id())})
        terminal = TraceBlock.model_validate(block)
        save_block_locked(
            session, current, run, {**block, "status": "running", "finished_at": None}
        )
    with factory() as session:
        service = trace_service(session, settings)
        first = service.page(conversation.id, accepted.run.id)
        assert len(first.items) == 50 and first.has_more
        second = service.page(conversation.id, accepted.run.id, first.next_before_seq)
        third = service.page(conversation.id, accepted.run.id, second.next_before_seq)
        assert len({item.block_id for item in [*first.items, *second.items, *third.items]}) == 105
        assert len(third.items) == 5 and not third.has_more
        assert (
            next(item for item in third.items if item.block_id == terminal.block_id).status
            == "succeeded"
        )
        assert len(service.page(conversation.id, accepted.run.id, limit=100).items) == 100


def test_sse_replay_real_authority_gap_and_deletion(task_workspace):
    from short_drama.api.v1.assistant.conversations.events import conversation_events

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    runtime_for(factory, settings, RuntimeTransport([{"text": "雨🙂声"}])).execute(
        accepted.run.id, 1
    )
    with factory.begin() as session:
        session.add(
            UserSession(
                id=1,
                user_id=1,
                token_hash=token_hash("trace-cookie"),
                csrf_hash="hash",
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )

    async def connected():
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=factory, settings=settings)),
        state=SimpleNamespace(actor=task_actor()),
        is_disconnected=connected,
    )

    async def run():
        first = await conversation_events(conversation.id, request, 0, None)
        duplicate = await conversation_events(conversation.id, request, 0, None)
        iterator, replay = first.body_iterator, duplicate.body_iterator
        assert await anext(iterator) == await anext(replay)
        await replay.aclose()
        with factory() as session:
            service = task_service(session, settings)
            current = service.get_conversation(conversation.id)
            service.delete_global_conversation(
                conversation.id, {"row_version": current.row_version}, uuid4().hex
            )
        remainder = "".join([item async for item in iterator])
        assert "event: access-ended" in remainder

    asyncio.run(run())
    # A separate active conversation exercises the existing gap/resync contract.
    with factory() as session:
        other = task_service(session, settings).create_global_conversation({}, uuid4().hex)
        accepted = task_service(session, settings).send_task_message(
            other.id, {"content": "缺口", "model_config_id": task_workspace[2]}, uuid4().hex
        )
    with factory.begin() as session:
        session.execute(
            delete(AgentEvent).where(AgentEvent.conversation_id == other.id, AgentEvent.seq == 1)
        )
    with factory() as session:
        with pytest.raises(WorkflowError, match="事件游标"):
            trace_service(session, settings).events(other.id, 0)


def test_wait_resume_keeps_one_operation_and_records_boundary_states(task_workspace):
    from tests.integration.standard.assistant.test_agent_questions import (
        answer,
        execute_next,
        wait_question,
    )

    conversation, accepted, operation_id = wait_question(task_workspace)
    factory, settings, _ = task_workspace
    with factory() as session:
        page = trace_service(session, settings).page(conversation.id, accepted.run.id)
        assert any(item.kind == "wait" and item.status == "waiting_input" for item in page.items)
    answer(factory, settings, operation_id)
    execute_next(task_workspace, accepted, RuntimeTransport([{"text": "继续完成"}]))
    with factory() as session:
        service = trace_service(session, settings)
        page = service.page(conversation.id, accepted.run.id)
        assert (
            next(item for item in page.items if item.operation_id == operation_id).status
            == "succeeded"
        )
        states = [
            event.payload["status"]
            for event in service.events(conversation.id)
            if event.event_type == "trace.block"
            and event.payload["block_id"] == str(accepted.run.id)
        ]
        assert states == ["waiting_input", "running", "succeeded"]


def test_native_sdk_command_trace_separates_stdout_stderr_and_archives(resources, monkeypatch):
    import httpx
    from opensandbox import SandboxSync
    from opensandbox.config import ConnectionConfigSync

    from short_drama.agent.sandbox.workspace import SandboxWorkspaceService

    factory, settings, model, storage = resources
    settings.agent_sandbox_command_timeout_seconds, settings.agent_sandbox_output_max_bytes = (
        45,
        65536,
    )
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(
                202,
                json={
                    "id": "trace-sandbox",
                    "status": {"state": "Running"},
                    "createdAt": "2026-10-29T00:00:00Z",
                    "entrypoint": ["tail", "-f", "/dev/null"],
                    "metadata": {},
                    "expiresAt": "2026-10-29T00:10:00Z",
                },
            )
        if "/endpoints/" in request.url.path:
            return httpx.Response(200, json={"endpoint": "execd.test:44772", "headers": {}})
        if request.url.path == "/command":
            events = [
                {"type": "stdout", "text": "雨" * 24000, "timestamp": 1},
                {"type": "stderr", "text": "错误🙂 token=hidden-value", "timestamp": 2},
                {"type": "execution_complete", "execution_time": 1, "timestamp": 3},
            ]
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text="".join(
                    "data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in events
                ),
            )
        raise AssertionError(str(request.url))

    transport = httpx.MockTransport(respond)
    connection = ConnectionConfigSync(
        domain="sandbox.test", transport=transport, disable_metrics=True
    )
    sandbox = SandboxSync.create(
        "python:3.12", connection_config=connection, skip_health_check=True
    )
    monkeypatch.setattr(SandboxWorkspaceService, "ensure", lambda *_args, **_kwargs: sandbox)
    try:
        conversation, accepted = new_task((factory, settings, model))
        runtime_for(
            factory,
            settings,
            RuntimeTransport(
                [
                    calls(
                        (
                            "command",
                            "execute",
                            {"command": "npm install fixture && node fixture.mjs"},
                        )
                    ),
                    {"text": "日志已检查"},
                ]
            ),
        ).execute(accepted.run.id, 1)
        with factory() as session:
            service = trace_service(session, settings, storage)
            page = service.page(conversation.id, accepted.run.id)
            block = next(item for item in page.items if item.tool_name == "execute")
            assert block.target.display_name == "npm install fixture && node fixture.mjs"
            assert (
                block.channels["stdout"].truncated and block.channels["stdout"].total_bytes == 72001
            )
            assert "错误🙂" in block.channels["stderr"].preview
            assert len(block.channels["stdout"].preview.encode("utf-8")) <= 16384
            assert any(item.name == "stdout" and item.kind == "archive" for item in block.resources)
            frames = service.events(conversation.id)
            assert "hidden-value" not in json.dumps(
                [event.model_dump(mode="json") for event in frames]
            )
            assert any(
                event.event_type == "trace.delta" and event.payload["channel"] == "stderr"
                for event in frames
            )
            assert all(
                len(event.payload["content"].encode("utf-8")) <= 16384
                for event in frames
                if event.event_type == "sandbox.log"
            )
        command = next(
            json.loads(request.content) for request in requests if request.url.path == "/command"
        )
        assert command["command"] == "npm install fixture && node fixture.mjs"
    finally:
        sandbox.close()
        transport.close()


def test_trace_http_uses_cookie_authority_and_effective_skill_resource_slice(resources):
    from fastapi.testclient import TestClient

    from short_drama.core.config import Settings
    from short_drama.main import create_app
    from tests.support.assistant_runtime import tool_result

    factory, settings, model, storage = resources
    skill = create_skill(
        resources,
        instructions="按[模板](assets/token=trace-secret.txt)查看实际分段。",
        resources=[
            {
                "path": "assets/token=trace-secret.txt",
                "kind": "template",
                "content": "A" * 100 + "B" * 4000 + "C" * 1900,
            }
        ],
    )
    conversation, accepted = new_task((factory, settings, model))

    def reads(body):
        activation = tool_result(body, "skill")["activation_id"]
        return calls(
            (
                "slice",
                "runtime__read_skill_resource",
                {
                    "activation_id": activation,
                    "path": "assets/token=trace-secret.txt",
                    "offset": 100,
                },
            ),
            (
                "default",
                "runtime__read_skill_resource",
                {"activation_id": activation, "path": "assets/token=trace-secret.txt"},
            ),
        )

    wire = RuntimeTransport(
        [
            calls(
                (
                    "skill",
                    "runtime__load_skill",
                    {"name": skill.name, "content_version": "1", "purpose": "查看实际分段"},
                )
            ),
            reads,
            {"text": "HTTP轨迹"},
        ]
    )
    runtime_for(
        factory,
        settings,
        wire,
    ).execute(accepted.run.id, 1)
    assert tool_result(wire.main_requests[-1], "slice")["content"] == "B" * 4000
    assert tool_result(wire.main_requests[-1], "default")["content"] == "A" * 100 + "B" * 3900
    with factory.begin() as session:
        session.add(
            UserSession(
                id=7,
                user_id=1,
                token_hash=token_hash("trace-http-cookie"),
                csrf_hash="hash",
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    app = create_app(
        Settings(
            _env_file=None,
            auth_enabled=True,
            agent_enabled=True,
            public_origin="https://testserver",
        )
    )
    app.state.session_factory = factory
    app.state.storage = storage
    client = TestClient(app, base_url="https://testserver")
    try:
        path = f"/api/v1/assistant/conversations/{conversation.id}/runs/{accepted.run.id}/trace"
        assert client.get(path).status_code == 401
        client.cookies.set("sd_session", "trace-http-cookie")
        assert client.get(path + "?limit=101").status_code == 422
        assert client.get(path + "?limit=0").status_code == 422
        page = client.get(path)
        assert page.status_code == 200 and page.json()["items"][0]["title"]
        assert "trace-secret" not in page.text
        blocks = [
            item
            for item in page.json()["items"]
            if item["tool_name"] == "runtime__read_skill_resource"
        ]
        assert len(blocks) == 2
        for block in blocks:
            offset = block["target"]["offset"] or 0
            resource = client.get(
                path + f"/{block['block_id']}/resources/{block['resources'][0]['resource_id']}"
            )
            assert resource.status_code == 200
            assert "trace-secret" not in resource.text
            expected = "B" * 4000 if offset == 100 else "A" * 100 + "B" * 3900
            assert resource.json()["content"] == expected
            assert resource.json()["offset"] == offset and resource.json()["length"] == 4000
            assert block["target"]["offset"] == offset and block["target"]["length"] == 4000
        assert client.get(path + "/9/resources/9?bucket=private&key=secret").status_code == 404
    finally:
        client.close()
