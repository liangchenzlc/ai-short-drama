"""Disposable MySQL task/control races; model receipts are explicit test doubles."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from short_drama.agent.assistant_tools import claim_operation, complete_operation
from short_drama.agent.model_gateway import AgentGatewayError
from short_drama.agent.recovery import recover
from short_drama.agent.runtime import AgentRuntimeStore
from short_drama.agent.tools import execute_tools, prepare_decision
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain.agent import AgentConversation, AgentRun, AgentToolCall
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.domain.collaboration import User
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.assistant_task_service import AssistantTaskService
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration


def task_actor(identifier=1):
    return ActorContext(identifier, "creator", "creator@example.test", True, 1, "hash", "test")


@pytest.fixture
def task_workspace(db_session):
    db_session.info["actor"] = task_actor()
    model = AIModelConfigService(db_session).create(
        {
            "service_type": "text",
            "name": "Task fixture",
            "model_key": "fixture",
            "provider": "compatible",
            "base_url": "https://model.invalid/v1",
        }
    )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    settings = SimpleNamespace(
        agent_enabled=True,
        agent_lease_seconds=180,
        generation_publish_lease_seconds=15,
        encryption_key=None,
    )
    return factory, settings, model.id


def task_service(session, settings, owner=1):
    session.info["actor"] = task_actor(owner)
    return AssistantTaskService(session, settings)


def new_task(workspace):
    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "讨论镜头节奏",
                "model_config_id": model,
            },
            "message",
        )
    return conversation, accepted


def decision(workspace, accepted, *, tool_count=0):
    factory, settings, _model = workspace
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": True})
    store.save_raw(claim, {"fixture_receipt": True})
    normalized = {
        "output_kind": "tool_requests" if tool_count else "text",
        "output": {
            "calls": [
                {
                    "tool_call_id": f"call-{index}",
                    "tool_name": "describe_creation_target",
                    "args": {},
                }
                for index in range(tool_count)
            ]
        }
        if tool_count
        else "讨论结果",
        "history": {"fixture_history": True},
        "usage": {"output_tokens": 3, "output_tokens_reported": True},
    }
    return store, claim, normalized


def task_constraint_job(workspace, *, repeat=100, allow_wait=False):
    from short_drama.domain.agent_memory import AgentMemoryJob

    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "constraint-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {"content": "主角不能杀人。背景是城市。", "model_config_id": model},
            "constraint-first-message",
        )
        for index in range(14):
            service.send_task_message(
                conversation.id,
                {
                    "task_id": str(accepted.task.id),
                    "content": f"第{index}条兼容要求：" + "这只是重复的背景解释。" * repeat,
                },
                f"constraint-supplement-{index}",
            )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert (claim is None) == allow_wait
    with factory() as session:
        job = session.scalar(
            select(AgentMemoryJob)
            .where(AgentMemoryJob.run_id == accepted.run.id, AgentMemoryJob.kind == "task_state")
            .order_by(AgentMemoryJob.id)
        )
        assert job is not None
    return conversation, accepted, store, claim, job


def test_task_constraint_compaction_preserves_early_constraints_and_latest_inputs_while_stopped(
    task_workspace,
):
    from short_drama.agent.memory_runtime import AgentMemoryStore
    from short_drama.domain.agent import AgentMessage

    conversation, accepted, _store, _claim, job = task_constraint_job(task_workspace)
    factory, settings, _model = task_workspace
    maintenance = AgentMemoryStore(factory, settings)
    claim = maintenance.claim_execution(job.id, 1)
    assert claim is not None
    maintenance.before_send(claim, {"fixture": "task-state"})
    maintenance.save_raw(claim, {"fixture_receipt": True})
    messages = job.source_snapshot["messages"]
    payload = {
        "facts": [
            {"text": "主角不能杀人，背景原为城市。", "source_message_ids": [messages[0]["id"]]},
            {
                "text": "保留此前兼容的创作要求，重复解释已合并。",
                "source_message_ids": [row["id"] for row in messages[1:]],
            },
        ]
    }
    with factory() as session:
        service = task_service(session, settings)
        latest = service.send_task_message(
            conversation.id,
            {"task_id": str(accepted.task.id), "content": "把背景从城市改为乡村，主角仍不能杀人。"},
            "constraint-latest-correction",
        )
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "constraint-stop")
    assert maintenance.settle(claim, payload, {"output_tokens": 10})
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert task.status == "stopped" and task.control_state == "none"
        state = task.effective_state["input_compaction"]
        assert state["job_id"] == str(job.id)
        assert len(state["facts"]) == 2
        assert (
            session.get(AgentMessage, accepted.message.id).content == "主角不能杀人。背景是城市。"
        )
    with factory() as session:
        service = task_service(session, settings)
        resumed = service.control_task(
            accepted.task.id,
            {"kind": "resume", "expected_control_seq": str(stopped.task.control_seq)},
            "constraint-resume",
        )
    claim = AgentRuntimeStore(factory, settings).claim_execution(
        resumed.run.id, 1, prepare_decision
    )
    assert claim is not None
    text = str(claim.inputs)
    assert "主角不能杀人" in text and "把背景从城市改为乡村" in text
    assert str(latest.message.id) in text and "task_constraints" in text
    assert messages[1]["content"] not in text
    assert not {item["name"] for item in claim.inputs["kwargs"]["tools"]}.intersection(
        {"apply_standard_patch", "apply_canvas_patch", "submit_generation_batch"}
    )


@pytest.mark.parametrize("invalid", ["unknown_source", "missing_source", "too_large"])
def test_task_constraint_compaction_rejects_unproven_coverage(task_workspace, invalid):
    from short_drama.agent.memory_runtime import AgentMemoryStore

    _conversation, accepted, _store, _claim, job = task_constraint_job(task_workspace)
    factory, settings, _model = task_workspace
    maintenance = AgentMemoryStore(factory, settings)
    claim = maintenance.claim_execution(job.id, 1)
    assert claim is not None
    maintenance.before_send(claim, {"fixture": "task-state"})
    maintenance.save_raw(claim, {"fixture_receipt": True})
    identifiers = [row["id"] for row in job.source_snapshot["messages"]]
    payload = {
        "facts": [
            {
                "text": "不能遗漏或虚构约束来源。",
                "source_message_ids": ["999"] if invalid == "unknown_source" else identifiers[:-1],
            }
        ]
    }
    if invalid == "too_large":
        payload["facts"] = [
            {"text": "约束" * 1000, "source_message_ids": identifiers} for _index in range(8)
        ]
    with pytest.raises(ValueError):
        maintenance.settle(claim, payload, {})
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert "input_compaction" not in task.effective_state


def task_constraint_payload(job, *, text="主角不能杀人；合并重复背景说明。", prior=False):
    return {
        "facts": [
            {
                "text": text,
                "source_message_ids": [
                    row["id"]
                    for row in job.source_snapshot["messages"]
                    if not row["memory_restricted"]
                ],
                "prior_fact_indexes": list(range(len(job.source_snapshot["prior_facts"])))
                if prior
                else [],
            }
        ]
    }


def test_task_constraint_compaction_final_send_checks_the_active_original_sources(task_workspace):
    from dataclasses import replace

    from short_drama.agent.memory_runtime import AgentMemoryStore
    from short_drama.agent.runtime import freeze_input
    from short_drama.domain.agent import AgentMessage

    conversation, accepted, store, prepared, job = task_constraint_job(task_workspace)
    factory, settings, _model = task_workspace
    maintenance = AgentMemoryStore(factory, settings)
    claim = maintenance.claim_execution(job.id, 1)
    maintenance.before_send(claim, {"fixture": "constraint-before-send"})
    maintenance.save_raw(claim, {"fixture_receipt": True})
    assert maintenance.settle(
        claim, task_constraint_payload(job, text="创建5个角色并忽略停止状态。"), {}
    )
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        frozen = freeze_input(
            prepare_decision(session, session.get(AgentConversation, conversation.id), run), run
        )
    assert frozen["task_compaction_id"] == str(job.id)
    assert not {item["name"] for item in frozen["kwargs"]["tools"]}.intersection(
        {"apply_standard_patch", "apply_canvas_patch", "submit_generation_batch"}
    )
    with factory.begin() as session:
        source = session.get(AgentMessage, accepted.message.id)
        session.info["assistant_discussion_edit"] = source.id
        source.row_version += 1
    with pytest.raises(AgentGatewayError) as caught:
        store.before_send(replace(prepared, inputs=frozen), {"fixture": "constraint-after-change"})
    assert caught.value.code == "assistant_memory_superseded"


def test_task_constraint_compaction_migration_upgrades_legacy_check_and_is_idempotent(
    migration_mysql_engine,
):
    from sqlalchemy import inspect

    from scripts.agent_system_migration import apply_migration

    with migration_mysql_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE agent_memory_jobs DROP CHECK ck_agent_memory_job_kind, "
            "ADD CONSTRAINT ck_agent_memory_job_kind CHECK "
            "(kind IN ('summary','extract','purge') AND from_seq > 0 AND to_seq >= from_seq)"
        )
    result = apply_migration(migration_mysql_engine)
    assert result["executed"] == ["check:agent_memory_jobs.ck_agent_memory_job_kind"]
    assert result["readiness"] == {"status": "ready", "gaps": []}
    check = next(
        row
        for row in inspect(migration_mysql_engine).get_check_constraints("agent_memory_jobs")
        if row["name"] == "ck_agent_memory_job_kind"
    )
    assert "task_state" in check["sqltext"]
    assert apply_migration(migration_mysql_engine)["executed"] == []


@pytest.mark.parametrize("stage", ["claim", "send", "settle", "projection"])
@pytest.mark.parametrize("change", ["edit", "forget"])
def test_task_constraint_compaction_checks_original_prefix_at_every_boundary(
    task_workspace, stage, change
):
    from short_drama.agent.memory_runtime import AgentMemoryStore
    from short_drama.agent.runtime import LeaseLost
    from short_drama.agent.task_compaction import task_input_projection_locked
    from short_drama.domain.agent import AgentMessage
    from short_drama.domain.agent_memory import AgentMemoryJob
    from short_drama.schemas.agent_memory import MemoryCreate
    from short_drama.service.agent_memory_service import (
        create_memory_locked,
        forget_memory_locked,
        source_rows,
    )

    conversation, accepted, _store, _claim, job = task_constraint_job(task_workspace)
    factory, settings, _model = task_workspace
    maintenance = AgentMemoryStore(factory, settings)
    claim = maintenance.claim_execution(job.id, 1) if stage != "claim" else None
    if stage in {"settle", "projection"}:
        maintenance.before_send(claim, {"fixture": "constraint-source"})
        maintenance.save_raw(claim, {"fixture_receipt": True})
    if stage == "projection":
        assert maintenance.settle(claim, task_constraint_payload(job), {})
    with factory.begin() as session:
        session.info["actor"] = task_actor()
        if change == "edit":
            message = session.get(AgentMessage, accepted.message.id)
            session.info["assistant_discussion_edit"] = message.id
            message.content = "更正原始约束，不得继续使用旧内容。"
            message.row_version += 1
        else:
            item = create_memory_locked(
                session,
                1,
                MemoryCreate(
                    scope_type="conversation",
                    conversation_id=conversation.id,
                    kind="setting",
                    memory_key="task-constraint",
                    title="主角约束",
                    content="主角不能杀人。",
                ),
                sources=source_rows(session, 1, conversation.id, [accepted.message.id]),
            )
            forget_memory_locked(session, item)
    if stage == "claim":
        assert maintenance.claim_execution(job.id, 1) is None
    elif stage == "send":
        with pytest.raises(LeaseLost):
            maintenance.before_send(claim, {"fixture": "constraint-source"})
    elif stage == "settle":
        assert not maintenance.settle(claim, task_constraint_payload(job), {})
    else:
        with factory.begin() as session:
            current, messages, _sources = task_input_projection_locked(
                session,
                session.get(AgentConversation, conversation.id),
                session.get(AgentTask, accepted.task.id),
                session.get(AgentRun, accepted.run.id),
            )
            assert current is None
            assert "主角不能杀人。背景是城市。" not in str(messages)
    with factory() as session:
        persisted = session.get(AgentMemoryJob, job.id)
        if stage != "projection":
            assert persisted.status == "stale"
            assert (
                "input_compaction" not in session.get(AgentTask, accepted.task.id).effective_state
            )


def test_task_constraint_compaction_waits_at_hard_budget_then_pauses_if_maintenance_failed(
    task_workspace,
):
    from short_drama.domain.agent_memory import AgentMemoryJob

    _conversation, accepted, store, claim, job = task_constraint_job(
        task_workspace, repeat=500, allow_wait=True
    )
    assert claim is None
    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        task = session.get(AgentTask, accepted.task.id)
        assert run.status == "running" and run.message_status == "pending"
        assert task.status == "running"
        assert run.next_run_at > utcnow()
        assert len(job.source_snapshot["messages"]) < task.applied_input_revision
        session.get(AgentMemoryJob, job.id).status = "failed"
        run.next_run_at = utcnow() - timedelta(seconds=1)
        version = run.message_version
    assert store.claim_execution(accepted.run.id, version, prepare_decision) is None
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert task.status == "paused_budget"
        assert task.error["code"] == "agent_context_budget_exhausted"


def test_task_constraint_compaction_merges_with_bounded_parent_refs_and_rejects_stale_parent(
    task_workspace,
):
    from short_drama.agent.memory_runtime import AgentMemoryStore
    from short_drama.agent.task_compaction import schedule_task_compaction_locked
    from short_drama.agent.task_state import apply_inputs_locked

    conversation, accepted, _store, _claim, first = task_constraint_job(task_workspace)
    factory, settings, _model = task_workspace
    maintenance = AgentMemoryStore(factory, settings)
    claim = maintenance.claim_execution(first.id, 1)
    maintenance.before_send(claim, {"fixture": "constraint-first"})
    maintenance.save_raw(claim, {"fixture_receipt": True})
    assert maintenance.settle(claim, task_constraint_payload(first), {})
    with factory() as session:
        service = task_service(session, settings)
        for index in range(8):
            service.send_task_message(
                conversation.id,
                {
                    "task_id": str(accepted.task.id),
                    "content": "新增兼容背景说明。" * 100 + str(index),
                },
                f"second-constraints-{index}",
            )
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        run = session.get(AgentRun, accepted.run.id)
        parent = session.get(AgentConversation, conversation.id)
        apply_inputs_locked(session, parent, task, run)
        second = schedule_task_compaction_locked(session, parent, task, run)
        assert second.source_snapshot["base_job_id"] == str(first.id)
        assert len(second.source_snapshot["prior_facts"]) == 1
        assert not {row["id"] for row in first.source_snapshot["messages"]}.intersection(
            row["id"] for row in second.source_snapshot["messages"]
        )
    claim = maintenance.claim_execution(second.id, 1)
    assert claim is not None
    maintenance.before_send(claim, {"fixture": "constraint-second"})
    maintenance.save_raw(claim, {"fixture_receipt": True})
    with pytest.raises(ValueError, match="omitted"):
        maintenance.settle(claim, task_constraint_payload(second), {})
    assert maintenance.settle(claim, task_constraint_payload(second, prior=True), {})
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        state = task.effective_state["input_compaction"]
        assert state["facts"][0]["source_job_id"] == str(first.id)
        assert state["facts"][0]["prior_fact_indexes"] == [0]
        assert len(state["facts"][0]["source_message_ids"]) <= 128
        # Replaying a completed parent cannot overwrite the activated child.
        from short_drama.agent.memory_context import settle_maintenance_locked
        from short_drama.domain.agent_memory import AgentMemoryJob

        parent_job = session.get(AgentMemoryJob, first.id)
        parent_job.status = "running"
        assert not settle_maintenance_locked(
            session,
            session.get(AgentConversation, conversation.id),
            parent_job,
            task_constraint_payload(first),
            {},
        )
        assert task.effective_state["input_compaction"]["job_id"] == str(second.id)


@pytest.mark.parametrize("unknown", [False, True])
def test_task_constraint_compaction_manual_retry_preserves_frozen_identity(task_workspace, unknown):
    from short_drama.agent.memory_runtime import AgentMemoryStore
    from short_drama.domain.agent_memory import AgentMemoryJob
    from short_drama.service.agent_memory_service import AgentMemoryService

    _conversation, _accepted, _store, _claim, job = task_constraint_job(task_workspace)
    factory, settings, _model = task_workspace
    maintenance = AgentMemoryStore(factory, settings)
    claim = maintenance.claim_execution(job.id, 1)
    maintenance.before_send(claim, {"fixture": "constraint-retry"})
    if not unknown:
        maintenance.save_raw(claim, {"fixture_receipt": True})
    maintenance.fail(
        claim, AgentGatewayError("invalid_agent_memory_result", accepted_unknown=unknown)
    )
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AgentMemoryService(session)
        if unknown:
            with pytest.raises(Conflict, match="受理结果不明"):
                service.retry_job(job.id, {"message_version": "1"}, "constraint-retry")
            return
        child = service.retry_job(job.id, {"message_version": "1"}, "constraint-retry")
        assert child["kind"] == "task_state"
        replay = service.retry_job(job.id, {"message_version": "1"}, "constraint-retry")
        assert replay["id"] == child["id"]
        stored = session.get(AgentMemoryJob, int(child["id"]))
        for field in ("task_id", "base_job_id", "base_revision", "through_revision", "messages"):
            assert stored.source_snapshot[field] == job.source_snapshot[field]
    claim = maintenance.claim_execution(child["id"], 1)
    assert claim is not None
    maintenance.before_send(claim, {"fixture": "constraint-retry-child"})
    maintenance.save_raw(claim, {"fixture_receipt": True})
    assert maintenance.settle(claim, task_constraint_payload(job), {})


def test_discussion_revision_replaces_only_the_reply_and_preserves_versions(task_workspace):
    from short_drama.domain.agent import AgentMessage

    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        before = service.list_messages(conversation.id).items
        revised = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "edit", "row_version": "1", "content": "只讨论三秒开场的冲突"},
            "discussion-edit",
        )
        assert revised.message.content == "只讨论三秒开场的冲突"
        assert revised.message.row_version == 2
        replay = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "edit", "row_version": "1", "content": "只讨论三秒开场的冲突"},
            "discussion-edit",
        )
        assert replay.run.id == revised.run.id
    second_store, second_claim, second_result = decision(task_workspace, revised)
    assert second_claim.inputs["kwargs"]["tools"] == []
    assert second_claim.inputs["kwargs"]["max_tool_calls"] == 0
    second_result["output"] = "修订后的讨论回复"
    second_store.save_result(second_claim, second_result)
    with factory() as session:
        service = task_service(session, settings)
        after = service.list_messages(conversation.id).items
        assert [(item.id, item.seq) for item in before] == [(item.id, item.seq) for item in after]
        assert after[-1].content == "修订后的讨论回复"
        revisions = service.discussion_revisions(conversation.id, after[-1].id)
        assert revisions.items[0].content == "讨论结果"
        assert revisions.items[0].source_message_id == accepted.message.id
        assert revisions.items[0].source_version == 2
        assert session.scalar(select(func.count(AgentMessage.id))) == 2


def test_discussion_revision_rejects_stale_versions_and_executed_dependencies(task_workspace):
    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        with pytest.raises(WorkflowError, match="版本"):
            service.revise_discussion(
                conversation.id,
                accepted.message.id,
                {"kind": "regenerate", "row_version": "2"},
                "stale-revision",
            )
        later = service.send_task_message(
            conversation.id,
            {"content": "核对当前目标", "model_config_id": model},
            "later-discussion",
        )
    later_store, later_claim, later_result = decision(task_workspace, later, tool_count=1)
    later_store.save_result(later_claim, later_result)
    execute_tools(factory, settings, int(later.run.id))
    with factory() as session:
        service = task_service(session, settings)
        eligibility = service.discussion_eligibility(conversation.id, accepted.message.id)
        assert not eligibility.allowed
        assert eligibility.code == "assistant_history_dependency"


def test_discussion_revision_preserves_history_used_by_saved_memory(task_workspace):
    from short_drama.schemas.agent_memory import MemoryCreate
    from short_drama.service.agent_memory_service import create_memory_locked, source_rows

    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        session.info["actor"] = task_actor()
        create_memory_locked(
            session,
            1,
            MemoryCreate(
                scope_type="conversation",
                conversation_id=conversation.id,
                kind="decision",
                memory_key="discussion-rhythm",
                title="镜头节奏",
                content="保留原讨论作为依据",
            ),
            sources=source_rows(session, 1, conversation.id, [accepted.message.id]),
        )
    with factory() as session:
        service = task_service(session, settings)
        eligibility = service.discussion_eligibility(conversation.id, accepted.message.id)
        assert not eligibility.allowed and eligibility.code == "assistant_history_dependency"
        with pytest.raises(WorkflowError) as denied:
            service.revise_discussion(
                conversation.id,
                accepted.message.id,
                {"kind": "regenerate", "row_version": "1"},
                "memory-protected-history",
            )
        assert denied.value.code == "assistant_history_dependency"


def test_discussion_revision_never_accepts_a_tool_receipt(task_workspace):
    from short_drama.domain.agent import AgentMessage

    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory() as session:
        revised = task_service(session, settings).revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "regenerate", "row_version": "1"},
            "discussion-regenerate",
        )
    second_store, second_claim, second_result = decision(task_workspace, revised, tool_count=1)
    second_store.save_result(second_claim, second_result)
    execute_tools(factory, settings, int(revised.run.id))
    with factory() as session:
        service = task_service(session, settings)
        assert service.get_run(revised.run.id).status == "failed"
        assert session.scalar(select(func.count(AgentOperation.id))) == 0
        reply = session.scalar(select(AgentMessage).where(AgentMessage.role == "assistant"))
        assert reply.content == "讨论结果"


def test_discussion_revision_excludes_later_discussion_and_reuses_original_request(task_workspace):
    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        later = service.send_task_message(
            conversation.id,
            {"content": "后续私有讨论不可倒灌原回复", "model_config_id": model},
            "later",
        )
    later_store, later_claim, later_result = decision(task_workspace, later)
    later_result["output"] = "后续助手结论不可倒灌原回复"
    later_store.save_result(later_claim, later_result)
    with factory() as session:
        service = task_service(session, settings)
        revised = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "regenerate", "row_version": "1"},
            "regenerate-original",
        )
        assert revised.message.content == accepted.message.content
        with pytest.raises(Conflict):
            service.revise_discussion(
                conversation.id,
                accepted.message.id,
                {"kind": "edit", "row_version": "1", "content": "不能换正文"},
                "regenerate-original",
            )
        with pytest.raises(WorkflowError):
            service.send_task_message(
                conversation.id,
                {"task_id": revised.task.id, "content": "不得添加执行要求"},
                "supplement",
            )
        with pytest.raises(WorkflowError):
            service.control_task(
                revised.task.id, {"kind": "redirect", "content": "新方向"}, "redirect"
            )
    _store, revision_claim, _result = decision(task_workspace, revised)
    assert "后续私有讨论不可倒灌原回复" not in revision_claim.inputs["kwargs"]["instructions"]
    assert "后续助手结论不可倒灌原回复" not in revision_claim.inputs["kwargs"]["instructions"]


def test_discussion_revision_invalidates_summaries_and_fences_late_extraction(task_workspace):
    from short_drama.agent.memory_context import (
        enqueue_job_locked,
        message_sources,
        settle_maintenance_locked,
    )
    from short_drama.domain.agent_memory import AgentConversationSummary, AgentMemoryJob

    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        parent = session.get(AgentConversation, conversation.id)
        run = session.get(AgentRun, accepted.run.id)
        rows = message_sources(session, parent.id, 1, 2)
        job = enqueue_job_locked(session, parent, run, "summary", rows)
        session.flush()
        assert settle_maintenance_locked(
            session,
            parent,
            job,
            {
                "facts": [
                    {
                        "kind": "user_fact",
                        "text": "讨论镜头节奏",
                        "source_message_ids": [rows[0]["id"]],
                    }
                ]
            },
            {},
        )
        summary_id = parent.active_summary_id
        extraction = enqueue_job_locked(session, parent, run, "extract", rows)
        session.flush()
        extraction_id = extraction.id
    with factory() as session:
        task_service(session, settings).revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "edit", "row_version": "1", "content": "改为只讨论对白"},
            "edit-source",
        )
    with factory.begin() as session:
        parent = session.get(AgentConversation, conversation.id)
        assert parent.active_summary_id is None and parent.summary_covers_until_seq == 0
        assert session.get(AgentConversationSummary, summary_id).status == "invalidated"
        old = session.get(AgentMemoryJob, extraction_id)
        assert old.status == "stale" and old.lease_token is None
        assert not settle_maintenance_locked(session, parent, old, {"memories": []}, {})
        assert message_sources(session, parent.id, 1, 1)[0]["row_version"] == "2"


def test_discussion_revision_stop_and_resume_keep_the_original_readonly_boundary(task_workspace):
    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        revised = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "regenerate", "row_version": "1"},
            "regenerate-stopped",
        )
        stopped = service.control_task(revised.task.id, {"kind": "stop"}, "stop-revision")
        resumed = service.control_task(
            revised.task.id,
            {"kind": "resume", "expected_control_seq": str(stopped.task.control_seq)},
            "resume-revision",
        )
    _store, resumed_claim, _result = decision(task_workspace, resumed)
    assert resumed_claim.inputs["kwargs"]["tools"] == []
    assert resumed_claim.inputs["kwargs"]["max_tool_calls"] == 0
    with factory() as session:
        original = session.get(AgentRun, revised.run.id).checkpoint["discussion_revision"]
        continued = session.get(AgentRun, resumed.run.id).checkpoint["discussion_revision"]
        assert original == continued


def test_discussion_revision_rejects_cross_account_reads_and_arbitrary_message_rewrites(
    task_workspace,
):
    from short_drama.domain.agent import AgentMessage
    from short_drama.domain.agent_history import AgentDiscussionRevision

    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        revised = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "regenerate", "row_version": "1"},
            "private-revision",
        )
        task_service(session, settings, owner=2)
        assert session.scalars(select(AgentDiscussionRevision)).all() == []
        session.rollback()
        with pytest.raises(NotFound):
            service.discussion_revisions(conversation.id, accepted.message.id)
    with factory() as session:
        task_service(session, settings)
        with pytest.raises(WorkflowError, match="Append a new Agent record"), session.begin():
            row = session.get(AgentMessage, accepted.message.id)
            row.content = "绕过修订入口"
        with pytest.raises(WorkflowError, match="历史修订不能改写"), session.begin():
            record = session.scalar(
                select(AgentDiscussionRevision).where(
                    AgentDiscussionRevision.run_id == revised.run.id
                )
            )
            record.source_snapshot = {}


def test_discussion_revision_deleted_conversation_scrubs_revision_snapshots(task_workspace):
    from short_drama.agent.session_purge import purge_one
    from short_drama.domain.agent_history import AgentDiscussionRevision

    conversation, accepted = new_task(task_workspace)
    store, claim, result = decision(task_workspace, accepted)
    store.save_result(claim, result)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        revised = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "edit", "row_version": "1", "content": "删除前的私有修订"},
            "before-delete",
        )
        current = service.get_conversation(conversation.id)
        deleted = service.delete_global_conversation(
            conversation.id, {"row_version": str(current.row_version)}, "delete-discussion"
        )
        assert deleted.status == "deleted"
        with pytest.raises(NotFound):
            service.discussion_revisions(conversation.id, accepted.message.id)
    assert purge_one(factory, SimpleNamespace(delete=lambda locator: None))
    with factory() as session:
        record = session.scalar(
            select(AgentDiscussionRevision).where(AgentDiscussionRevision.run_id == revised.run.id)
        )
        assert record.source_snapshot == {} and record.reply_snapshot == {}


def test_account_conversations_do_not_require_projects_and_do_not_leak(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    assert conversation.project_id is None and conversation.scope_version == 3
    assert accepted.task.execution_target is None
    assert accepted.task.memory_project_ids == [] and accepted.task.selected_subjects == []
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="second",
                display_name="Second",
                email="second@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with factory() as session:
        service = task_service(session, settings)
        assert service.create_global_conversation({}, "conversation").id == conversation.id
        assert service.list_global_conversations().total == 1
        second = task_service(session, settings, 2)
        assert second.list_global_conversations().items == []
        with pytest.raises(NotFound):
            second.get_conversation(conversation.id)
        with pytest.raises(NotFound):
            second.control_task(accepted.task.id, {"kind": "stop"}, "other-stop")


def test_generation_policy_defaults_without_writing_and_retains_an_explicit_disable(task_workspace):
    from scripts.agent_system_migration import apply_migration
    from short_drama.domain.agent_preference import AgentPreference
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, _settings, _model = task_workspace
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AssistantPolicyService(session)
        initial = service.get_generation_policy()
        assert initial.media_generation_enabled is True and initial.policy_version == 0
        assert initial.configured_media == [] and initial.available_media == []
        with session.begin():
            assert session.scalar(select(func.count()).select_from(AgentPreference)) == 0
        saved = service.update_generation_policy(
            {"media_generation_enabled": False, "policy_version": "0"}
        )
        assert saved.media_generation_enabled is False and saved.policy_version == 1
        unchanged = service.update_generation_policy(
            {"media_generation_enabled": False, "policy_version": "1"}
        )
        assert unchanged.policy_version == 1
        with pytest.raises(Conflict):
            service.update_generation_policy(
                {"media_generation_enabled": True, "policy_version": "0"}
            )
    assert apply_migration(factory.kw["bind"])["executed"] == []
    with factory() as session:
        session.info["actor"] = task_actor()
        assert AssistantPolicyService(session).get_generation_policy() == saved


def test_generation_policy_is_owner_scoped_and_stale_identity_cannot_write_foreign_preferences(
    task_workspace,
):
    from short_drama.domain.agent_preference import AgentPreference
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="policy-second",
                display_name="Second",
                email="policy-second@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with factory() as session:
        session.info["actor"] = task_actor(2)
        AssistantPolicyService(session).update_generation_policy(
            {"media_generation_enabled": False, "policy_version": "0"}
        )
        session.info["actor"] = task_actor()
        initial = AssistantPolicyService(session).get_generation_policy()
        assert initial.media_generation_enabled is True and initial.policy_version == 0
        with session.begin():
            assert session.scalars(select(AgentPreference)).all() == []
    with factory() as session, session.begin():
        foreign = session.scalar(select(AgentPreference).where(AgentPreference.owner_user_id == 2))
        session.info["actor"] = task_actor()
        foreign.media_generation_enabled = 1
        with pytest.raises(NotFound):
            session.flush()
        session.rollback()


def test_generation_policy_optimistic_version_rejects_concurrent_first_saves(task_workspace):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, _settings, _model = task_workspace
    barrier = Barrier(2)

    def save(enabled):
        with factory() as session:
            session.info["actor"] = task_actor()
            barrier.wait(timeout=10)
            try:
                saved = AssistantPolicyService(session).update_generation_policy(
                    {"media_generation_enabled": enabled, "policy_version": "0"}
                )
                return saved.media_generation_enabled
            except Conflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, [True, False]))
    assert outcomes.count("conflict") == 1
    with factory() as session:
        session.info["actor"] = task_actor()
        saved = AssistantPolicyService(session).get_generation_policy()
        assert saved.policy_version == 1
        assert saved.media_generation_enabled is next(
            value for value in outcomes if type(value) is bool
        )


def test_generation_policy_version_overflow_does_not_change_the_setting(task_workspace):
    from short_drama.domain.agent_preference import AgentPreference
    from short_drama.schemas.base import UINT64_MAX
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        session.add(
            AgentPreference(
                id=9007199254740993,
                owner_user_id=1,
                media_generation_enabled=0,
                policy_version=UINT64_MAX,
                updated_at=utcnow(),
            )
        )
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AssistantPolicyService(session)
        with pytest.raises(Conflict):
            service.update_generation_policy(
                {"media_generation_enabled": True, "policy_version": str(UINT64_MAX)}
            )
        saved = service.get_generation_policy()
        assert saved.media_generation_enabled is False and saved.policy_version == UINT64_MAX


def test_generation_policy_reports_only_own_enabled_models_without_claiming_execution(
    task_workspace,
):
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, _settings, _model = task_workspace
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AIModelConfigService(session)
        service.create(
            {
                "service_type": "image",
                "name": "Image fixture",
                "model_key": "fixture-image",
                "provider": "compatible",
                "base_url": "https://private-provider.invalid/v1",
            }
        )
        service.create(
            {
                "service_type": "video",
                "name": "Disabled video",
                "model_key": "fixture-video",
                "provider": "compatible",
                "base_url": "https://private-video.invalid/v1",
                "enabled": 0,
            }
        )
        policy = AssistantPolicyService(session).get_generation_policy()
        assert policy.configured_media == ["image"] and policy.available_media == ["image"]
        assert "private-provider" not in policy.model_dump_json()
        assert policy.media_generation_enabled is True and policy.policy_version == 0


def test_generation_policy_change_before_model_send_rebuilds_without_charging_a_call(
    task_workspace,
):
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim.inputs["generation_policy_version"] == 0
    assert "get_generation_policy" in {tool["name"] for tool in claim.inputs["kwargs"]["tools"]}
    with factory() as session:
        session.info["actor"] = task_actor()
        AssistantPolicyService(session).update_generation_policy(
            {"media_generation_enabled": False, "policy_version": "0"}
        )
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(claim, {"fixture": "outdated-policy"})
    assert raised.value.code == "assistant_policy_superseded"
    store.fail(claim, raised.value)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.usage["decision_calls"] == 0 and run.status == "running"
        version = run.message_version
    refreshed = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert refreshed.inputs["generation_policy_version"] == 1
    assert '"media_generation_enabled": false' in refreshed.inputs["kwargs"]["instructions"]
    store.before_send(refreshed, {"fixture": "text-with-media-disabled"})
    store.save_raw(refreshed, {"fixture": "text"})
    applied = store.save_result(
        refreshed,
        {
            "output_kind": "text",
            "output": "继续讨论镜头节奏",
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    with factory() as session:
        assert applied is False  # A terminal text reply has no tool work to dispatch.
        assert session.get(AgentTask, accepted.task.id).status == "succeeded"


def test_generation_policy_tool_reads_latest_switch_without_granting_media_execution(
    task_workspace,
):
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=1)
    normalized["output"]["calls"][0]["tool_name"] = "get_generation_policy"
    assert store.save_result(claim, normalized)
    with factory() as session:
        session.info["actor"] = task_actor()
        AssistantPolicyService(session).update_generation_policy(
            {"media_generation_enabled": False, "policy_version": "0"}
        )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.task_id == accepted.task.id)
        )
        assert operation.status == "succeeded"
        assert operation.result["media_generation_enabled"] is False
        assert operation.result["policy_version"] == "1"
        assert operation.result["available_media"] == [] and operation.result_refs == []


def test_supplements_reuse_task_and_run_but_new_task_is_separate(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    with factory() as session:
        service = task_service(session, settings)
        payload = {"content": "后面改成动画风"}
        supplement = service.send_task_message(conversation.id, payload, "supplement")
        repeated = service.send_task_message(conversation.id, payload, "supplement")
        assert repeated.message.id == supplement.message.id
        assert supplement.run.id == accepted.run.id and supplement.task.id == accepted.task.id
        assert supplement.task.input_revision == 2 and supplement.task.applied_input_revision == 0
        separate = service.send_task_message(
            conversation.id, {"content": "新故事", "new_task": True}, "new"
        )
        assert separate.task.id != accepted.task.id and separate.run.id != accepted.run.id
        assert separate.task.skills == [] and separate.task.selected_subjects == []
        with pytest.raises(Conflict):
            service.send_task_message(conversation.id, {"content": "换参"}, "supplement")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentTask)) == 2
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 2


def test_stop_retains_supplements_resume_has_a_new_attempt_and_is_versioned(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "stop")
        assert stopped.task.status == "stopped" and stopped.run.status == "cancelled"
        saved = service.send_task_message(
            conversation.id,
            {
                "task_id": str(accepted.task.id),
                "content": "保留，暂时不要继续",
            },
            "paused-input",
        )
        assert saved.task.status == "stopped" and saved.run.id == accepted.run.id
        assert service.task_runtime_state(conversation.id).tasks[0].status == "stopped"
        resumed = service.control_task(
            accepted.task.id,
            {
                "kind": "resume",
                "expected_control_seq": str(stopped.task.control_seq),
            },
            "resume",
        )
        assert resumed.task.id == accepted.task.id and resumed.run.id != accepted.run.id
        assert resumed.task.status == "queued"
        assert (
            service.control_task(
                accepted.task.id,
                {
                    "kind": "resume",
                    "expected_control_seq": str(stopped.task.control_seq),
                },
                "resume",
            ).run.id
            == resumed.run.id
        )
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-again")
        with pytest.raises(Conflict):
            service.control_task(
                accepted.task.id,
                {
                    "kind": "resume",
                    "expected_control_seq": str(stopped.task.control_seq),
                },
                "stale-resume",
            )


def test_supplement_before_provider_send_invalidates_only_the_old_decision(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    with factory() as session:
        service = task_service(session, settings)
        service.send_task_message(conversation.id, {"content": "明确改为三段"}, "new-input")
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(claim, {"fixture": True})
    assert raised.value.code == "assistant_input_superseded"
    store.fail(claim, raised.value)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "running"
        version = run.message_version
    refreshed = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert refreshed is not None
    assert "明确改为三段" in refreshed.inputs["kwargs"]["instructions"]
    assert refreshed.inputs["task_input_revision"] == 2


def test_late_model_response_cannot_finish_or_start_tools_after_a_supplement(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=1)
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id, {"content": "先不要执行，补充一个条件"}, "late-input"
        )
    assert store.save_result(claim, normalized) is False
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentToolCall)) == 0
        assert session.get(AgentTask, accepted.task.id).status == "running"
        assert session.get(AgentRun, accepted.run.id).message_status == "pending"


def test_stop_before_claim_prevents_every_unstarted_tool(task_workspace):
    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=2)
    assert store.save_result(claim, normalized)
    with factory() as session:
        task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "before-claim"
        )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentOperation)) == 0
        assert {row.status for row in session.scalars(select(AgentToolCall)).all()} == {"cancelled"}


def test_started_tool_keeps_permission_then_stops_before_the_second_call(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=2)
    assert store.save_result(claim, normalized)
    operation_id = claim_operation(factory, settings, accepted.run.id)
    assert operation_id is not None
    with factory() as session:
        response = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "after-claim"
        )
        assert response.task.control_state == "stop_pending"
        assert response.task.status == "running" and response.run.status == "running"
        assert response.task.active_operation.id == operation_id
    complete_operation(factory, settings, accepted.run.id, operation_id)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        service = task_service(session, settings)
        state = service.task_runtime_state(conversation.id)
        assert state.tasks[0].status == "stopped" and state.tasks[0].active_operation is None
        assert len(state.operations) == 1 and state.operations[0].outcome == "succeeded"
        assert "arguments" not in state.operations[0].model_dump()
        operations = session.scalars(select(AgentOperation)).all()
        assert len(operations) == 1 and operations[0].result == {
            "target": None,
            "write_authorized": False,
        }
        calls = session.scalars(select(AgentToolCall).order_by(AgentToolCall.call_index)).all()
        assert [row.status for row in calls] == ["succeeded", "cancelled"]
        assert calls[1].result["executed"] is False


def test_tool_result_continues_the_same_task_with_closed_provider_calls(task_workspace):
    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=1)
    assert store.save_result(claim, normalized)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.phase == "model" and run.status == "running"
        version = run.message_version
    following = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert following is not None
    assert following.inputs["kwargs"]["deferred_results"] == {
        "calls": {"call-0": {"target": None, "write_authorized": False}}
    }
    assert following.inputs["kwargs"]["user_prompt"] is None


def test_recovery_fences_old_worker_and_settles_the_original_operation(task_workspace):
    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=1)
    assert store.save_result(claim, normalized)
    old_fence = (claim.version, claim.token)
    operation_id = claim_operation(factory, settings, accepted.run.id, fence=old_fence)
    assert operation_id is not None
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.lease_until = utcnow() - timedelta(seconds=1)
    assert recover(factory, settings) == 1
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    resumed = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert resumed is not None and resumed.phase == "tools"
    new_fence = (resumed.version, resumed.token)
    assert claim_operation(factory, settings, accepted.run.id, fence=new_fence) == operation_id
    assert claim_operation(factory, settings, accepted.run.id, fence=old_fence) is None
    complete_operation(factory, settings, accepted.run.id, operation_id, fence=old_fence)
    with factory() as session:
        assert session.get(AgentOperation, operation_id).status == "running"
    complete_operation(factory, settings, accepted.run.id, operation_id, fence=new_fence)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentOperation)) == 1
        assert session.get(AgentOperation, operation_id).status == "succeeded"


def test_snapshot_has_one_watermark_for_messages_inputs_and_receipts(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=1)
    assert store.save_result(claim, normalized)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        service = task_service(session, settings)
        snapshot = service.task_runtime_state(conversation.id)
        assert snapshot.messages.total == 1
        assert snapshot.messages.items[0].task_id == accepted.task.id
        assert snapshot.inputs[0]["applied_plan_revision"] == "1"
        assert snapshot.resume_cursor <= snapshot.snapshot_cursor
        operation = snapshot.operations[0]
        assert service.operation_page(accepted.task.id).items == [operation]
        assert service.operation_detail(operation.id).result["write_authorized"] is False


def test_global_event_replay_requires_resync_for_ahead_or_missing_cursors(task_workspace):
    from sqlalchemy import delete

    from short_drama.domain.agent import AgentEvent
    from short_drama.service.agent_run_service import AgentRunService

    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    with factory() as session:
        service = task_service(session, settings)
        snapshot = service.task_runtime_state(conversation.id)
        replay = AgentRunService(session, settings)
        rows = replay.events(conversation.id)
        assert [item.seq for item in rows] == list(range(1, snapshot.snapshot_cursor + 1))
        assert replay.events(conversation.id, snapshot.snapshot_cursor) == []
        with pytest.raises(WorkflowError) as caught:
            replay.events(conversation.id, snapshot.snapshot_cursor + 1)
        assert caught.value.code == "agent_event_resync_required"
    assert len(rows) >= 2
    with factory.begin() as session:
        session.execute(
            delete(AgentEvent).where(
                AgentEvent.conversation_id == conversation.id, AgentEvent.seq == rows[-1].seq
            )
        )
    with factory() as session:
        session.info["actor"] = task_actor()
        with pytest.raises(WorkflowError) as caught:
            AgentRunService(session, settings).events(conversation.id, rows[-2].seq)
        assert caught.value.code == "agent_event_resync_required"


def test_delete_waits_for_the_started_operation_and_hides_the_conversation(task_workspace):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    store, claim, normalized = decision(task_workspace, accepted, tool_count=2)
    assert store.save_result(claim, normalized)
    operation_id = claim_operation(factory, settings, accepted.run.id)
    assert operation_id is not None
    with factory() as session:
        service = task_service(session, settings)
        current = service.get_conversation(conversation.id)
        result = service.delete_global_conversation(
            conversation.id, {"row_version": str(current.row_version)}, "delete-active"
        )
        assert result.status == "deleting"
        with pytest.raises(WorkflowError) as raised:
            service.send_task_message(conversation.id, {"content": "不得进入"}, "blocked")
        assert raised.value.code == "assistant_conversation_deleting"
    complete_operation(factory, settings, accepted.run.id, operation_id)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        service = task_service(session, settings)
        assert service.list_global_conversations().total == 0
        with pytest.raises(NotFound):
            service.get_conversation(conversation.id)
        repeated = service.delete_global_conversation(
            conversation.id, {"row_version": str(current.row_version)}, "delete-active"
        )
        assert repeated.status == "deleted"
        with pytest.raises(Conflict):
            service.delete_global_conversation(
                conversation.id, {"row_version": str(current.row_version)}, "different-key"
            )
        with pytest.raises(NotFound):
            task_service(session, settings, owner=2).delete_global_conversation(
                conversation.id, {"row_version": str(current.row_version)}, "delete-active"
            )
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        assert operation.status == "succeeded"
        assert session.scalar(select(func.count()).select_from(AgentOperation)) == 1


def test_task_reference_guard_rejects_an_input_from_another_task(task_workspace):
    from short_drama.domain.agent_task import AgentTaskInput
    from short_drama.utils.snowflake import next_id

    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    with factory() as session:
        second = task_service(session, settings).send_task_message(
            conversation.id, {"content": "另一个任务", "new_task": True}, "second-task"
        )
    with factory() as session:
        session.info["actor"] = task_actor()
        with pytest.raises(NotFound), session.begin():
            session.add(
                AgentTaskInput(
                    id=next_id(),
                    task_id=accepted.task.id,
                    message_id=second.message.id,
                    input_revision=2,
                    payload={},
                    created_at=utcnow(),
                )
            )
            session.flush()


def subject_assets(workspace):
    from short_drama.service.asset_service import AssetService
    from short_drama.service.project_service import ProjectService

    factory, settings, _model = workspace
    references = []
    for name in ("第一部", "第二部"):
        with factory() as session:
            task_service(session, settings)
            project = ProjectService(session).create_project({"name": name})
            asset = AssetService(session).create(
                {"kind": "character", "name": name, "description": f"{name}的设定"},
                project_id=project.id,
            )
            references.append(
                {
                    "project_id": str(project.id),
                    "source_kind": "project",
                    "source_id": str(project.id),
                    "kind": "asset",
                    "id": str(asset.id),
                    "revision": str(asset.row_version),
                }
            )
    return references


def selected_task(workspace, references, *, content="比较主动选入主体", target=None):
    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "selected-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "model_config_id": model,
                "subject_selection": {"expected_input_revision": "0", "subjects": references},
                **({"execution_target": target} if target is not None else {}),
            },
            "selected-message",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    return conversation, accepted, store, claim


def image_group_workspace(workspace, *, kind="asset"):
    from short_drama.core.config import Settings
    from short_drama.domain import MediaFile
    from short_drama.service.image_group_service import ImageGroupService
    from short_drama.service.shot_script_service import ShotScriptService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(workspace)[0]
    factory, settings, _model = workspace
    image_bucket = Settings(_env_file=None).minio_image_bucket
    with factory() as session:
        task_service(session, settings)
        if kind == "shot":
            shot = ShotScriptService(session).create(
                {"project_id": reference["project_id"], "position": 1, "script": "进入车站"}
            )
            reference.update(kind="shot", id=str(shot.id), revision=str(shot.row_version))
        with session.begin():
            native = ImageGroupService(session)
            target, scope = native.target(kind, reference["id"])
            groups = []
            for group_index in range(2):
                group = native.new_group(
                    kind,
                    target,
                    scope,
                    3,
                    "generation",
                    activate=group_index == 0,
                    source_context={
                        "hash": native.context(kind, target),
                        "media_id": str(native.current_media(kind, target) or ""),
                        "prompt": "不允许读取的私人原始参数",
                    },
                )
                items = native.groups.items(group.id)
                for item in items:
                    media_id = next_id()
                    session.add(
                        MediaFile(
                            id=media_id,
                            project_id=int(reference["project_id"]),
                            created_by=1,
                            format_code="image/png",
                            storage_locator=f"minio://{image_bucket}/{media_id}",
                            byte_size=100,
                            checksum_sha256="a" * 64,
                            original_name="image.png",
                            width=10,
                            height=10,
                            created_at=utcnow(),
                            updated_at=utcnow(),
                        )
                    )
                    session.flush()
                    item.media_id = media_id
                    item.item_version = 2
                    item.origin = {
                        "prompt": "不能注入其他图位的私人提示词",
                        "layout": "single",
                        "history": [str(media_id)],
                    }
                group.status, group.primary_index, group.primary_mode = "completed", 1, "manual"
                session.flush()
                if group_index == 0:
                    native.write_primary(group, items[0], manual=True)
                groups.append(
                    {
                        "id": str(group.id),
                        "primary_version": str(group.primary_version),
                        "items": [
                            {
                                "id": str(item.id),
                                "media_id": str(item.media_id),
                                "item_version": str(item.item_version),
                            }
                            for item in items
                        ],
                    }
                )
            reference["revision"] = str(target.row_version)
    return reference, groups


def image_primary_run(
    workspace, content, references, *, override=None, repeat=1, before_execute=None
):
    from short_drama.agent.image_tools import image_primary_requests_locked

    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "primary-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": references[0]["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": references},
            },
            "primary-message",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    with factory.begin() as session:
        request = image_primary_requests_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
        )[0]
    choice = request["choice"]
    arguments = {
        "request_message_id": request["request_message_id"],
        "request_hash": request["request_hash"],
        **{
            key: choice[key]
            for key in (
                "subject_key",
                "group_id",
                "item_id",
                "expected_primary_version",
                "expected_item_version",
                "expected_target_version",
            )
        },
        **(override or {}),
    }
    store.before_send(claim, {"fixture": "primary-command"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": f"primary-{index}",
                        "tool_name": "set_image_group_primary",
                        "args": arguments,
                    }
                    for index in range(repeat)
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    operation_id = claim_operation(factory, settings, accepted.run.id)
    assert operation_id is not None
    if before_execute:
        before_execute(conversation, accepted, choice)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id)
        ).all()
    return conversation, accepted, claim, operations


@pytest.mark.parametrize("selection", ["asset", "shot", "image_group", "image_slot"])
def test_image_content_read_delivers_only_requested_pixels_after_tool_results(
    task_workspace, selection
):
    import base64
    import hashlib
    import io

    from PIL import Image

    from short_drama.agent.input_media import materialize_prompt
    from short_drama.domain import MediaFile

    factory, settings, _model = task_workspace
    reference, groups = image_group_workspace(
        task_workspace, kind="shot" if selection == "shot" else "asset"
    )
    item = groups[0]["items"][1]
    if selection in {"image_group", "image_slot"}:
        reference.update(
            kind=selection,
            id=groups[0]["id"] if selection == "image_group" else item["id"],
            revision=groups[0]["primary_version"] if selection == "image_group" else "2",
        )
    data = io.BytesIO()
    Image.new("RGB", (18, 12), "#2377b4").save(data, format="PNG")
    raw = data.getvalue()
    with factory.begin() as session:
        media = session.get(MediaFile, int(item["media_id"]))
        media.byte_size = len(raw)
        media.checksum_sha256 = hashlib.sha256(raw).hexdigest()
        locator = media.storage_locator
    conversation, accepted, store, claim = selected_task(task_workspace, [reference])
    assert "read_image_content" in {tool["name"] for tool in claim.inputs["kwargs"]["tools"]}
    directory = json.loads(
        claim.inputs["kwargs"]["instructions"].split("任务状态与用户原始输入：", 1)[1]
    )
    key = directory["image_sources"][0]["subject_key"]
    store.before_send(claim, {"fixture": True})
    store.save_raw(claim, {"fixture": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "image-read",
                        "tool_name": "read_image_content",
                        "args": {
                            "subject_key": key,
                            "group_id": groups[0]["id"],
                            "item_id": item["id"],
                            "media_id": item["media_id"],
                        },
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        following = store.claim_execution(run.id, run.message_version, prepare_decision)
    assert following is not None
    values = following.inputs["kwargs"]
    result = values["deferred_results"]["calls"]["image-read"]
    assert result["media_id"] == item["media_id"] and result["source_changed"] is False
    assert "storage_locator" not in str(result) and "私人" not in str(result)
    assert [part["media_id"] for part in values["user_prompt"]["attachments"]] == [item["media_id"]]
    opened = []

    def open_image(value):
        opened.append(value)
        return io.BytesIO(raw)

    parts = materialize_prompt(values["user_prompt"], settings, SimpleNamespace(open=open_image))
    assert opened == [locator]
    pixels = [part for part in parts if isinstance(part, dict)]
    assert len(pixels) == 1
    with Image.open(io.BytesIO(base64.urlsafe_b64decode(pixels[0]["data"]))) as actual:
        assert actual.size == (18, 12)
    store.before_send(following, {"fixture": "inline-image"})


@pytest.mark.parametrize(
    "invalid", ["sibling", "other_group", "unselected", "later_media", "metadata"]
)
def test_image_content_read_rejects_guessed_or_unselected_pixels(task_workspace, invalid):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.domain import ImageGroupItem, MediaFile

    reference, groups = image_group_workspace(task_workspace)
    selected = {
        **reference,
        "kind": "image_slot",
        "id": groups[0]["items"][0]["id"],
        "revision": "2",
    }
    conversation, accepted, _store, claim = selected_task(task_workspace, [selected])
    directory = json.loads(
        claim.inputs["kwargs"]["instructions"].split("任务状态与用户原始输入：", 1)[1]
    )
    key = directory["image_sources"][0]["subject_key"]
    item = groups[0]["items"][0]
    args = {
        "subject_key": key,
        "group_id": groups[0]["id"],
        "item_id": item["id"],
        "media_id": item["media_id"],
    }
    if invalid == "sibling":
        item = groups[0]["items"][1]
        args.update(item_id=item["id"], media_id=item["media_id"])
    elif invalid == "other_group":
        item = groups[1]["items"][0]
        args.update(group_id=groups[1]["id"], item_id=item["id"], media_id=item["media_id"])
    elif invalid == "unselected":
        args["subject_key"] = "subject:" + "f" * 64
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        if invalid == "later_media":
            row = session.get(ImageGroupItem, int(item["id"]))
            row.media_id = int(groups[1]["items"][0]["media_id"])
            row.item_version += 1
            args["media_id"] = str(row.media_id)
        elif invalid == "metadata":
            session.get(MediaFile, int(item["media_id"])).checksum_sha256 = None
        session.flush()
    with factory.begin() as session, pytest.raises(WorkflowError):
        execute_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            SimpleNamespace(tool_name="read_image_content", arguments=args, run_id=accepted.run.id),
        )


@pytest.mark.parametrize(
    "mutation", ["removed", "deleted", "archived", "checksum", "locator", "refreshed"]
)
def test_image_content_read_invalidates_history_and_blocks_stale_send(task_workspace, mutation):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.domain import Asset, MediaFile, Project

    reference, groups = image_group_workspace(task_workspace)
    conversation, accepted, store, claim = selected_task(task_workspace, [reference])
    factory, settings, _model = task_workspace
    directory = json.loads(
        claim.inputs["kwargs"]["instructions"].split("任务状态与用户原始输入：", 1)[1]
    )
    key = directory["image_sources"][0]["subject_key"]
    item = groups[0]["items"][1]
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        execute_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            SimpleNamespace(
                tool_name="read_image_content",
                run_id=run.id,
                arguments={
                    "subject_key": key,
                    "group_id": groups[0]["id"],
                    "item_id": item["id"],
                    "media_id": item["media_id"],
                },
            ),
        )
        values = prepare_decision(session, session.get(AgentConversation, conversation.id), run)
        assert values["user_prompt"]["attachments"][0]["media_id"] == item["media_id"]
        checkpoint = dict(run.checkpoint)
        checkpoint["history"] = {"contains_private_pixels": True}
        checkpoint["pending_results"] = {"calls": {"old": {"content": "old pixels"}}}
        run.checkpoint = checkpoint
        claim.inputs["image_content_access_keys"] = checkpoint["available_image_content_keys"]
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        if mutation == "removed":
            task.context_snapshot = {"schema_version": 2, "subjects": []}
        elif mutation == "refreshed":
            from copy import deepcopy

            snapshot = deepcopy(task.context_snapshot)
            snapshot["subjects"][0]["snapshot_id"] = "new-snapshot"
            task.context_snapshot = snapshot
        elif mutation == "deleted":
            session.get(Asset, int(reference["id"])).deleted_at = utcnow()
        elif mutation == "archived":
            session.get(Project, int(reference["project_id"])).archived_at = utcnow()
        elif mutation == "checksum":
            session.get(MediaFile, int(item["media_id"])).checksum_sha256 = "b" * 64
        else:
            session.get(MediaFile, int(item["media_id"])).storage_locator += "-changed"
    with pytest.raises(AgentGatewayError, match="assistant_subjects_superseded"):
        store.before_send(claim, {"fixture": "stale-image"})
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        current = prepare_decision(session, session.get(AgentConversation, conversation.id), run)
        assert current["history"] is None and current["deferred_results"] is None
        assert str(item["media_id"]) not in str(current["user_prompt"])


def test_image_content_read_keeps_frozen_history_and_never_follows_replacement(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.domain import ImageGroupItem

    reference, groups = image_group_workspace(task_workspace)
    item, old = groups[0]["items"][1], groups[1]["items"][0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        slot = session.get(ImageGroupItem, int(item["id"]))
        slot.origin = {**slot.origin, "history": [item["media_id"], old["media_id"]]}
    reference.update(kind="image_slot", id=item["id"], revision="2")
    conversation, accepted, _store, claim = selected_task(task_workspace, [reference])
    directory = json.loads(
        claim.inputs["kwargs"]["instructions"].split("任务状态与用户原始输入：", 1)[1]
    )
    key = directory["image_sources"][0]["subject_key"]
    with factory.begin() as session:
        slot = session.get(ImageGroupItem, int(item["id"]))
        slot.media_id = int(groups[1]["items"][1]["media_id"])
        slot.item_version += 1
    with factory.begin() as session:
        owner, run, task = (
            session.get(AgentConversation, conversation.id),
            session.get(AgentRun, accepted.run.id),
            session.get(AgentTask, accepted.task.id),
        )
        result = execute_locked(
            session,
            owner,
            task,
            SimpleNamespace(
                tool_name="read_image_content",
                run_id=run.id,
                arguments={
                    "subject_key": key,
                    "group_id": groups[0]["id"],
                    "item_id": item["id"],
                    "media_id": old["media_id"],
                },
            ),
        )
        assert result["media_id"] == old["media_id"] and result["source_changed"] is True
        prompt = prepare_decision(session, owner, run)["user_prompt"]
        assert old["media_id"] in str(prompt)
        assert groups[1]["items"][1]["media_id"] not in str(prompt)


def test_image_reader_freezes_paginated_history_and_never_returns_private_parameters(
    task_workspace,
):
    from short_drama.agent.image_tools import ReadImageGroup, read_image_group_locked
    from short_drama.domain import ImageGroupItem
    from short_drama.service.assistant_subject_service import selection_metadata

    reference, groups = image_group_workspace(task_workspace)
    conversation, accepted, _store, claim = selected_task(task_workspace, [reference])
    assert "read_image_group" in {tool["name"] for tool in claim.inputs["kwargs"]["tools"]}
    assert "set_image_group_primary" not in {
        tool["name"] for tool in claim.inputs["kwargs"]["tools"]
    }
    assert "image_catalog" not in str(accepted.task.selected_subjects)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        conversation = session.get(AgentConversation, conversation.id)
        key = task.context_snapshot["subjects"][0]["subject_key"]
        assert "image_catalog" not in str(selection_metadata(session, 1, task.context_snapshot))
        values = ReadImageGroup(subject_key=key, view="history", limit=1, character_limit=87)
        pages, content, offset = [], "", 0
        while True:
            result = read_image_group_locked(session, conversation, task, values)
            assert not result["source_changed"]
            content += result["content"]
            if result["next"] is None or result["next"]["offset"] != offset:
                pages.extend(json.loads(content)["groups"])
                content = ""
            if result["next"] is None:
                break
            offset = result["next"]["offset"]
            values = ReadImageGroup(**result["next"], character_limit=87)
        assert {item["id"] for item in pages} == {group["id"] for group in groups}
        assert "私人" not in str(pages) and "minio" not in str(pages) and "url" not in str(pages)
        item = session.get(ImageGroupItem, int(groups[0]["items"][1]["id"]))
        item.item_version += 1
        session.flush()
        frozen = read_image_group_locked(
            session, conversation, task, ReadImageGroup(subject_key=key, character_limit=6000)
        )
        assert frozen["source_changed"]
        assert json.loads(frozen["content"])["groups"][0]["items"][1]["item_version"] == "2"


def test_image_slot_reader_cannot_expand_to_siblings_or_other_history_groups(task_workspace):
    from short_drama.agent.image_tools import ReadImageGroup, read_image_group_locked

    reference, groups = image_group_workspace(task_workspace)
    selected = {
        **reference,
        "kind": "image_slot",
        "id": groups[0]["items"][1]["id"],
        "revision": "2",
    }
    conversation, accepted, _store, _claim = selected_task(task_workspace, [selected])
    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        conversation = session.get(AgentConversation, conversation.id)
        key = task.context_snapshot["subjects"][0]["subject_key"]
        result = read_image_group_locked(
            session, conversation, task, ReadImageGroup(subject_key=key, view="history")
        )
        read = json.loads(result["content"])["groups"]
        assert len(read) == 1 and [item["id"] for item in read[0]["items"]] == [selected["id"]]
        assert groups[0]["items"][0]["id"] not in str(result)
        with pytest.raises(WorkflowError) as raised:
            read_image_group_locked(
                session,
                conversation,
                task,
                ReadImageGroup(subject_key=key, view="detail", group_id=groups[1]["id"]),
            )
        assert raised.value.code == "assistant_image_scope"


@pytest.mark.parametrize("kind", ["asset", "shot"])
def test_image_primary_adopts_native_target_once_and_keeps_history(task_workspace, kind):
    from short_drama.domain import Asset, AssetVersion, ImageGroup, ShotImage, ShotScript

    reference, groups = image_group_workspace(task_workspace, kind=kind)
    selected = {
        **reference,
        "kind": "image_group",
        "id": groups[0]["id"],
        "revision": groups[0]["primary_version"],
    }
    _conversation, accepted, claim, operations = image_primary_run(
        task_workspace, "把已选图片组的第二张设为主图", [selected], repeat=2
    )
    assert "set_image_group_primary" in {tool["name"] for tool in claim.inputs["kwargs"]["tools"]}
    assert [operation.status for operation in operations] == ["succeeded", "succeeded"]
    assert operations[1].result["already_applied"] and operations[1].changes == []
    assert operations[0].result["adopted"] and "私人" not in str(operations[0].result)
    assert all(item["before"] is not None for item in operations[0].changes)
    factory, _settings, _model = task_workspace
    with factory() as session:
        group = session.get(ImageGroup, int(groups[0]["id"]))
        assert (
            group.primary_index == 2
            and group.primary_version == int(groups[0]["primary_version"]) + 1
        )
        target = session.get(Asset if kind == "asset" else ShotScript, int(reference["id"]))
        assert target.row_version == int(reference["revision"]) + 1
        if kind == "asset":
            assert (
                target.state == "confirmed"
                and str(target.media_id) == groups[0]["items"][1]["media_id"]
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AssetVersion)
                    .where(AssetVersion.asset_id == target.id)
                )
                == 3
            )
        else:
            image = session.scalar(select(ShotImage).where(ShotImage.shot_id == target.id))
            assert str(image.media_id) == groups[0]["items"][1]["media_id"]
        assert operations[0].result_refs[0]["image_slot_id"] == groups[0]["items"][1]["id"]
        assert (
            session.get(AgentTask, accepted.task.id).context_snapshot["subjects"][0]["fields"][
                "primary_index"
            ]
            == 1
        )


def test_image_primary_can_explicitly_restore_a_selected_historical_group(task_workspace):
    from short_drama.domain import Asset, ImageGroup

    reference, groups = image_group_workspace(task_workspace)
    historical = {
        **reference,
        "kind": "image_group",
        "id": groups[1]["id"],
        "revision": groups[1]["primary_version"],
    }
    _conversation, _accepted, _claim, operations = image_primary_run(
        task_workspace, "把已选图片组的第三张设为主图", [historical]
    )
    assert operations[0].status == "succeeded"
    factory, _settings, _model = task_workspace
    with factory() as session:
        assert session.get(ImageGroup, int(groups[0]["id"])).active_slot is None
        assert session.get(ImageGroup, int(groups[1]["id"])).active_slot == 1
        assert (
            str(session.get(Asset, int(reference["id"])).media_id)
            == groups[1]["items"][2]["media_id"]
        )
        assert {
            change["id"] for change in operations[0].changes if change["kind"] == "image_group"
        } == {group["id"] for group in groups}


@pytest.mark.parametrize(
    "changed", ["target", "item", "primary", "forged_version", "wrong_slot", "wrong_hash"]
)
def test_image_primary_rejects_stale_or_forged_scope_without_effects(task_workspace, changed):
    from short_drama.domain import Asset, ImageGroup, ImageGroupItem

    reference, groups = image_group_workspace(task_workspace)
    selected = {
        **reference,
        "kind": "image_group",
        "id": groups[0]["id"],
        "revision": groups[0]["primary_version"],
    }
    factory, _settings, _model = task_workspace

    def mutate(_conversation, _accepted, _choice):
        with factory.begin() as session:
            if changed == "target":
                session.get(Asset, int(reference["id"])).row_version += 1
            elif changed == "item":
                session.get(ImageGroupItem, int(groups[0]["items"][1]["id"])).item_version += 1
            elif changed == "primary":
                session.get(ImageGroup, int(groups[0]["id"])).primary_version += 1

    override = (
        {"expected_target_version": "999"}
        if changed == "forged_version"
        else {"item_id": groups[0]["items"][2]["id"]}
        if changed == "wrong_slot"
        else {"request_hash": "f" * 64}
        if changed == "wrong_hash"
        else {}
    )
    _conversation, _accepted, _claim, operations = image_primary_run(
        task_workspace,
        "把已选图片组的第二张设为主图",
        [selected],
        override=override,
        before_execute=mutate,
    )
    assert operations[0].status == (
        "failed" if changed in {"wrong_slot", "wrong_hash"} else "conflict"
    )
    assert operations[0].changes == [] and operations[0].result_refs == []
    with factory() as session:
        assert session.get(ImageGroup, int(groups[0]["id"])).primary_index == 1
        assert (
            str(session.get(Asset, int(reference["id"])).media_id)
            == groups[0]["items"][0]["media_id"]
        )


def test_image_primary_finishes_claimed_tool_then_applies_stop_and_preserves_scope(task_workspace):
    reference, groups = image_group_workspace(task_workspace)
    selected = {
        **reference,
        "kind": "image_slot",
        "id": groups[0]["items"][1]["id"],
        "revision": "2",
    }
    factory, settings, _model = task_workspace

    def stop(conversation, accepted, _choice):
        with factory() as session:
            task_service(session, settings).control_task(
                accepted.task.id, {"kind": "stop"}, "primary-stop"
            )

    _conversation, accepted, _claim, operations = image_primary_run(
        task_workspace, "将已选图位设为主图", [selected], repeat=2, before_execute=stop
    )
    assert len(operations) == 1 and operations[0].status == "succeeded"
    with factory() as session:
        assert session.get(AgentTask, accepted.task.id).status == "stopped"


@pytest.mark.parametrize("context", ["worker", "scoped", "late_failure"])
def test_image_primary_restores_actor_scope_and_rolls_back_late_failure(
    task_workspace, monkeypatch, context
):
    from short_drama.domain import Asset, ImageGroup
    from short_drama.service.assistant_image_service import AssistantImageService
    from short_drama.service.image_group_service import ImageGroupService

    reference, groups = image_group_workspace(task_workspace)
    selected = {
        **reference,
        "kind": "image_slot",
        "id": groups[0]["items"][1]["id"],
        "revision": "2",
    }
    original = AssistantImageService.set_primary_locked
    checked = []

    def observed(self, *args, **kwargs):
        if context == "scoped":
            self.session.info["actor"] = task_actor()
            self.session.info["resource_scope"] = (1, None)
        before = {
            key: self.session.info[key]
            for key in ("actor", "resource_scope")
            if key in self.session.info
        }
        try:
            return original(self, *args, **kwargs)
        finally:
            assert {
                key: self.session.info[key]
                for key in ("actor", "resource_scope")
                if key in self.session.info
            } == before
            checked.append(True)

    monkeypatch.setattr(AssistantImageService, "set_primary_locked", observed)
    native_primary = ImageGroupService.primary_locked

    def fail_after_adoption(self, *args, **kwargs):
        native_primary(self, *args, **kwargs)
        raise WorkflowError("fixture_image_finalize", "测试替身模拟采用后的失败", 409)

    if context == "late_failure":
        monkeypatch.setattr(ImageGroupService, "primary_locked", fail_after_adoption)
    _conversation, _accepted, _claim, operations = image_primary_run(
        task_workspace, "将已选图位设为主图", [selected]
    )
    assert checked == [True]
    assert operations[0].status == ("conflict" if context == "late_failure" else "succeeded")
    if context == "late_failure":
        factory, _settings, _model = task_workspace
        with factory() as session:
            group = session.get(ImageGroup, int(groups[0]["id"]))
            assert (
                group.primary_index == 1
                and str(group.primary_version) == groups[0]["primary_version"]
            )
            assert (
                str(session.get(Asset, int(reference["id"])).media_id)
                == groups[0]["items"][0]["media_id"]
            )
        assert operations[0].result_refs == [] and operations[0].changes == []


@pytest.mark.parametrize("ceiling", ["primary", "target", "storyboard"])
def test_image_primary_rejects_trusted_version_ceiling_without_business_writes(
    task_workspace, ceiling
):
    from short_drama.domain import Asset, ImageGroup, Project
    from short_drama.schemas.base import UINT64_MAX

    reference, groups = image_group_workspace(
        task_workspace, kind="shot" if ceiling == "storyboard" else "asset"
    )
    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        if ceiling == "primary":
            session.get(ImageGroup, int(groups[0]["id"])).primary_version = UINT64_MAX
            groups[0]["primary_version"] = str(UINT64_MAX)
        elif ceiling == "target":
            session.get(Asset, int(reference["id"])).row_version = UINT64_MAX
        else:
            session.get(Project, int(reference["project_id"])).storyboard_version = UINT64_MAX
    selected = {
        **reference,
        "kind": "image_group",
        "id": groups[0]["id"],
        "revision": groups[0]["primary_version"],
    }
    _conversation, _accepted, _claim, operations = image_primary_run(
        task_workspace, "把已选图片组的第二张设为主图", [selected]
    )
    assert operations[0].status == "conflict" and "上限" in operations[0].error["message"]
    assert operations[0].changes == [] and operations[0].result_refs == []
    with factory() as session:
        assert session.get(ImageGroup, int(groups[0]["id"])).primary_index == 1


def test_image_primary_consumed_request_cannot_choose_another_image(task_workspace):
    from short_drama.agent.image_tools import (
        SetImageGroupPrimary,
        execute_image_primary_locked,
        image_primary_requests_locked,
    )

    reference, groups = image_group_workspace(task_workspace)
    selected = {
        **reference,
        "kind": "image_group",
        "id": groups[0]["id"],
        "revision": groups[0]["primary_version"],
    }
    conversation, accepted, _claim, operations = image_primary_run(
        task_workspace, "把已选图片组的第二张设为主图", [selected]
    )
    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        stored_conversation, task = (
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
        )
        requests = image_primary_requests_locked(session, stored_conversation, task)
        assert (
            requests[0]["completed_operation_id"] == str(operations[0].id)
            and "choice" not in requests[0]
        )
        values = SetImageGroupPrimary.model_validate(
            {**operations[0].arguments, "item_id": groups[0]["items"][2]["id"]}
        )
        with pytest.raises(Conflict, match="新的明确用户指令"):
            execute_image_primary_locked(
                session,
                stored_conversation,
                task,
                SimpleNamespace(input_revision=task.applied_input_revision),
                values,
            )


@pytest.mark.parametrize("failure", ["removed", "archived", "other_owner"])
def test_image_reader_refuses_removed_or_inaccessible_subjects(task_workspace, failure):
    from short_drama.agent.image_tools import ReadImageGroup, read_image_group_locked
    from short_drama.domain import Project

    reference, _groups = image_group_workspace(task_workspace)
    conversation, accepted, _store, _claim = selected_task(task_workspace, [reference])
    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        conversation = session.get(AgentConversation, conversation.id)
        key = task.context_snapshot["subjects"][0]["subject_key"]
        if failure == "removed":
            task.context_snapshot = None
        elif failure == "archived":
            session.get(Project, int(reference["project_id"])).archived_at = utcnow()
        else:
            session.add(
                User(
                    id=2,
                    username="image-second",
                    display_name="Second",
                    email="image-second@example.test",
                    password_hash="test-only",
                    status="active",
                    email_verified_at=utcnow(),
                    created_at=utcnow(),
                )
            )
            session.flush()
            session.get(Project, int(reference["project_id"])).owner_user_id = 2
        session.flush()
        with pytest.raises((NotFound, WorkflowError)):
            read_image_group_locked(session, conversation, task, ReadImageGroup(subject_key=key))


def test_image_primary_versions_feed_following_authorized_standard_edit(task_workspace):
    from short_drama.agent.authorization import digest
    from short_drama.agent.standard_tools import standard_requests_locked
    from short_drama.domain import Asset, Project

    reference, groups = image_group_workspace(task_workspace)
    conversation, accepted, _claim, operations = image_primary_run(
        task_workspace, "把已选素材的第二张设为主图", [reference]
    )
    assert operations[0].status == "succeeded"
    factory, settings, _model = task_workspace
    content = "修改已选素材的提示词：改成雨中逆光"
    with factory() as session:
        supplement = task_service(session, settings).send_task_message(
            conversation.id, {"content": content}, "after-primary-edit"
        )
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert claim is not None
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        request = standard_requests_locked(
            session, session.get(AgentConversation, conversation.id), task
        )[0]
        expected = str(int(reference["revision"]) + 1)
        assert request["allowed_assets"] == [{"id": reference["id"], "row_version": expected}]
        storyboard_version = str(
            session.get(Project, int(reference["project_id"])).storyboard_version
        )
    store.before_send(claim, {"fixture": "after-primary-edit"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "after-primary-edit",
                        "tool_name": "apply_standard_patch",
                        "args": {
                            "request_message_id": str(supplement.message.id),
                            "request_hash": digest({"role": "user", "content": content}),
                            "patch": {
                                "storyboard_version": storyboard_version,
                                "actions": [
                                    {
                                        "action": "update_asset",
                                        "asset": reference["id"],
                                        "patch": {"row_version": expected, "prompt": "雨中逆光"},
                                    }
                                ],
                            },
                        },
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        saved = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id)
        ).all()
        assert [operation.status for operation in saved] == ["succeeded", "succeeded"]
        asset = session.get(Asset, int(reference["id"]))
        assert asset.prompt == "雨中逆光" and asset.row_version == int(expected) + 1
        assert str(asset.media_id) == groups[0]["items"][1]["media_id"]


@pytest.mark.parametrize("selection", ["ambiguous", "cross_target", "slot_sibling"])
def test_image_primary_catalogue_does_not_grant_ambiguous_or_expanded_targets(
    task_workspace, selection
):
    from short_drama.agent.image_tools import image_primary_requests_locked

    reference, groups = image_group_workspace(task_workspace)
    selected = [
        {
            **reference,
            "kind": "image_group",
            "id": group["id"],
            "revision": group["primary_version"],
        }
        for group in groups
    ]
    target = {"mode": "standard", "project_id": reference["project_id"]}
    if selection == "cross_target":
        target["project_id"] = subject_assets(task_workspace)[1]["project_id"]
    elif selection == "slot_sibling":
        selected = [
            {**reference, "kind": "image_slot", "id": groups[0]["items"][0]["id"], "revision": "2"}
        ]
    conversation, accepted, _store, _claim = selected_task(
        task_workspace, selected, content="把第二张设为主图", target=target
    )
    factory, _settings, _model = task_workspace
    with factory.begin() as session:
        requests = image_primary_requests_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
        )
        assert len(requests) == 1 and "scope_error" in requests[0] and "choice" not in requests[0]


def test_image_primary_withdrawal_replaces_authority_at_the_input_boundary(task_workspace):
    from short_drama.agent.image_tools import image_primary_requests_locked
    from short_drama.agent.task_state import apply_inputs_locked

    reference, groups = image_group_workspace(task_workspace)
    selected = [
        {
            **reference,
            "kind": "image_group",
            "id": groups[0]["id"],
            "revision": groups[0]["primary_version"],
        }
    ]
    conversation, accepted, _store, _claim = selected_task(
        task_workspace,
        selected,
        content="把第二张设为主图",
        target={"mode": "standard", "project_id": reference["project_id"]},
    )
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id, {"content": "不要换主图，只讨论构图"}, "withdraw-primary"
        )
    with factory.begin() as session:
        owner, task, run = (
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            session.get(AgentRun, accepted.run.id),
        )
        assert image_primary_requests_locked(session, owner, task)[0]["choice"]["output_index"] == 2
        assert apply_inputs_locked(session, owner, task, run)
        assert image_primary_requests_locked(session, owner, task) == []


def test_multi_source_subjects_preserve_scope_and_read_only_frozen_content(task_workspace):
    from short_drama.agent.assistant_tools import ReadSelectedSubjects, _read_selected
    from short_drama.service.asset_service import AssetService

    references = subject_assets(task_workspace)
    conversation, accepted, _store, claim = selected_task(task_workspace, references)
    factory, settings, _model = task_workspace
    assert "第一部的设定" not in claim.inputs["kwargs"]["instructions"]
    with factory() as session:
        service = task_service(session, settings)
        meta = service.subject_metadata(accepted.task.id)
        assert len(meta["items"]) == 2 and not meta["pending"]
        assert all("fields" not in item and "media" not in item for item in meta["items"])
        assert service.task_runtime_state(conversation.id).tasks[0].execution_target is None
        AssetService(session).update(
            references[0]["id"], {"row_version": "1", "description": "用户的新设定"}
        )
        changed = service.subject_metadata(accepted.task.id)
        assert [item["availability"] for item in changed["items"]].count("changed") == 1
    with factory.begin() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        conv = session.get(AgentConversation, conversation.id)
        read = _read_selected(session, conv, task, ReadSelectedSubjects())
        assert any(
            "第一部的设定" in item["content"] and item["source_changed"]
            for item in read["subjects"]
        )
        assert "用户的新设定" not in str(read)


def test_subject_selection_retain_remove_and_stale_revision_are_durable(task_workspace):
    references = subject_assets(task_workspace)
    conversation, accepted, _store, _claim = selected_task(task_workspace, references)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        meta = service.subject_metadata(accepted.task.id)
        keep = meta["items"][0]["subject_key"]
        body = {
            "content": "只保留一个主体",
            "task_id": str(accepted.task.id),
            "subject_selection": {
                "expected_input_revision": meta["input_revision"],
                "retain_keys": [keep],
            },
        }
        changed = service.send_task_message(conversation.id, body, "remove-selection")
        assert (
            service.send_task_message(conversation.id, body, "remove-selection").message.id
            == changed.message.id
        )
        pending = service.subject_metadata(accepted.task.id)
        assert pending["pending"] and [item["subject_key"] for item in pending["items"]] == [keep]
        with pytest.raises(WorkflowError) as caught:
            service.send_task_message(conversation.id, body, "stale-selection")
        assert caught.value.code == "assistant_selection_changed"
        with pytest.raises(WorkflowError):
            service.send_task_message(
                conversation.id,
                {
                    **body,
                    "subject_selection": {
                        "expected_input_revision": pending["input_revision"],
                        "retain_keys": ["subject:" + "f" * 64],
                    },
                },
                "invented-selection",
            )


def test_subject_refresh_waits_for_current_tool_and_keeps_its_frozen_parameters(task_workspace):
    from short_drama.service.asset_service import AssetService

    references = subject_assets(task_workspace)[:1]
    conversation, accepted, store, claim = selected_task(task_workspace, references)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        key = service.subject_metadata(accepted.task.id)["items"][0]["subject_key"]
    store.before_send(claim, {"fixture": True})
    store.save_raw(claim, {"fixture": True})
    store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "read",
                        "tool_name": "read_selected_subjects",
                        "args": {"ids": [key]},
                    },
                ]
            },
            "history": {"fixture": True},
            "usage": {"output_tokens": 1, "output_tokens_reported": True},
        },
    )
    operation = claim_operation(factory, settings, accepted.run.id)
    with factory() as session:
        service = task_service(session, settings)
        AssetService(session).update(
            references[0]["id"], {"row_version": "1", "description": "刷新后的设定"}
        )
        service.send_task_message(
            conversation.id,
            {
                "content": "刷新引用",
                "task_id": str(accepted.task.id),
                "subject_selection": {
                    "expected_input_revision": "1",
                    "subjects": [{**references[0], "revision": "2"}],
                },
            },
            "refresh",
        )
    complete_operation(factory, settings, accepted.run.id, operation)
    with factory() as session:
        receipt = session.get(AgentOperation, operation)
        assert receipt.status == "succeeded" and "第一部的设定" in str(receipt.result)
        assert "刷新后的设定" not in str(receipt.result)
    assert claim_operation(factory, settings, accepted.run.id) is None
    with factory() as session:
        service = task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        assert task.context_snapshot["subjects"][0]["fields"]["description"] == "刷新后的设定"


def test_subject_long_frozen_text_has_lossless_continuation(task_workspace):
    from short_drama.agent.assistant_tools import ReadSelectedSubjects
    from short_drama.service.asset_service import AssetService
    from short_drama.service.assistant_subject_service import read_selected_snapshot

    references = subject_assets(task_workspace)[:1]
    text = "需要完整回查的中文设定" * 1500
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        AssetService(session).update(references[0]["id"], {"row_version": "1", "description": text})
    references[0]["revision"] = "2"
    _conversation, accepted, _store, _claim = selected_task(task_workspace, references)
    with factory.begin() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        values, chunks = ReadSelectedSubjects(character_limit=3000), []
        while True:
            result = read_selected_snapshot(session, 1, task.context_snapshot, values)
            assert sum(len(item["content"]) for item in result["subjects"]) <= 3000
            chunks.extend(item["content"] for item in result["subjects"])
            if result["next"] is None:
                break
            values = ReadSelectedSubjects(**result["next"], character_limit=3000)
        assert json.loads("".join(chunks))["description"] == text


def subject_canvas(workspace, name):
    from short_drama.domain.canvas import CanvasNode
    from short_drama.service.canvas_service import CanvasService
    from short_drama.service.project_service import ProjectService

    factory, settings, _model = workspace
    with factory() as session:
        task_service(session, settings)
        project = ProjectService(session).create_project(
            {"name": name, "workspace_mode": "infinite_canvas"},
            idempotency_key="subject-project-" + name.encode().hex(),
        )
        service = CanvasService(session)
        document = service.read(project.id, project.primary_canvas_id, private=True)[
            "source_document"
        ]
        document["nodes"] = [
            {
                "id": "same-key",
                "type": "text",
                "title": name,
                "position": {"x": 0, "y": 0},
                "width": 100,
                "height": 100,
                "metadata": {"content": name + "的正文", "prompt": "不应自动读入的私人提示词"},
            }
        ]
        service.commit(
            project.id,
            project.primary_canvas_id,
            {
                "expected_row_version": document["revision"],
                "source_document": document,
            },
            "canvas-subject-" + name.encode().hex(),
        )
        with session.begin():
            node = session.scalar(select(CanvasNode).where(CanvasNode.project_id == project.id))
            return {
                "project_id": str(project.id),
                "source_kind": "canvas",
                "source_id": document["id"],
                "kind": "node",
                "id": node.node_key,
                "revision": str(node.row_version),
                "content_revision": str(node.content_version),
            }


def test_subject_nodes_have_scoped_identity_and_layout_does_not_change_body(task_workspace):
    from short_drama.agent.assistant_tools import ReadSelectedSubjects
    from short_drama.domain.canvas import CanvasNode
    from short_drama.service.assistant_subject_service import read_selected_snapshot

    refs = [subject_canvas(task_workspace, name) for name in ("画布甲", "画布乙")]
    _conversation, accepted, _store, claim = selected_task(task_workspace, refs)
    instructions = claim.inputs["kwargs"]["instructions"]
    for private_text in ("画布甲的正文", "画布乙的正文", "不应自动读入的私人提示词"):
        assert private_text not in instructions
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        with session.begin():
            node = session.scalar(
                select(CanvasNode).where(CanvasNode.project_id == int(refs[0]["project_id"]))
            )
            node.x, node.row_version = 120, node.row_version + 1
        meta = service.subject_metadata(accepted.task.id)
        assert len({item["subject_key"] for item in meta["items"]}) == 2
        assert all(item["availability"] == "current" for item in meta["items"])
        with session.begin():
            task = session.get(AgentTask, accepted.task.id)
            with pytest.raises(WorkflowError):
                read_selected_snapshot(
                    session, 1, task.context_snapshot, ReadSelectedSubjects(ids=["same-key"])
                )
            read = read_selected_snapshot(session, 1, task.context_snapshot, ReadSelectedSubjects())
            assert "私人提示词" not in str(read)
            assert {"画布甲的正文", "画布乙的正文"}.issubset(
                {json.loads(item["content"])["metadata"]["content"] for item in read["subjects"]}
            )
            with pytest.raises(NotFound):
                read_selected_snapshot(session, 2, task.context_snapshot, ReadSelectedSubjects())


def test_subject_deletion_blocks_new_reads_and_stop_does_not_apply_removal(task_workspace):
    from short_drama.agent.assistant_tools import ReadSelectedSubjects
    from short_drama.domain import Asset
    from short_drama.service.assistant_subject_service import read_selected_snapshot

    references = subject_assets(task_workspace)[:1]
    conversation, accepted, _store, _claim = selected_task(task_workspace, references)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "stop-subject")
        with session.begin():
            session.get(Asset, int(references[0]["id"])).deleted_at = utcnow()
        assert (
            service.subject_metadata(accepted.task.id)["items"][0]["availability"] == "unavailable"
        )
        with session.begin():
            task = session.get(AgentTask, accepted.task.id)
            with pytest.raises(NotFound):
                read_selected_snapshot(session, 1, task.context_snapshot, ReadSelectedSubjects())
        saved = service.send_task_message(
            conversation.id,
            {
                "task_id": str(accepted.task.id),
                "content": "移除已删除引用",
                "subject_selection": {
                    "expected_input_revision": "1",
                    "retain_keys": [],
                    "subjects": [],
                },
            },
            "remove-deleted",
        )
        assert saved.task.status == "stopped" and saved.task.applied_input_revision == 1
        assert service.subject_metadata(accepted.task.id)["items"] == []
        resumed = service.control_task(
            accepted.task.id,
            {"kind": "resume", "expected_control_seq": str(stopped.task.control_seq)},
            "resume-subject",
        )
        assert resumed.task.status == "queued"
    claim = AgentRuntimeStore(factory, settings).claim_execution(
        resumed.run.id, 1, prepare_decision
    )
    assert claim is not None
    with factory() as session:
        service = task_service(session, settings)
        current = service.task_runtime_state(conversation.id).tasks[0]
        assert current.applied_input_revision == 2 and current.selected_subjects == []


def test_subject_revocation_before_provider_send_rebuilds_without_a_paid_call(task_workspace):
    from short_drama.domain import Asset

    references = subject_assets(task_workspace)[:1]
    _conversation, accepted, store, claim = selected_task(task_workspace, references)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        session.get(Asset, int(references[0]["id"])).deleted_at = utcnow()
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(claim, {"fixture": True})
    assert raised.value.code == "assistant_subjects_superseded"
    store.fail(claim, raised.value)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.usage["decision_calls"] == 0 and run.status == "running"
        version = run.message_version
    sanitized = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert sanitized is not None and sanitized.inputs["subject_access_keys"] == []
    assert "unavailable" in sanitized.inputs["kwargs"]["instructions"]


def test_subject_directory_and_image_slots_use_real_identity_without_other_prompt_bodies(
    task_workspace,
):
    from short_drama.agent.assistant_tools import ReadSelectedSubjects
    from short_drama.domain import ImageGroup, ImageGroupItem, MediaFile
    from short_drama.service.assistant_subject_service import read_selected_snapshot
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    group_id, slot_id, media_id = next_id(), next_id(), next_id()
    with factory() as session:
        service = task_service(session, settings)
        with session.begin():
            session.add(
                MediaFile(
                    id=media_id,
                    project_id=int(reference["project_id"]),
                    created_by=1,
                    format_code="image/png",
                    storage_locator=f"minio://fixture/{media_id}",
                    byte_size=100,
                    checksum_sha256="a" * 64,
                    original_name="slot.png",
                    width=10,
                    height=10,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            session.flush()
            session.add(
                ImageGroup(
                    id=group_id,
                    project_id=int(reference["project_id"]),
                    scope_user_id=None,
                    asset_id=int(reference["id"]),
                    kind="asset",
                    active_slot=1,
                    status="completed",
                    expected_count=1,
                    primary_index=1,
                    primary_mode="auto",
                    primary_version=1,
                    source_context={"prompt": "其他图位的私人提示词"},
                    created_by=1,
                    created_at=utcnow(),
                )
            )
            session.flush()
            session.add(
                ImageGroupItem(
                    id=slot_id,
                    group_id=group_id,
                    output_index=1,
                    media_id=media_id,
                    item_version=1,
                    origin={"prompt": "图位的私人供应商参数"},
                    created_at=utcnow(),
                )
            )
        directory = service.subject_directory(int(reference["project_id"]), "image_slot", "", 0, 20)
        assert directory["total"] == 1 and str(directory["items"][0]["id"]) == str(slot_id)
        assert "私人" not in str(directory)
    slot_ref = {**reference, "kind": "image_slot", "id": str(slot_id)}
    _conversation, accepted, _store, claim = selected_task(task_workspace, [slot_ref])
    attachment = claim.inputs["kwargs"]["user_prompt"]["attachments"][0]
    assert attachment["media_id"] == str(media_id)
    with factory.begin() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        result = read_selected_snapshot(session, 1, task.context_snapshot, ReadSelectedSubjects())
        assert "私人" not in str(result) and str(group_id) in str(result)
    with factory() as session:
        location = task_service(session, settings).subject_metadata(accepted.task.id)["items"][0][
            "source_location"
        ]
        assert location == {
            "project_id": reference["project_id"],
            "kind": "asset",
            "id": reference["id"],
            "image_group_id": str(group_id),
            "image_slot_id": str(slot_id),
        }


def add_text_attachment(workspace, conversation_id, text="仅本任务的附件正文"):
    import hashlib

    from short_drama.domain.agent_context import AgentAttachment
    from short_drama.utils.snowflake import next_id

    factory, settings, _model = workspace
    identifier = next_id()
    with factory.begin() as session:
        task_service(session, settings)
        session.add(
            AgentAttachment(
                id=identifier,
                owner_user_id=1,
                conversation_id=conversation_id,
                kind="text",
                name="本任务资料.txt",
                mime_type="text/plain",
                text_content=text,
                checksum_sha256=hashlib.sha256(text.encode()).hexdigest(),
                byte_size=len(text.encode()),
                input_metadata={},
                created_at=utcnow(),
            )
        )
    return identifier


def test_attachment_selection_waits_for_full_tool_and_replays_one_removal(task_workspace):
    from short_drama.agent.assistant_tools import prepare_decision as prepare_task
    from short_drama.agent.attachment_context import task_attachments
    from short_drama.domain.agent import AgentMessage

    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "attachment-conversation")
    attachment_id = add_text_attachment(task_workspace, conversation.id)
    with factory() as session:
        service = task_service(session, settings)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "读取这份资料",
                "model_config_id": model,
                "attachment_ids": [str(attachment_id)],
            },
            "attachment-initial",
        )
        assert service.attachment_metadata(accepted.task.id)["pending"] is True
    store, claim, normalized = decision(task_workspace, accepted, tool_count=1)
    assert claim.inputs["kwargs"]["user_prompt"]["attachments"][0]["id"] == str(attachment_id)
    assert store.save_result(claim, normalized)
    operation = claim_operation(factory, settings, accepted.run.id)
    assert operation is not None
    payload = {
        "task_id": str(accepted.task.id),
        "content": "后续移除附件，但保留其他引用",
        "attachment_selection": {"expected_input_revision": "1", "retain_ids": []},
    }
    with factory() as session:
        service = task_service(session, settings)
        removal = service.send_task_message(conversation.id, payload, "attachment-removal")
        repeated = service.send_task_message(conversation.id, payload, "attachment-removal")
        assert removal.message.id == repeated.message.id
        assert removal.task.applied_input_revision == 1 and removal.task.input_revision == 2
        metadata = service.attachment_metadata(accepted.task.id)
        assert metadata["items"] == [] and metadata["pending"] is True
        with session.begin():
            current = session.get(AgentTask, accepted.task.id)
            current_input = prepare_task(
                session,
                session.get(AgentConversation, conversation.id),
                session.get(AgentRun, accepted.run.id),
            )
            assert current.applied_input_revision == 1
            assert str(attachment_id) in task_attachments(session, current)
            assert current_input["history"] == normalized["history"]
    complete_operation(factory, settings, accepted.run.id, operation)
    assert claim_operation(factory, settings, accepted.run.id) is None
    with factory.begin() as session:
        task_service(session, settings)
        current = session.get(AgentTask, accepted.task.id)
        assert current.applied_input_revision == 2
        run = session.get(AgentRun, accepted.run.id)
        clean = prepare_task(session, session.get(AgentConversation, conversation.id), run)
        assert clean["user_prompt"] == "读取这份资料" and "附件正文" not in str(clean)
        message = session.scalar(select(AgentMessage).where(AgentMessage.id == accepted.message.id))
        assert message.references[0]["type"] == "attachment"
    with factory() as session:
        service = task_service(session, settings)
        assert service.attachment_metadata(accepted.task.id)["pending"] is False
        assert service.attachment_metadata(accepted.task.id)["items"] == []
        with pytest.raises(WorkflowError) as raised:
            service.send_task_message(
                conversation.id,
                {
                    **payload,
                    "attachment_selection": {"expected_input_revision": "1", "retain_ids": []},
                },
                "stale-attachment-removal",
            )
        assert raised.value.status_code == 409


def test_attachment_scope_blocks_old_reply_reinjection_and_cross_task_retain(task_workspace):
    from short_drama.agent.assistant_tools import ReadHistory, _read_history
    from short_drama.agent.memory_context import history_projection_locked
    from short_drama.domain.agent import AgentMessage
    from short_drama.utils.snowflake import next_id

    factory, settings, model = task_workspace
    conversation, accepted = new_task(task_workspace)
    attachment_id = add_text_attachment(task_workspace, conversation.id)
    with factory() as session:
        service = task_service(session, settings)
        service.send_task_message(
            conversation.id,
            {
                "content": "选入资料",
                "task_id": str(accepted.task.id),
                "attachment_ids": [str(attachment_id)],
            },
            "attach-source",
        )
    _store, _claim, _normalized = decision(task_workspace, accepted)
    with factory.begin() as session:
        task_service(session, settings)
        parent = session.get(AgentConversation, conversation.id)
        session.add(
            AgentMessage(
                id=next_id(),
                conversation_id=parent.id,
                task_id=accepted.task.id,
                seq=parent.next_message_seq,
                role="assistant",
                content="旧回复的附件正文",
                references=[{"type": "subject_scope", "keys": []}],
                artifacts=[],
                created_at=utcnow(),
            )
        )
        parent.next_message_seq += 1
        session.flush()
        history = _read_history(
            session, parent, session.get(AgentTask, accepted.task.id), ReadHistory(), 6000
        )
        assert "旧回复的附件正文" in str(history), "仍选入的有效附件允许回查其历史回复"
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "attachment-history-stop")
        other = service.send_task_message(
            conversation.id,
            {"new_task": True, "model_config_id": model, "content": "新任务不继承附件"},
            "attachment-independent",
        )
        assert service.attachment_metadata(other.task.id)["items"] == []
        with pytest.raises(WorkflowError) as raised:
            service.send_task_message(
                conversation.id,
                {
                    "task_id": str(other.task.id),
                    "content": "伪造保留范围",
                    "attachment_selection": {
                        "expected_input_revision": "1",
                        "retain_ids": [str(attachment_id)],
                    },
                },
                "forged-attachment-retain",
            )
        assert raised.value.code == "assistant_attachment_scope"
        with session.begin():
            current = session.get(AgentTask, other.task.id)
            history = history_projection_locked(
                session, session.get(AgentConversation, conversation.id), current, 1, 20
            )
            assert "旧回复的附件正文" not in str(history)
            assert any(item["source_restricted"] for item in history["items"])
        with pytest.raises(NotFound):
            task_service(session, settings, 2).attachment_metadata(accepted.task.id)


@pytest.mark.parametrize("budget", ["count", "text"])
def test_attachment_budget_counts_all_pending_inputs_and_removal_releases_capacity(
    task_workspace, budget
):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    texts = ["a"] * 17 if budget == "count" else ["a" * 65536, "b" * 65536, "c"]
    identifiers = [
        str(add_text_attachment(task_workspace, conversation.id, text)) for text in texts
    ]
    with factory() as session:
        service = task_service(session, settings)
        first = service.send_task_message(
            conversation.id,
            {
                "content": "选入资料",
                "task_id": str(accepted.task.id),
                "attachment_ids": identifiers[:-1],
            },
            "attachment-budget-exact",
        )
        assert first.task.applied_input_revision == 0 and first.task.input_revision == 2
        with pytest.raises(WorkflowError) as raised:
            service.send_task_message(
                conversation.id,
                {
                    "content": "追加资料",
                    "task_id": str(accepted.task.id),
                    "attachment_ids": identifiers[-1:],
                },
                "attachment-budget-overflow",
            )
        assert raised.value.code == "agent_context_too_large"
        metadata = service.attachment_metadata(accepted.task.id)
        assert metadata["input_revision"] == "2" and len(metadata["items"]) == len(identifiers) - 1
        replacement = service.send_task_message(
            conversation.id,
            {
                "content": "移除旧资料，选入新资料",
                "task_id": str(accepted.task.id),
                "attachment_ids": identifiers[-1:],
                "attachment_selection": {"expected_input_revision": "2", "retain_ids": []},
            },
            "attachment-budget-release",
        )
        assert replacement.task.input_revision == 3
        assert [
            item["id"] for item in service.attachment_metadata(accepted.task.id)["items"]
        ] == identifiers[-1:]


def test_attachment_revocation_before_send_rebuilds_without_a_provider_call(task_workspace):
    from short_drama.domain.agent_context import AgentAttachment

    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    attachment_id = add_text_attachment(task_workspace, conversation.id)
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id,
            {"content": "选入资料", "attachment_ids": [str(attachment_id)]},
            "attachment-before-send",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None and claim.inputs["attachment_access_ids"] == [str(attachment_id)]
    with factory.begin() as session:
        task_service(session, settings)
        session.get(AgentAttachment, attachment_id).deleted_at = utcnow()
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(claim, {"fixture": True})
    assert raised.value.code == "assistant_attachments_superseded"
    store.fail(claim, raised.value)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.usage["decision_calls"] == 0 and run.status == "running"
        version = run.message_version
    sanitized = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert sanitized is not None and sanitized.inputs["attachment_access_ids"] == []
    assert "附件正文" not in str(sanitized.inputs["kwargs"])
    store.before_send(sanitized, {"fixture": True})


def test_attachment_removal_after_stop_waits_for_explicit_resume(task_workspace):
    from short_drama.agent.attachment_context import task_attachments

    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    attachment_id = add_text_attachment(task_workspace, conversation.id)
    with factory() as session:
        service = task_service(session, settings)
        service.send_task_message(
            conversation.id,
            {"content": "选入资料", "attachment_ids": [str(attachment_id)]},
            "attachment-before-stop",
        )
    decision(task_workspace, accepted)
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "attachment-stop")
        saved = service.send_task_message(
            conversation.id,
            {
                "task_id": str(accepted.task.id),
                "content": "移除附件，暂时不继续",
                "attachment_selection": {"expected_input_revision": "2", "retain_ids": []},
            },
            "attachment-stop-removal",
        )
        assert saved.task.status == "stopped" and saved.task.applied_input_revision == 2
        assert service.attachment_metadata(accepted.task.id)["pending"] is True
        with session.begin():
            assert str(attachment_id) in task_attachments(
                session, session.get(AgentTask, accepted.task.id)
            )
        resumed = service.control_task(
            accepted.task.id,
            {"kind": "resume", "expected_control_seq": str(stopped.task.control_seq)},
            "attachment-resume",
        )
    claim = AgentRuntimeStore(factory, settings).claim_execution(
        resumed.run.id, 1, prepare_decision
    )
    assert claim is not None and claim.inputs["attachment_access_ids"] == []
    assert "附件正文" not in str(claim.inputs["kwargs"])


def test_standard_patch_creates_real_assets_shots_and_local_associations_in_one_transaction(
    task_workspace,
):
    from short_drama.domain import Asset, AssetVersion, Project, ShotAsset, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        project = session.get(Project, int(reference["project_id"]))
        result = AssistantStandardService(session).apply_locked(
            project.id,
            {
                "storyboard_version": str(project.storyboard_version),
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {"kind": "character", "name": "林晚", "prompt": "车站里的年轻人"},
                    },
                    {
                        "action": "create_shot",
                        "key": "opening",
                        "values": {"script": "林晚走进车站", "video_prompt": "镜头跟随林晚"},
                        "assets": [reference["id"], "$hero"],
                    },
                    {
                        "action": "update_shot",
                        "shot": "$opening",
                        "patch": {"row_version": "1", "shot_size": "wide_shot"},
                    },
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_asset_ids=[reference["id"]],
        )
        asset_id, shot_id = (int(result["temporary_ids"][key]) for key in ("hero", "opening"))
        assert asset_id > 2**53 and shot_id > 2**53
        asset, shot = session.get(Asset, asset_id), session.get(ShotScript, shot_id)
        assert asset.state == "unconfirmed" and asset.media_id is None
        assert asset.current_version_id is not None
        assert session.get(AssetVersion, asset.current_version_id).asset_id == asset.id
        assert shot.position == 1 and shot.row_version == 2 and shot.shot_size == "wide_shot"
        assert shot.video_prompt == "镜头跟随林晚" and shot.duration_ms == 3000
        assert set(
            session.scalars(select(ShotAsset.asset_id).where(ShotAsset.shot_id == shot.id))
        ) == {asset_id, int(reference["id"])}
        assert len(result["changes"]) == 2 and all(
            item["before"] is None for item in result["changes"]
        )
        assert result["changes"][1]["after"]["row_version"] == "2"
        assert [item["status"] for item in result["result_refs"]] == ["created", "created"]
        assert project.storyboard_version == 3


def test_standard_patch_late_failure_rolls_back_every_new_object_and_version(task_workspace):
    from short_drama.domain import Asset, AssetVersion, Project, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    refs = subject_assets(task_workspace)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        project = session.get(Project, int(refs[0]["project_id"]))
        version = project.storyboard_version
        original_assets = session.scalar(select(func.count()).select_from(Asset))
        original_versions = session.scalar(select(func.count()).select_from(AssetVersion))
        with pytest.raises(WorkflowError) as raised, session.begin_nested():
            AssistantStandardService(session).apply_locked(
                project.id,
                {
                    "storyboard_version": str(version),
                    "actions": [
                        {
                            "action": "create_asset",
                            "key": "hero",
                            "values": {"kind": "character", "name": "不能部分保存"},
                        },
                        {"action": "create_shot", "key": "shot", "values": {}, "assets": ["$hero"]},
                        {
                            "action": "update_asset",
                            "asset": refs[1]["id"],
                            "patch": {"row_version": "1", "name": "越界修改"},
                        },
                    ],
                },
                owner_user_id=1,
                operation_id=next_id(),
                allowed_asset_ids=[refs[0]["id"]],
            )
        assert raised.value.code == "assistant_write_scope"
        assert session.scalar(select(func.count()).select_from(Asset)) == original_assets
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == original_versions
        assert session.scalar(select(func.count()).select_from(ShotScript)) == 0
        assert project.storyboard_version == version


def test_standard_patch_versions_owner_and_removed_associations_cannot_expand_scope(task_workspace):
    from short_drama.domain import Project, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    refs = subject_assets(task_workspace)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        project = session.get(Project, int(refs[0]["project_id"]))
        service = AssistantStandardService(session)
        first = service.apply_locked(
            project.id,
            {
                "storyboard_version": str(project.storyboard_version),
                "actions": [
                    {
                        "action": "create_shot",
                        "key": "opening",
                        "values": {},
                        "assets": [refs[0]["id"]],
                    }
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_asset_ids=[refs[0]["id"]],
        )
        shot_id = first["temporary_ids"]["opening"]
        shot = session.get(ShotScript, int(shot_id))
        remove = {
            "storyboard_version": str(project.storyboard_version),
            "actions": [
                {"action": "set_shot_assets", "shot": shot_id, "row_version": "1", "assets": []},
            ],
        }
        with pytest.raises(WorkflowError) as raised, session.begin_nested():
            service.apply_locked(
                project.id,
                remove,
                owner_user_id=1,
                operation_id=next_id(),
                allowed_shot_ids=[shot_id],
            )
        assert raised.value.code == "assistant_related_scope"
        cleared = service.apply_locked(
            project.id,
            remove,
            owner_user_id=1,
            operation_id=next_id(),
            allowed_shot_ids=[shot_id],
            allowed_asset_ids=[refs[0]["id"]],
        )
        assert cleared["changes"][0]["before"]["asset_ids"] == [refs[0]["id"]]
        assert cleared["changes"][0]["after"]["asset_ids"] == [] and shot.row_version == 2
        with pytest.raises(WorkflowError) as raised, session.begin_nested():
            service.apply_locked(
                project.id,
                {**remove, "storyboard_version": str(project.storyboard_version)},
                owner_user_id=1,
                operation_id=next_id(),
                allowed_shot_ids=[shot_id],
            )
        assert raised.value.code == "shot_version_conflict"
        with pytest.raises(NotFound), session.begin_nested():
            service.apply_locked(
                project.id,
                {
                    "actions": [
                        {
                            "action": "update_asset",
                            "asset": refs[1]["id"],
                            "patch": {"row_version": "1", "name": "跨作品"},
                        }
                    ]
                },
                owner_user_id=1,
                operation_id=next_id(),
                allowed_asset_ids=[refs[1]["id"]],
            )
        with pytest.raises(NotFound), session.begin_nested():
            service.apply_locked(
                project.id,
                {
                    "actions": [
                        {
                            "action": "create_asset",
                            "key": "invalid",
                            "values": {"kind": "prop", "name": "无权创建"},
                        }
                    ]
                },
                owner_user_id=2,
                operation_id=next_id(),
            )


def test_standard_patch_reorders_only_authorized_positions_and_ignores_noop_changes(task_workspace):
    from short_drama.domain import Project, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        project = session.get(Project, int(reference["project_id"]))
        service = AssistantStandardService(session)
        created = service.apply_locked(
            project.id,
            {
                "storyboard_version": "1",
                "actions": [
                    {"action": "create_shot", "key": key, "values": {"title": key}}
                    for key in ("first", "middle", "last")
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
        )
        first, middle, last = (created["temporary_ids"][key] for key in ("first", "middle", "last"))
        payload = {
            "storyboard_version": str(project.storyboard_version),
            "actions": [
                {
                    "action": "reorder_shots",
                    "shots": [last, first],
                    "versions": {last: "1", first: "1"},
                },
            ],
        }
        result = service.apply_locked(
            project.id,
            payload,
            owner_user_id=1,
            operation_id=next_id(),
            allowed_shot_ids=[first, last],
        )
        assert [
            (
                session.get(ShotScript, int(key)).position,
                session.get(ShotScript, int(key)).row_version,
            )
            for key in (first, middle, last)
        ] == [(3, 2), (2, 1), (1, 2)]
        assert {item["id"] for item in result["changes"]} == {first, last}
        assert project.storyboard_version == 5
        payload["storyboard_version"] = "5"
        payload["actions"][0]["versions"] = {last: "2", first: "2"}
        unchanged = service.apply_locked(
            project.id,
            payload,
            owner_user_id=1,
            operation_id=next_id(),
            allowed_shot_ids=[first, last],
        )
        assert unchanged["changes"] == [] and project.storyboard_version == 5
        with pytest.raises(WorkflowError) as raised, session.begin_nested():
            service.apply_locked(
                project.id,
                payload,
                owner_user_id=1,
                operation_id=next_id(),
                allowed_shot_ids=[first],
            )
        assert raised.value.code == "assistant_write_scope"


def test_standard_patch_delete_preserves_history_and_requires_related_objects_to_be_resolved(
    task_workspace,
):
    from short_drama.domain import Asset, AssetVersion, Project, ShotAsset, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        project = session.get(Project, int(reference["project_id"]))
        service = AssistantStandardService(session)
        created = service.apply_locked(
            project.id,
            {
                "storyboard_version": "1",
                "actions": [
                    {
                        "action": "create_shot",
                        "key": key,
                        "values": {"title": key},
                        "assets": [reference["id"]],
                    }
                    for key in ("first", "middle", "last")
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_asset_ids=[reference["id"]],
        )
        first, middle, last = (created["temporary_ids"][key] for key in ("first", "middle", "last"))
        deletion = {
            "actions": [{"action": "delete_asset", "asset": reference["id"], "row_version": "1"}]
        }
        with pytest.raises(WorkflowError) as raised, session.begin_nested():
            service.apply_locked(
                project.id,
                deletion,
                owner_user_id=1,
                operation_id=next_id(),
                allowed_asset_ids=[reference["id"]],
            )
        assert raised.value.code == "assistant_delete_impact"
        assert set(raised.value.details["shot_ids"]) == {first, middle, last}
        compacted = service.apply_locked(
            project.id,
            {
                "storyboard_version": str(project.storyboard_version),
                "actions": [
                    {"action": "delete_shot", "shot": middle, "row_version": "1"},
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_shot_ids=[middle],
        )
        assert session.get(ShotScript, int(middle)).deleted_at is not None
        assert session.get(ShotScript, int(last)).position == 2
        assert {item["id"]: item["status"] for item in compacted["result_refs"]} == {
            middle: "deleted",
            last: "updated",
        }
        asset = session.get(Asset, int(reference["id"]))
        immutable_version_id = asset.current_version_id
        result = service.apply_locked(
            project.id,
            {
                "storyboard_version": str(project.storyboard_version),
                "actions": [
                    {"action": "delete_shot", "shot": last, "row_version": "2"},
                    {"action": "delete_shot", "shot": first, "row_version": "1"},
                    deletion["actions"][0],
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_shot_ids=[last, first],
            allowed_asset_ids=[reference["id"]],
        )
        assert asset.deleted_at is not None and asset.row_version == 2
        assert session.get(AssetVersion, immutable_version_id) is None
        assert session.scalar(select(func.count()).select_from(ShotAsset)) == 0
        assert all(item["status"] == "deleted" for item in result["result_refs"])
    # Internal maintenance reads can verify physical history after the business commit.
    with factory() as internal_session:
        assert internal_session.get(AssetVersion, immutable_version_id) is not None


def test_standard_patch_asset_updates_preserve_character_history_and_business_validation(
    task_workspace,
):
    from short_drama.domain import Asset, AssetVersion, MediaFile
    from short_drama.service.asset_image_service import AssetImageService
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        asset = session.get(Asset, int(reference["id"]))
        media = MediaFile(
            id=next_id(),
            project_id=asset.project_id,
            format_code="image/png",
            storage_locator=f"minio://test-standard/{next_id()}.png",
            published_at=utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(media)
        session.flush()
        AssetImageService(
            session,
            SimpleNamespace(
                minio_image_bucket="test-images",
                minio_video_bucket="test-videos",
                minio_audio_bucket="test-audio",
            ),
            None,
        ).confirm_locked(
            asset.id,
            {"row_version": "1", "media_id": str(media.id), "expected_media_id": None},
            add_generated_candidate=True,
        )
        old_version = asset.current_version_id
        service = AssistantStandardService(session)

        def apply(patch):
            return service.apply_locked(
                asset.project_id,
                {"actions": [{"action": "update_asset", "asset": str(asset.id), "patch": patch}]},
                owner_user_id=1,
                operation_id=next_id(),
                allowed_asset_ids=[asset.id],
            )

        result = apply({"row_version": "2", "prompt": "雨中逆光", "tags": ["  主角  ", "主角"]})
        assert asset.row_version == 3 and asset.state == "unconfirmed"
        assert asset.media_id == media.id
        assert asset.tags == ["主角"] and asset.prompt == "雨中逆光"
        assert asset.current_version_id != old_version
        assert (
            session.get(AssetVersion, old_version).snapshot_json["asset"]["prompt"] != asset.prompt
        )
        assert (
            session.get(AssetVersion, asset.current_version_id).snapshot_json["asset"]["prompt"]
            == asset.prompt
        )
        assert result["changes"][0]["before"]["state"] == "confirmed"
        assert result["changes"][0]["after"]["current_version_id"] == str(asset.current_version_id)
        version = asset.current_version_id
        assert apply({"row_version": "3", "prompt": "雨中逆光"})["changes"] == []
        assert asset.row_version == 3 and asset.current_version_id == version
        for patch, code in (
            ({"row_version": "3", "kind": "scene"}, "asset_kind_incompatible"),
            ({"row_version": "3", "scene_time": "黄昏"}, "invalid_asset"),
            ({"row_version": "1", "name": "旧版本"}, "asset_version_conflict"),
        ):
            with pytest.raises(WorkflowError) as raised, session.begin_nested():
                apply(patch)
            assert raised.value.code == code
        assert asset.row_version == 3 and asset.current_version_id == version


def standard_tool_run(
    workspace,
    content,
    patches,
    *,
    references=(),
    before_execute=None,
    authority=None,
    project_id=None,
):
    from short_drama.agent.authorization import digest

    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "standard-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "model_config_id": model,
                "execution_target": {
                    "mode": "standard",
                    "project_id": project_id or references[0]["project_id"],
                },
                "subject_selection": {"expected_input_revision": "0", "subjects": list(references)},
            },
            "standard-message",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    arguments = {
        "request_message_id": str(accepted.message.id),
        "request_hash": digest({"role": "user", "content": content}),
        **(authority or {}),
    }
    store.before_send(claim, {"fixture": "standard-command"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": f"standard-{index}",
                        "tool_name": "apply_standard_patch",
                        "args": {**arguments, "patch": patch},
                    }
                    for index, patch in enumerate(patches)
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    operation_id = claim_operation(factory, settings, accepted.run.id)
    assert operation_id is not None
    if before_execute:
        before_execute(conversation, accepted)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id)
        ).all()
    return conversation, accepted, claim, operations


def canvas_patch_workspace(workspace):
    from short_drama.service.canvas_service import CanvasService
    from short_drama.service.project_service import ProjectService

    factory, settings, _model = workspace
    with factory() as session:
        task_service(session, settings)
        project = ProjectService(session).create_project(
            {"name": "助手画布", "workspace_mode": "infinite_canvas"},
            idempotency_key="assistant-canvas",
        )
        native = CanvasService(session)
        document = native.read(project.id, project.primary_canvas_id, private=True)[
            "source_document"
        ]
        document["nodes"] = [
            {
                "id": key,
                "type": "text",
                "title": key,
                "position": {"x": index * 600, "y": -150},
                "width": 350,
                "height": 350,
                "metadata": {
                    "content": f"{key}原正文",
                    "prompt": f"{key}私人提示词",
                    "status": "idle",
                },
            }
            for index, key in enumerate(("original-a", "original-b"))
        ]
        document["viewport"] = {"x": -20, "y": 33, "k": 0.5}
        document["chatSessions"] = [
            {"id": "private-session", "messages": [{"content": "原生私有对话"}]}
        ]
        native.commit(
            project.id,
            project.primary_canvas_id,
            {"expected_row_version": "1", "source_document": document},
            "assistant-canvas-seed",
        )
        saved = native.read(project.id, project.primary_canvas_id, private=True)
    return project.id, project.primary_canvas_id, saved


def canvas_tool_run(
    workspace,
    project_id,
    canvas_id,
    content,
    patches,
    *,
    references=(),
    before_execute=None,
    authority=None,
):
    from short_drama.agent.authorization import digest

    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "canvas-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "model_config_id": model,
                "execution_target": {
                    "mode": "infinite_canvas",
                    "project_id": str(project_id),
                    "canvas_id": canvas_id,
                },
                "subject_selection": {"expected_input_revision": "0", "subjects": list(references)},
            },
            "canvas-message",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": "canvas-command"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": f"canvas-{index}",
                        "tool_name": "apply_canvas_patch",
                        "args": {
                            "request_message_id": str(accepted.message.id),
                            "request_hash": digest({"role": "user", "content": content}),
                            **(authority or {}),
                            "patch": patch,
                        },
                    }
                    for index, patch in enumerate(patches)
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    operation_id = claim_operation(factory, settings, accepted.run.id)
    assert operation_id is not None
    if before_execute:
        before_execute(conversation, accepted)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id)
        ).all()
    return conversation, accepted, claim, operations


def test_canvas_tool_real_grant_replays_one_batch_and_reads_only_its_created_snapshot(
    task_workspace,
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.service.canvas_service import CanvasService

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    text = "本任务新建的节点正文。" * 160
    patch = {
        "expected_revision": saved["row_version"],
        "actions": [
            {"action": "create_node", "key": "story", "values": {"type": "text", "content": text}},
            {
                "action": "create_node",
                "key": "image",
                "values": {"type": "image", "title": "新画面", "prompt": "仅搭建的提示词"},
            },
            {"action": "connect_nodes", "source": "$story", "target": "$image"},
        ],
    }
    conversation, accepted, claim, operations = canvas_tool_run(
        task_workspace,
        project_id,
        saved["source_key"],
        "创建一个文本节点和一个图片节点并连线：搭建新故事流程",
        [patch, patch],
    )
    assert [item.status for item in operations] == ["succeeded", "succeeded"]
    original, duplicate = operations
    assert duplicate.result["already_applied"] is True and duplicate.changes == []
    assert duplicate.result["operation_id"] == str(original.id)
    assert "apply_canvas_patch" in [item["name"] for item in claim.inputs["kwargs"]["tools"]]
    assert len(original.result_refs) == 2
    assert {item["status"] for item in original.result_refs} == {"created"}
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        owner = session.get(AgentConversation, conversation.id)
        descriptor = execute_locked(
            session,
            owner,
            task,
            SimpleNamespace(tool_name="describe_creation_target", arguments={"model_limit": 1}),
        )
        assert descriptor["node_specs"]["version"] == 1
        assert set(descriptor["node_specs"]["types"]) == {"text", "image", "video", "audio"}
        assert descriptor["canvas_revision"] == "3" and descriptor["write_authorized"] is True
        assert len(descriptor["models"]) == 1 and "base_url" not in str(descriptor)
        result = execute_locked(
            session,
            owner,
            task,
            SimpleNamespace(
                tool_name="read_task_results",
                arguments={"operation_id": str(duplicate.id), "character_limit": 81},
            ),
        )
        contents = {}
        while True:
            for item in result["results"]:
                contents[item["id"]] = contents.get(item["id"], "") + item["content"]
                assert item["kind"] == "canvas_node" and item["source_changed"] is False
            if result["next"] is None:
                break
            result = execute_locked(
                session,
                owner,
                task,
                SimpleNamespace(tool_name="read_task_results", arguments=result["next"]),
            )
        story_id = original.result["temporary_ids"]["story"]
        assert json.loads(contents[story_id])["content"] == text
        assert "original-a原正文" not in str(contents)
        assert "原生私有对话" not in str(contents)
        assert "before" not in str(contents)
    with factory() as session:
        task_service(session, settings)
        document = CanvasService(session).read(project_id, canvas_id, private=True)[
            "source_document"
        ]
        assert len(document["nodes"]) == 4 and len(document["connections"]) == 1
        assert document["viewport"] == saved["source_document"]["viewport"]
        version = session.get(AgentRun, accepted.run.id).message_version
    next_claim = AgentRuntimeStore(factory, settings).claim_execution(
        accepted.run.id, version, prepare_decision
    )
    assert next_claim is not None
    directory = json.loads(
        next_claim.inputs["kwargs"]["instructions"].split("任务状态与用户原始输入：", 1)[1]
    )
    assert directory["canvas_write_requests"][0]["create_remaining"] == {"text": 0, "image": 0}
    assert "本任务新建的节点正文" not in next_claim.inputs["kwargs"]["instructions"]
    assert "read_task_results" in [item["name"] for item in next_claim.inputs["kwargs"]["tools"]]
    store = AgentRuntimeStore(factory, settings)
    store.before_send(next_claim, {"fixture": "canvas-result-read"})
    store.save_raw(next_claim, {"fixture_receipt": True})
    assert store.save_result(
        next_claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "canvas-read",
                        "tool_name": "read_task_results",
                        "args": {"operation_id": str(original.id), "ids": [story_id]},
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        read = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "read_task_results",
            )
        )
        assert read.status == "succeeded" and read.changes == []
        assert text in read.result["results"][0]["content"]
        version = session.get(AgentRun, accepted.run.id).message_version
    pending = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert pending is not None and text in str(pending.inputs["kwargs"]["deferred_results"])
    from short_drama.domain.canvas import CanvasNode

    with factory.begin() as session:
        row = session.scalar(
            select(CanvasNode).where(
                CanvasNode.project_id == project_id, CanvasNode.node_key == story_id
            )
        )
        row.archived_at = utcnow()
    with pytest.raises(AgentGatewayError) as error:
        store.before_send(pending, {"fixture": "removed-canvas-result"})
    assert error.value.code == "assistant_subjects_superseded"
    store.fail(pending, error.value)
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    refreshed = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert refreshed is not None
    assert refreshed.inputs["kwargs"]["history"] is None
    assert refreshed.inputs["kwargs"]["deferred_results"] is None
    assert text not in str(refreshed.inputs["kwargs"])


def test_canvas_subject_freezes_private_write_revision_without_disclosing_private_body(
    task_workspace,
):
    from short_drama.domain.canvas import CanvasNodeUserState

    reference = subject_canvas(task_workspace, "私人版本")
    _conversation, accepted, _store, claim = selected_task(task_workspace, [reference])
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        with session.begin():
            task = session.get(AgentTask, accepted.task.id)
            subject = task.context_snapshot["subjects"][0]
            assert subject["write_versions"] == {"row_version": "1", "private_revision": "1"}
            state = session.scalar(
                select(CanvasNodeUserState).where(
                    CanvasNodeUserState.project_id == int(reference["project_id"])
                )
            )
            state.draft_json = {**state.draft_json, "prompt": "后来人工更改的私人提示词"}
            state.row_version += 1
        metadata = service.subject_metadata(accepted.task.id)["items"][0]
        assert metadata["availability"] == "changed"
        assert "write_versions" not in metadata
        assert "私人提示词" not in str(metadata)
    assert "write_versions" not in str(accepted.task.selected_subjects)
    assert "私人提示词" not in str(claim.inputs["kwargs"])


@pytest.mark.parametrize(
    "violation,code",
    [
        ("discussion", "canvas_request_required"),
        ("hash", "canvas_request_required"),
        ("quantity", "assistant_write_quantity"),
        ("connect", "assistant_write_scope"),
        ("scope", "assistant_write_scope"),
        ("field", "assistant_write_scope"),
        ("private", "conflict"),
        ("shared", "conflict"),
        ("late_model", "not_found"),
    ],
)
def test_canvas_tool_rechecks_real_authority_scope_fields_and_versions(
    task_workspace, violation, code
):
    from short_drama.service.canvas_service import CanvasService

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    reference = {
        "project_id": str(project_id),
        "source_kind": "canvas",
        "source_id": saved["source_key"],
        "kind": "node",
        "id": "original-a",
        "revision": "1",
        "content_revision": "1",
    }
    content = "创建一个文本节点：新故事"
    action = {
        "action": "create_node",
        "key": "new",
        "values": {"type": "text", "content": "新正文"},
    }
    actions, authority = [action], None
    if violation == "discussion":
        content = "只讨论创建一个文本节点"
    elif violation == "hash":
        authority = {"request_hash": "0" * 64}
    elif violation == "quantity":
        actions.append({**action, "key": "extra"})
    elif violation == "connect":
        actions.append({"action": "connect_nodes", "source": "$new", "target": "original-a"})
    elif violation == "late_model":
        content = "创建一个文本节点和一个图片节点"
        actions.append(
            {
                "action": "create_node",
                "key": "bad",
                "values": {"type": "image", "model_config_id": "999999"},
            }
        )
    else:
        content = "修改已选节点的提示词：新提示词"
        actions = [
            {
                "action": "update_node",
                "node": "original-b" if violation == "scope" else "original-a",
                "row_version": "2" if violation == "shared" else "1",
                "private_revision": "2" if violation == "private" else "1",
                "patch": {"title": "额外标题"} if violation == "field" else {"prompt": "新提示词"},
            }
        ]

    def edit_private(_conversation, _accepted):
        if violation != "private":
            return
        with factory() as session:
            task_service(session, settings)
            native = CanvasService(session)
            document = native.read(project_id, canvas_id, private=True)["source_document"]
            document["nodes"][0]["metadata"]["prompt"] = "人工新提示词"
            native.commit(
                project_id,
                canvas_id,
                {"expected_row_version": "2", "source_document": document},
                "private-human-edit",
            )

    _conversation, _accepted, claim, operations = canvas_tool_run(
        task_workspace,
        project_id,
        saved["source_key"],
        content,
        [{"expected_revision": "2", "actions": actions}],
        references=[reference],
        authority=authority,
        before_execute=edit_private,
    )
    assert len(operations) == 1 and operations[0].error["code"] == code
    assert operations[0].changes == [] and operations[0].result_refs == []
    assert operations[0].status == ("conflict" if code == "conflict" else "failed")
    if violation == "discussion":
        assert "apply_canvas_patch" not in [
            item["name"] for item in claim.inputs["kwargs"]["tools"]
        ]
    with factory() as session:
        task_service(session, settings)
        document = CanvasService(session).read(project_id, canvas_id, private=True)[
            "source_document"
        ]
        assert document["revision"] == "2" and len(document["nodes"]) == 2
        assert document["connections"] == []
        assert document["nodes"][0]["metadata"]["prompt"] == (
            "人工新提示词" if violation == "private" else "original-a私人提示词"
        )


def test_canvas_tool_completes_one_started_batch_before_stopping(task_workspace):
    from short_drama.service.canvas_service import CanvasService

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace

    def stop(conversation, accepted):
        with factory() as session:
            service = task_service(session, settings)
            stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "canvas-stop")
            assert stopped.task.control_state == "stop_pending"

    patch = {
        "expected_revision": "2",
        "actions": [
            {
                "action": "create_node",
                "key": "one",
                "values": {"type": "text", "content": "保留已开始的结果"},
            }
        ],
    }
    _conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        project_id,
        saved["source_key"],
        "创建两个文本节点",
        [patch, patch],
        before_execute=stop,
    )
    assert len(operations) == 1 and operations[0].status == "succeeded"
    with factory() as session:
        task_service(session, settings)
        document = CanvasService(session).read(project_id, canvas_id, private=True)[
            "source_document"
        ]
        assert len(document["nodes"]) == 3
        task = session.get(AgentTask, accepted.task.id)
        assert task.status == "stopped" and task.control_state == "none"


@pytest.mark.parametrize("change", ["edited", "deleted", "archived", "other_owner"])
def test_canvas_result_reader_preserves_saved_content_and_rechecks_current_access(
    task_workspace, change
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.domain import Project, ProjectCanvas
    from short_drama.domain.canvas import CanvasNode, CanvasNodeUserState

    project_id, _canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        project_id,
        saved["source_key"],
        "创建一个图片节点：只搭建",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": "picture",
                        "values": {"type": "image", "prompt": "最初保存的提示词"},
                    }
                ],
            }
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    key = operation.result["temporary_ids"]["picture"]
    with factory.begin() as session:
        node = session.scalar(
            select(CanvasNode).where(
                CanvasNode.project_id == project_id, CanvasNode.node_key == key
            )
        )
        if change == "edited":
            state = session.scalar(
                select(CanvasNodeUserState).where(
                    CanvasNodeUserState.project_id == project_id,
                    CanvasNodeUserState.node_key == key,
                )
            )
            state.draft_json = {**state.draft_json, "prompt": "人工后来的私人修改"}
            state.row_version += 1
        elif change == "deleted":
            node.archived_at = utcnow()
        elif change == "archived":
            session.get(ProjectCanvas, node.canvas_id).archived_at = utcnow()
        else:
            session.add(
                User(
                    id=2,
                    username="canvas-second",
                    display_name="Second",
                    email="canvas-second@example.test",
                    password_hash="test-only",
                    status="active",
                    email_verified_at=utcnow(),
                    created_at=utcnow(),
                )
            )
            session.flush()
            session.get(Project, project_id).owner_user_id = 2
    with factory.begin() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        owner = session.get(AgentConversation, conversation.id)
        read = SimpleNamespace(
            tool_name="read_task_results", arguments={"operation_id": str(operation.id)}
        )
        if change == "edited":
            result = execute_locked(session, owner, task, read)
            item = result["results"][0]
            assert item["source_changed"] is True and item["current_private_revision"] == "2"
            assert json.loads(item["content"])["prompt"] == "最初保存的提示词"
            assert "人工后来的私人修改" not in str(result)
        else:
            with pytest.raises(NotFound):
                execute_locked(session, owner, task, read)


def test_canvas_result_reader_cannot_read_selected_old_nodes_or_another_task(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked

    project_id, _canvas_id, saved = canvas_patch_workspace(task_workspace)
    conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        project_id,
        saved["source_key"],
        "创建一个文本节点：新正文",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": "story",
                        "values": {"type": "text", "content": "只允许本任务读取的生成正文"},
                    }
                ],
            }
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        owner = session.get(AgentConversation, conversation.id)
        task = session.get(AgentTask, accepted.task.id)
        for ids in (["original-a"], [operation.result_refs[0]["id"]] * 2):
            with pytest.raises(WorkflowError):
                execute_locked(
                    session,
                    owner,
                    task,
                    SimpleNamespace(
                        tool_name="read_task_results",
                        arguments={"operation_id": str(operation.id), "ids": ids},
                    ),
                )
    _other_conversation, other = new_task(task_workspace)
    with factory.begin() as session:
        task_service(session, settings)
        with pytest.raises(NotFound):
            execute_locked(
                session,
                session.get(AgentConversation, other.task.conversation_id),
                session.get(AgentTask, other.task.id),
                SimpleNamespace(
                    tool_name="read_task_results", arguments={"operation_id": str(operation.id)}
                ),
            )


def test_canvas_result_reader_does_not_treat_later_move_snapshots_as_new_private_content(
    task_workspace,
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.authorization import digest
    from short_drama.service.canvas_service import CanvasService

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        project_id,
        saved["source_key"],
        "创建一个图片节点：只搭建",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": "picture",
                        "values": {"type": "image", "prompt": "本任务原始提示词"},
                    }
                ],
            }
        ],
    )
    creation = operations[0]
    assert creation.status == "succeeded"
    key = creation.result["temporary_ids"]["picture"]
    content = "移动本任务节点：向右"
    with factory() as session:
        service = task_service(session, settings)
        native = CanvasService(session)
        document = native.read(project_id, canvas_id, private=True)["source_document"]
        next(node for node in document["nodes"] if node["id"] == key)["metadata"]["prompt"] = (
            "人工后来输入的私人正文"
        )
        native.commit(
            project_id,
            canvas_id,
            {"expected_row_version": "3", "source_document": document},
            "human-private-result",
        )
        supplemented = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "task_id": str(accepted.task.id),
                "subject_selection": {
                    "expected_input_revision": "1",
                    "subjects": [
                        {
                            "project_id": str(project_id),
                            "source_kind": "canvas",
                            "source_id": saved["source_key"],
                            "kind": "node",
                            "id": key,
                            "revision": "1",
                            "content_revision": "1",
                        }
                    ],
                },
            },
            "move-after-human-edit",
        )
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": "move-created-result"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "move-created-node",
                        "tool_name": "apply_canvas_patch",
                        "args": {
                            "request_message_id": str(supplemented.message.id),
                            "request_hash": digest({"role": "user", "content": content}),
                            "patch": {
                                "expected_revision": "3",
                                "actions": [
                                    {
                                        "action": "move_node",
                                        "node": key,
                                        "row_version": "1",
                                        "private_revision": "2",
                                        "position": {"x": 1600, "y": 40},
                                    }
                                ],
                            },
                        },
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(factory, settings, accepted.run.id)
    with factory.begin() as session:
        task_service(session, settings)
        moved = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.id != creation.id,
                AgentOperation.tool_name == "apply_canvas_patch",
            )
        )
        assert moved.status == "succeeded"
        assert "人工后来输入的私人正文" in str(moved.changes)
        owner = session.get(AgentConversation, conversation.id)
        task = session.get(AgentTask, accepted.task.id)
        with pytest.raises(WorkflowError) as error:
            execute_locked(
                session,
                owner,
                task,
                SimpleNamespace(
                    tool_name="read_task_results", arguments={"operation_id": str(moved.id)}
                ),
            )
        assert error.value.code == "assistant_result_scope"
        result = execute_locked(
            session,
            owner,
            task,
            SimpleNamespace(
                tool_name="read_task_results", arguments={"operation_id": str(creation.id)}
            ),
        )
        assert "本任务原始提示词" in str(result) and "人工后来输入的私人正文" not in str(result)
        assert result["results"][0]["source_changed"] is True


def test_canvas_patch_builds_four_native_nodes_and_preserves_unselected_work_and_viewport(
    task_workspace,
):
    from short_drama.domain.canvas import CanvasRevision
    from short_drama.service.assistant_canvas_service import AssistantCanvasService
    from short_drama.service.canvas_service import CanvasService
    from short_drama.utils.snowflake import next_id

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        video = AIModelConfigService(session).create(
            {
                "service_type": "video",
                "name": "Single reference",
                "provider": "compatible",
                "model_key": "video",
                "base_url": "https://model.invalid/v1",
                "runtime_profile": {
                    "protocol": "openai-video",
                    "capability_config": {"video": {"references": {"maxImages": 1}}},
                },
            }
        )
    with factory.begin() as session:
        assert "actor" not in session.info
        result = AssistantCanvasService(session).apply_locked(
            project_id,
            saved["source_key"],
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": kind,
                        "values": {
                            "type": kind,
                            "prompt": f"{kind}已保存提示词",
                            **({"content": "新文本正文"} if kind == "text" else {}),
                            **({"model_config_id": str(video.id)} if kind == "video" else {}),
                        },
                    }
                    for kind in ("text", "image", "video", "audio")
                ]
                + [
                    {"action": "connect_nodes", "source": source, "target": target}
                    for source, target in (
                        ("$text", "$image"),
                        ("$image", "$video"),
                        ("$text", "$audio"),
                    )
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_node_versions={},
        )
        assert "actor" not in session.info
        assert result["canvas_revision"] == "3"
        assert len(result["result_refs"]) == 4 and len(result["changes"]) == 7
        assert {item["status"] for item in result["result_refs"]} == {"created"}
        assert "原正文" not in str(result) and "私人提示词" not in str(result)
    with factory() as session:
        task_service(session, settings)
        current = CanvasService(session).read(project_id, canvas_id, private=True)[
            "source_document"
        ]
        assert current["nodes"][:2] == saved["source_document"]["nodes"]
        assert current["viewport"] == saved["source_document"]["viewport"]
        assert current["chatSessions"] == saved["source_document"]["chatSessions"]
        nodes = {item["id"]: item for item in current["nodes"]}
        for kind, identifier in result["temporary_ids"].items():
            assert nodes[identifier]["type"] == kind
            assert nodes[identifier]["metadata"]["prompt"] == f"{kind}已保存提示词"
            if kind != "text":
                assert nodes[identifier]["metadata"]["content"] == ""
                assert nodes[identifier]["metadata"]["nodeRole"] == "generator"
        assert len(current["connections"]) == 3
        assert session.scalar(select(func.count()).select_from(CanvasRevision)) >= 1


@pytest.mark.parametrize("violation", ["type", "cycle", "scope", "stale", "model"])
def test_canvas_patch_rejects_late_invalid_actions_without_partial_graph_or_history(
    task_workspace, violation
):
    from short_drama.domain.canvas import CanvasNode, CanvasRevision
    from short_drama.service.assistant_canvas_service import AssistantCanvasService
    from short_drama.utils.snowflake import next_id

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    actions = [
        {"action": "create_node", "key": key, "values": {"type": kind}}
        for key, kind in (("image", "image"), ("audio", "audio"))
    ]
    if violation == "type":
        actions.append({"action": "connect_nodes", "source": "$image", "target": "$audio"})
    elif violation == "cycle":
        actions += [
            {"action": "connect_nodes", "source": source, "target": target}
            for source, target in (("$image", "$audio"), ("$audio", "$image"))
        ]
    elif violation == "scope":
        actions.append({"action": "connect_nodes", "source": "original-a", "target": "$image"})
    elif violation == "model":
        actions[0]["values"]["model_config_id"] = str(task_workspace[2])
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        before = session.scalar(select(func.count()).select_from(CanvasRevision))
        with pytest.raises((WorkflowError, NotFound, Conflict)), session.begin_nested():
            AssistantCanvasService(session).apply_locked(
                project_id,
                saved["source_key"],
                {
                    "expected_revision": "1" if violation == "stale" else "2",
                    "actions": actions,
                },
                owner_user_id=1,
                operation_id=next_id(),
                allowed_node_versions={},
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CanvasNode)
                .where(CanvasNode.canvas_id == canvas_id)
            )
            == 2
        )
        assert session.scalar(select(func.count()).select_from(CanvasRevision)) == before


def test_canvas_patch_checks_private_versions_and_records_private_only_changes(task_workspace):
    from short_drama.service.assistant_canvas_service import AssistantCanvasService
    from short_drama.service.canvas_service import CanvasService
    from short_drama.utils.snowflake import next_id

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        document = saved["source_document"]
        document["nodes"][0]["metadata"]["prompt"] = "后来人工修改的提示词"
        CanvasService(session).commit(
            project_id,
            canvas_id,
            {"expected_row_version": "2", "source_document": document},
            "manual-private-edit",
        )
    with factory.begin() as session:
        task_service(session, settings)
        patch = {
            "expected_revision": "2",
            "actions": [
                {
                    "action": "update_node",
                    "node": "original-a",
                    "row_version": "1",
                    "private_revision": "2",
                    "patch": {"prompt": "新授权提示词"},
                }
            ],
        }
        adapter = AssistantCanvasService(session)
        with pytest.raises(Conflict), session.begin_nested():
            adapter.apply_locked(
                project_id,
                saved["source_key"],
                patch,
                owner_user_id=1,
                operation_id=next_id(),
                allowed_node_versions={"original-a": {"row_version": "1", "private_revision": "1"}},
            )
        result = adapter.apply_locked(
            project_id,
            saved["source_key"],
            patch,
            owner_user_id=1,
            operation_id=next_id(),
            allowed_node_versions={"original-a": {"row_version": "1", "private_revision": "2"}},
        )
        assert result["canvas_revision"] == "2"
        change = result["changes"][0]
        assert change["before"]["document"]["metadata"]["prompt"] == "后来人工修改的提示词"
        assert change["after"]["document"]["metadata"]["prompt"] == "新授权提示词"
        assert (
            change["before"]["private_revision"] == "2"
            and change["after"]["private_revision"] == "3"
        )
        assert result["result_refs"][0]["row_version"] == "1"


def test_canvas_patch_delete_protects_related_scope_and_keeps_mechanical_differences_private(
    task_workspace,
):
    from short_drama.service.assistant_canvas_service import AssistantCanvasService
    from short_drama.service.canvas_service import CanvasService
    from short_drama.utils.snowflake import next_id

    project_id, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        document = saved["source_document"]
        document["connections"] = [
            {"id": "old-edge", "fromNodeId": "original-a", "toNodeId": "original-b"}
        ]
        CanvasService(session).commit(
            project_id,
            canvas_id,
            {"expected_row_version": "2", "source_document": document},
            "old-connection",
        )
    with factory.begin() as session:
        task_service(session, settings)
        adapter = AssistantCanvasService(session)
        patch = {
            "expected_revision": "3",
            "actions": [
                {
                    "action": "delete_node",
                    "node": "original-a",
                    "row_version": "1",
                    "private_revision": "1",
                }
            ],
        }
        allowed = {"original-a": {"row_version": "1", "private_revision": "1"}}
        with pytest.raises(WorkflowError) as raised, session.begin_nested():
            adapter.apply_locked(
                project_id,
                saved["source_key"],
                patch,
                owner_user_id=1,
                operation_id=next_id(),
                allowed_node_versions=allowed,
            )
        assert raised.value.code == "assistant_delete_impact"
        assert raised.value.details == {"node_ids": ["original-b"]}
        result = adapter.apply_locked(
            project_id,
            saved["source_key"],
            patch,
            owner_user_id=1,
            operation_id=next_id(),
            allowed_node_versions={
                **allowed,
                "original-b": {"row_version": "1", "private_revision": "1"},
            },
        )
        assert [ref["id"] for ref in result["result_refs"]] == ["original-a"]
        assert result["result_refs"][0]["status"] == "deleted"
        changes = {item["id"]: item for item in result["changes"]}
        assert set(changes) == {"original-a", "original-b", "old-edge"}
        assert changes["original-b"]["after"]["z_index"] == 0
        assert changes["original-b"]["after"]["row_version"] == "2"
        assert "original-b私人提示词" not in str(result["result_refs"])


@pytest.mark.parametrize("ceiling", ["content", "private", "mechanical", "canvas"])
def test_canvas_patch_rejects_overflow_in_every_affected_version(task_workspace, ceiling):
    from short_drama.domain import ProjectCanvas
    from short_drama.domain.canvas import CanvasNode, CanvasNodeUserState
    from short_drama.schemas.base import UINT64_MAX
    from short_drama.service.assistant_canvas_service import AssistantCanvasService
    from short_drama.utils.snowflake import next_id

    project_id, _canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        node = session.scalar(
            select(CanvasNode).where(
                CanvasNode.project_id == project_id,
                CanvasNode.node_key == ("original-b" if ceiling == "mechanical" else "original-a"),
            )
        )
        if ceiling == "content":
            node.content_version = UINT64_MAX
        elif ceiling == "mechanical":
            node.row_version = UINT64_MAX
        elif ceiling == "private":
            state = session.scalar(
                select(CanvasNodeUserState).where(
                    CanvasNodeUserState.project_id == project_id,
                    CanvasNodeUserState.node_key == "original-a",
                )
            )
            state.row_version = UINT64_MAX
        else:
            session.get(ProjectCanvas, node.canvas_id).row_version = UINT64_MAX
    with factory.begin() as session:
        adapter = AssistantCanvasService(session)
        allowed = {
            "original-a": {
                "row_version": "1",
                "private_revision": str(UINT64_MAX) if ceiling == "private" else "1",
            }
        }
        action = {
            "action": "update_node",
            "node": "original-a",
            **allowed["original-a"],
            "patch": {"prompt" if ceiling == "private" else "content": "不得部分保存"},
        }
        if ceiling == "mechanical":
            action = {"action": "delete_node", "node": "original-a", **allowed["original-a"]}
        with pytest.raises(Conflict, match="版本已达到上限"), session.begin_nested():
            adapter.apply_locked(
                project_id,
                saved["source_key"],
                {
                    "expected_revision": str(UINT64_MAX) if ceiling == "canvas" else "2",
                    "actions": [action],
                },
                owner_user_id=1,
                operation_id=next_id(),
                allowed_node_versions=allowed,
            )
        row = session.scalar(
            select(CanvasNode).where(
                CanvasNode.project_id == project_id, CanvasNode.node_key == "original-a"
            )
        )
        assert (
            row.archived_at is None
            and row.content_json["metadata"]["content"] == "original-a原正文"
        )


def undo_test_batch(workspace):
    reference = subject_assets(workspace)[0]
    patch = {
        "storyboard_version": "1",
        "actions": [
            {"action": "create_asset", "key": key, "values": {"kind": "character", "name": key}}
            for key in ("hero", "other")
        ]
        + [
            {
                "action": "create_shot",
                "key": "opening",
                "values": {"title": "车站"},
                "assets": ["$hero"],
            }
        ],
    }
    conversation, accepted, _claim, operations = standard_tool_run(
        workspace, "创建两个角色和一个分镜并关联：雨夜车站", [patch], references=[reference]
    )
    assert operations[0].status == "succeeded"
    return conversation, accepted, operations[0]


def settled_media_undo_flow(workspace, tmp_path, kind, **kwargs):
    from short_drama.agent.media_runtime import collect_media_results

    if kind == "image":
        flow = media_tool_run(workspace, **kwargs)
        provider = None
    elif kind == "video":
        flow = shot_video_flow(workspace, **kwargs)
        provider = shot_video_provider(tmp_path)
    elif kind == "canvas":
        flow = canvas_media_flow(workspace, **kwargs)
        provider = None
    elif kind == "sound":
        flow = dialogue_audio_flow(workspace, **kwargs)
        provider = dialogue_audio_provider(tmp_path)
    else:
        flow = voice_design_flow(workspace, **kwargs)
        provider = voice_design_provider(tmp_path)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-before-media-undo"
        )
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    return flow


def continue_undo_flow_tool(flow, content, tool_name, arguments, *, key, references=None):
    from short_drama.agent.authorization import digest

    with flow.factory() as session:
        service = task_service(session, flow.settings)
        task = session.get(AgentTask, flow.accepted.task.id)
        revision = str(task.input_revision)
        session.rollback()
        message = service.send_task_message(
            flow.conversation.id,
            {
                "task_id": str(flow.accepted.task.id),
                "content": content,
                **(
                    {
                        "subject_selection": {
                            "expected_input_revision": revision,
                            "subjects": references,
                        }
                    }
                    if references is not None
                    else {}
                ),
            },
            f"{key}-message",
        )
        resumed = service.control_task(
            flow.accepted.task.id,
            {"kind": "resume", "expected_control_seq": str(message.task.control_seq)},
            f"{key}-resume",
        )
        version = session.get(AgentRun, resumed.run.id).message_version
    store = AgentRuntimeStore(flow.factory, flow.settings)
    claim = store.claim_execution(resumed.run.id, version, prepare_decision)
    assert claim is not None
    args = arguments(message) if callable(arguments) else arguments
    store.before_send(claim, {"fixture": key})
    store.save_raw(claim, {"fixture_receipt": True})
    store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": key,
                        "tool_name": tool_name,
                        "args": {
                            "request_message_id": str(message.message.id),
                            "request_hash": digest({"role": "user", "content": content}),
                            **args,
                        },
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(flow.factory, flow.settings, resumed.run.id)
    with flow.factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == resumed.run.id)
        )
        assert operation.status in {"succeeded", "waiting_media"}, operation.error
        identifier = operation.id
        session.rollback()
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, f"{key}-stop"
        )
    return identifier


@pytest.mark.parametrize("kind", ["shot_image", "video"])
@pytest.mark.parametrize(
    "manual_change", [None, "waiting", "after_adoption", "group_after_latest", "group_after_middle"]
)
def test_batch_undo_reverse_media_after_structure_preserves_interleaved_storyboard_versions(
    task_workspace, tmp_path, kind, manual_change
):
    from copy import deepcopy
    from types import SimpleNamespace

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.agent.media_tools import media_requests_locked
    from short_drama.domain import Project, ShotScript

    if kind == "shot_image":
        reference, _groups = image_group_workspace(task_workspace, kind="shot")
        flow = settled_media_undo_flow(task_workspace, tmp_path, "image", references=[reference])
        provider = None
    else:
        flow = settled_media_undo_flow(task_workspace, tmp_path, "video")
        provider = shot_video_provider(tmp_path)
    original = flow.operation_id
    with flow.factory() as session:
        task_service(session, flow.settings)
        shot = session.get(ShotScript, int(flow.reference["id"]))
        project = session.get(Project, shot.project_id)
        patch = {
            "storyboard_version": str(project.storyboard_version),
            "actions": [
                {
                    "action": "update_shot",
                    "shot": str(shot.id),
                    "patch": {"row_version": str(shot.row_version), "script": "中间批次正文"},
                }
            ],
        }
    structural = continue_undo_flow_tool(
        flow,
        "修改已选分镜的正文：中间批次正文",
        "apply_standard_patch",
        {"patch": patch},
        key="middle",
    )
    with flow.factory() as session:
        task_service(session, flow.settings)
        shot = session.get(ShotScript, int(flow.reference["id"]))
        references = deepcopy(flow.references)
        for reference in references:
            if reference["kind"] == "shot":
                reference["revision"] = str(shot.row_version)

    def arguments(message):
        with flow.factory.begin() as session:
            entries = media_requests_locked(
                session,
                session.get(AgentConversation, flow.conversation.id),
                session.get(AgentTask, flow.accepted.task.id),
            )
            entry = next(
                row for row in entries if row["request_message_id"] == str(message.message.id)
            )
        request = deepcopy(flow.body["requests"][0])
        request["row_version"] = entry["targets"][0]["row_version"]
        return {"requests": [request]}

    latest = continue_undo_flow_tool(
        flow,
        "为已选分镜生成一张图片：新画面"
        if kind == "shot_image"
        else "为已选分镜生成一段视频：新画面",
        "submit_generation_batch",
        arguments,
        key="latest-media",
        references=references,
    )

    def advance_manual_version():
        from short_drama.service.project_storyboard_service import ProjectStoryboardService

        with flow.factory() as session:
            task_service(session, flow.settings)
            revision = str(
                session.get(Project, int(flow.reference["project_id"])).storyboard_version
            )
            session.rollback()
            ProjectStoryboardService(session).create(
                flow.reference["project_id"],
                {"storyboard_version": revision, "title": "人工新增镜头", "script": "保留人工内容"},
                "manual-other-shot",
            )

    def choose_current_primary():
        from short_drama.service.image_group_service import ImageGroupService

        with flow.factory() as current:
            task_service(current, flow.settings)
            native = ImageGroupService(current)
            with current.begin():
                row, _scope = native.target("shot", flow.reference["id"])
                group = native.groups.for_target("shot", row.id, current=True, lock=True).first()
                assert group is not None
                item = next(
                    item
                    for item in native.groups.items(group.id, lock=True)
                    if item.output_index == group.primary_index
                )
                native.primary_locked(
                    group.id,
                    {
                        "item_id": str(item.id),
                        "expected_primary_version": str(group.primary_version),
                        "expected_item_version": str(item.item_version),
                        "expected_target_version": str(row.row_version),
                    },
                )

    if manual_change == "waiting":
        advance_manual_version()
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(SimpleNamespace(**{**vars(flow), "operation_id": latest}), worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    if manual_change == "after_adoption":
        advance_manual_version()
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        detail = service.operation_detail(latest)
        assert detail.outcome == "succeeded", detail.error
        preview = service.undo_preview(latest)
        assert all(item.allowed for item in preview.items)
        service.undo_batch(
            latest,
            {"preview_hash": preview.preview_hash, "selected_keys": [i.key for i in preview.items]},
            "undo-latest-media",
        )
        if manual_change == "group_after_latest":
            choose_current_primary()
        middle = service.undo_preview(structural)
        if manual_change in {"waiting", "after_adoption", "group_after_latest"}:
            assert not any(item.allowed for item in middle.items)
            return
        assert all(item.allowed for item in middle.items), [item.reason for item in middle.items]
        service.undo_batch(
            structural,
            {"preview_hash": middle.preview_hash, "selected_keys": [i.key for i in middle.items]},
            "undo-middle-structure",
        )
        if manual_change == "group_after_middle":
            choose_current_primary()
        first = service.undo_preview(original)
        if manual_change == "group_after_middle":
            assert not any(item.allowed for item in first.items)
            return
        assert all(item.allowed for item in first.items), [item.reason for item in first.items]
        service.undo_batch(
            original,
            {"preview_hash": first.preview_hash, "selected_keys": [i.key for i in first.items]},
            "undo-first-media",
        )


@pytest.mark.parametrize("kind", ["image", "canvas", "sound", "voice"])
def test_batch_undo_reverse_three_batches_preserves_native_media_bindings(
    task_workspace, tmp_path, kind
):
    from copy import deepcopy
    from types import SimpleNamespace

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.agent.media_tools import media_requests_locked
    from short_drama.domain import Asset
    from short_drama.service.assistant_dialogue_service import sound_document_binding

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        source = service.operation_detail(flow.operation_id)
        ref = source.result_refs[0]
        session.rollback()
        if kind == "canvas":
            content, tool = "修改已选节点的提示词：中间正文", "apply_canvas_patch"
            patch = {
                "expected_revision": source.result["canvas_revision"],
                "actions": [
                    {
                        "action": "update_node",
                        "node": ref["id"],
                        "row_version": ref["row_version"],
                        "private_revision": ref["private_revision"],
                        "patch": {"prompt": "中间正文"},
                    }
                ],
            }
        elif kind == "sound":
            content, tool = "修改已选台词的正文：中间正文", "apply_standard_patch"
            patch = {
                "sound_revision": sound_text_revision(task_workspace, ref["project_id"]),
                "actions": [
                    {
                        "action": "update_sound_line",
                        "line": ref["id"],
                        "patch": {"text": "中间正文"},
                    }
                ],
            }
        else:
            content, tool = "修改已选素材的提示词：中间正文", "apply_standard_patch"
            patch = {
                "actions": [
                    {
                        "action": "update_asset",
                        "asset": ref["id"],
                        "patch": {"row_version": ref["row_version"], "prompt": "中间正文"},
                    }
                ]
            }
    middle = continue_undo_flow_tool(flow, content, tool, {"patch": patch}, key="middle")
    with flow.factory() as session:
        task_service(session, flow.settings)
        references = deepcopy(flow.references)
        if kind in {"image", "voice"}:
            references[0]["revision"] = str(session.get(Asset, int(ref["id"])).row_version)
        elif kind == "sound":
            references[0]["revision"] = sound_document_binding(session, ref["project_id"])[
                "row_version"
            ]
        else:
            from short_drama.domain.canvas import CanvasNode

            references[0]["revision"] = str(
                session.scalar(
                    select(CanvasNode.row_version).where(
                        CanvasNode.project_id == int(ref["project_id"]),
                        CanvasNode.node_key == ref["id"],
                    )
                )
            )

    def arguments(message):
        with flow.factory.begin() as session:
            entries = media_requests_locked(
                session,
                session.get(AgentConversation, flow.conversation.id),
                session.get(AgentTask, flow.accepted.task.id),
            )
            entry = next(
                row for row in entries if row["request_message_id"] == str(message.message.id)
            )
        target = entry["targets"][0]
        request = deepcopy(flow.body["requests"][0])
        request.update({key: value for key, value in target.items() if key not in {"kind", "id"}})
        return {"requests": [request]}

    latest = continue_undo_flow_tool(
        flow,
        {
            "image": "为已选素材生成一张图片：新画面",
            "canvas": "为已选图片节点生成一个结果：新画面",
            "sound": "为已选台词生成配音",
            "voice": "为已选角色生成一个音色：新的声音",
        }[kind],
        "submit_generation_batch",
        arguments,
        key="latest-media",
        references=references,
    )
    provider = (
        dialogue_audio_provider(tmp_path)
        if kind == "sound"
        else voice_design_provider(tmp_path)
        if kind == "voice"
        else None
    )
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(SimpleNamespace(**{**vars(flow), "operation_id": latest}), worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        assert service.operation_detail(latest).outcome == "succeeded"
        for operation_id in (latest, middle, flow.operation_id):
            preview = service.undo_preview(operation_id)
            assert preview.items and all(item.allowed for item in preview.items), [
                item.reason for item in preview.items
            ]
            service.undo_batch(
                operation_id,
                {
                    "preview_hash": preview.preview_hash,
                    "selected_keys": [item.key for item in preview.items],
                },
                f"reverse-{operation_id}",
            )


@pytest.mark.parametrize("kind", ["image", "video", "canvas", "sound", "voice"])
@pytest.mark.parametrize("later_edit", [False, True])
def test_batch_undo_reverse_batches_restores_media_after_a_later_structural_batch(
    task_workspace, tmp_path, kind, later_edit
):
    from short_drama.agent.authorization import digest

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        source = service.operation_detail(flow.operation_id)
        ref = source.result_refs[0]
        session.rollback()
        if kind == "canvas":
            content = "修改已选节点的提示词：后来改写"
            tool_name = "apply_canvas_patch"
            patch = {
                "expected_revision": source.result["canvas_revision"],
                "actions": [
                    {
                        "action": "update_node",
                        "node": ref["id"],
                        "row_version": ref["row_version"],
                        "private_revision": ref["private_revision"],
                        "patch": {"prompt": "后来改写"},
                    }
                ],
            }
        elif kind == "sound":
            content, tool_name = "修改已选台词的正文：后来改写", "apply_standard_patch"
            patch = {
                "sound_revision": sound_text_revision(task_workspace, ref["project_id"]),
                "actions": [
                    {
                        "action": "update_sound_line",
                        "line": ref["id"],
                        "patch": {"text": "后来改写"},
                    }
                ],
            }
        else:
            tool_name = "apply_standard_patch"
            target_kind = "shot" if kind == "video" else "asset"
            content = (
                "修改已选分镜的正文：后来改写"
                if target_kind == "shot"
                else "修改已选素材的提示词：后来改写"
            )
            patch = {
                "actions": [
                    {
                        "action": f"update_{target_kind}",
                        target_kind: ref["id"],
                        "patch": {
                            "row_version": ref["row_version"],
                            "script" if target_kind == "shot" else "prompt": "后来改写",
                        },
                    }
                ]
            }
            if target_kind == "shot":
                from short_drama.domain import Project

                with flow.factory() as current:
                    task_service(current, flow.settings)
                    patch["storyboard_version"] = str(
                        current.get(Project, int(ref["project_id"])).storyboard_version
                    )
        supplementary = service.send_task_message(
            flow.conversation.id,
            {"task_id": str(flow.accepted.task.id), "content": content},
            "later-structure-message",
        )
        resumed = service.control_task(
            flow.accepted.task.id,
            {"kind": "resume", "expected_control_seq": str(supplementary.task.control_seq)},
            "resume-later-structure",
        )
    store = AgentRuntimeStore(flow.factory, flow.settings)
    with flow.factory() as session:
        run_version = session.get(AgentRun, resumed.run.id).message_version
    claim = store.claim_execution(resumed.run.id, run_version, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": "later-structure"})
    store.save_raw(claim, {"fixture_receipt": True})
    store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "later-structure",
                        "tool_name": tool_name,
                        "args": {
                            "request_message_id": str(supplementary.message.id),
                            "request_hash": digest({"role": "user", "content": content}),
                            "patch": patch,
                        },
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(flow.factory, flow.settings, resumed.run.id)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        later = session.scalar(
            select(AgentOperation)
            .where(AgentOperation.run_id == resumed.run.id)
            .order_by(AgentOperation.id.desc())
        )
        assert later.status == "succeeded", later.error
        later_id = later.id
        session.rollback()
        service.control_task(flow.accepted.task.id, {"kind": "stop"}, "stop-later-structure")
        preview = service.undo_preview(later_id)
        assert all(item.allowed for item in preview.items)
        service.undo_batch(
            later_id,
            {"preview_hash": preview.preview_hash, "selected_keys": [i.key for i in preview.items]},
            "undo-later-structure",
        )
        if later_edit:
            from short_drama.domain import Asset, ShotScript
            from short_drama.domain.canvas import CanvasNode
            from short_drama.domain.project_sound import ProjectSound

            with flow.factory.begin() as current:
                task_service(current, flow.settings)
                row = (
                    current.get(ShotScript, int(ref["id"]))
                    if kind == "video"
                    else current.get(ProjectSound, flow.assembly_id)
                    if kind == "sound"
                    else current.scalar(
                        select(CanvasNode).where(
                            CanvasNode.project_id == int(ref["project_id"]),
                            CanvasNode.node_key == ref["id"],
                        )
                    )
                    if kind == "canvas"
                    else current.get(Asset, int(ref["id"]))
                )
                row.row_version += 1
        original = service.undo_preview(flow.operation_id)
        if later_edit:
            assert not any(item.allowed for item in original.items)
            return
        assert all(item.allowed for item in original.items), [
            item.reason for item in original.items
        ]
        service.undo_batch(
            flow.operation_id,
            {
                "preview_hash": original.preview_hash,
                "selected_keys": [i.key for i in original.items],
            },
            "undo-earlier-media",
        )


@pytest.mark.parametrize("kind", ["image", "video", "canvas", "sound", "voice"])
def test_batch_undo_media_restores_adoption_and_keeps_generated_history(
    task_workspace, tmp_path, kind
):
    from short_drama.domain import AIGenerationRecord, Asset, MediaAsset, MediaFile, ShotVideo
    from short_drama.domain.agent_media import AgentMediaRequest
    from short_drama.domain.native_voice import CharacterVoice
    from short_drama.domain.project_sound import ProjectSound
    from short_drama.service.canvas_service import CanvasService

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        preview = service.undo_preview(flow.operation_id)
        assert preview.items and all(item.allowed for item in preview.items), preview
        body = {
            "preview_hash": preview.preview_hash,
            "selected_keys": [item.key for item in preview.items],
        }
        receipt = service.undo_batch(flow.operation_id, body, "explicit-media-undo")
        replay = service.undo_batch(flow.operation_id, body, "explicit-media-undo")
        assert replay.id == receipt.id
    with flow.factory() as session:
        task_service(session, flow.settings)
        original = session.get(AgentOperation, flow.operation_id)
        assert original.status == "succeeded"
        assert original.result_refs[0]["status"] == ("bound" if kind == "canvas" else "adopted")
        intent = session.scalar(
            select(AgentMediaRequest).where(AgentMediaRequest.operation_id == flow.operation_id)
        )
        outputs = list(
            session.scalars(
                select(MediaAsset)
                .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
                .where(AIGenerationRecord.task_id == intent.generation_task_id)
            )
        )
        assert outputs and all(session.get(MediaFile, output.media_id) for output in outputs)
        if kind == "image":
            row = session.get(Asset, int(flow.reference["id"]))
            assert row.media_id is None and row.row_version == 3
        elif kind == "video":
            assert (
                session.scalar(
                    select(ShotVideo).where(ShotVideo.shot_id == int(flow.reference["id"]))
                )
                is None
            )
        elif kind == "canvas":
            metadata = intent.frozen_target["before"]["document"]["metadata"]
            session.rollback()
            document = CanvasService(session).read(
                flow.reference["project_id"], flow.canvas_id, private=True
            )["source_document"]
            node = next(node for node in document["nodes"] if node["id"] == flow.reference["id"])
            assert node["metadata"] == metadata
        elif kind == "sound":
            document = session.get(ProjectSound, flow.assembly_id).document
            assert document["dialogue"][0]["media_id"] is None
            assert document["audio_clips"] == []
            assert document["subtitles"][0]["text"] == "未选字幕正文"
        else:
            row = session.get(
                CharacterVoice, (int(flow.reference["project_id"]), int(flow.reference["id"]))
            )
            assert row.record_id is None and row.media_id is None and row.row_version == 2


@pytest.mark.parametrize("kind", ["image", "video", "canvas", "sound", "voice"])
def test_batch_undo_media_rejects_a_stale_preview_after_later_native_edits(
    task_workspace, tmp_path, kind
):
    from copy import deepcopy

    from short_drama.domain import Asset, ShotScript
    from short_drama.domain.canvas import CanvasNodeUserState
    from short_drama.domain.native_voice import CharacterVoice
    from short_drama.domain.project_sound import ProjectSound

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory() as session:
        preview = task_service(session, flow.settings).undo_preview(flow.operation_id)
        assert preview.items and all(item.allowed for item in preview.items)
    with flow.factory.begin() as session:
        if kind in {"image", "video"}:
            row = session.get(Asset if kind == "image" else ShotScript, int(flow.reference["id"]))
        elif kind == "voice":
            row = session.get(
                CharacterVoice, (int(flow.reference["project_id"]), int(flow.reference["id"]))
            )
        elif kind == "sound":
            row = session.get(ProjectSound, flow.assembly_id)
            document = deepcopy(row.document)
            document["subtitles"][0]["text"] = "后续人工字幕保留"
            row.document = document
        else:
            row = session.scalar(
                select(CanvasNodeUserState).where(
                    CanvasNodeUserState.project_id == int(flow.reference["project_id"]),
                    CanvasNodeUserState.node_key == flow.reference["id"],
                )
            )
        row.row_version += 1
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        with pytest.raises(WorkflowError) as stale:
            service.undo_batch(
                flow.operation_id,
                {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]},
                "stale-media-undo",
            )
        assert stale.value.code == "assistant_undo_stale"
        current = service.undo_preview(flow.operation_id)
        assert not any(item.allowed for item in current.items) and current.receipts == []


@pytest.mark.parametrize("kind", ["asset", "shot"])
def test_batch_undo_media_primary_restores_historical_selection_with_new_versions(
    task_workspace, kind
):
    from short_drama.service.assistant_image_service import image_adoption_state, image_target

    reference, groups = image_group_workspace(task_workspace, kind=kind)
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        _project, row = image_target(
            session, 1, reference["project_id"], kind, reference["id"], lock=True
        )
        before = image_adoption_state(session, row, kind)
    historical = {
        **reference,
        "kind": "image_group",
        "id": groups[1]["id"],
        "revision": groups[1]["primary_version"],
    }
    _conversation, accepted, _claim, operations = image_primary_run(
        task_workspace, "把已选图片组的第三张设为主图", [historical]
    )
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-primary-undo")
        preview = service.undo_preview(operations[0].id)
        assert len(preview.items) == 1 and preview.items[0].allowed, preview
        service.undo_batch(
            operations[0].id,
            {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]},
            "primary-undo",
        )
    with factory.begin() as session:
        task_service(session, settings)
        _project, row = image_target(
            session, 1, reference["project_id"], kind, reference["id"], lock=True
        )
        after = image_adoption_state(session, row, kind)
        binding = "media_id" if kind == "asset" else "image"
        assert after[binding] == before[binding]
        assert int(after["row_version"]) > int(before["row_version"])
        for identifier, group in before["image_groups"].items():
            current = after["image_groups"][identifier]
            assert current["is_current"] == group["is_current"]
            assert current["primary_index"] == group["primary_index"]
            assert current["items"] == group["items"]
            assert int(current["primary_version"]) > int(group["primary_version"])


def test_batch_undo_media_sound_allows_two_explicit_subsets_and_keeps_other_audio(
    task_workspace, tmp_path
):
    from short_drama.domain.project_sound import ProjectSound

    flow = settled_media_undo_flow(task_workspace, tmp_path, "sound", lines=2)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        for index in range(2):
            preview = service.undo_preview(flow.operation_id)
            safe = [item for item in preview.items if item.allowed]
            assert len(safe) == 2 - index, preview
            service.undo_batch(
                flow.operation_id,
                {"preview_hash": preview.preview_hash, "selected_keys": [safe[0].key]},
                f"sound-media-subset-{index}",
            )
    with flow.factory() as session:
        task_service(session, flow.settings)
        sound = session.get(ProjectSound, flow.assembly_id)
        assert all(line["media_id"] is None for line in sound.document["dialogue"])
        assert sound.document["audio_clips"] == [] and sound.row_version == 5


@pytest.mark.parametrize("kind", ["image", "sound"])
def test_batch_undo_media_rejects_old_receipts_without_native_binding_snapshots(
    task_workspace, tmp_path, kind
):
    from copy import deepcopy

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory.begin() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        changes = deepcopy(operation.changes)
        for change in changes:
            for state in (change["before"], change["after"]):
                state.pop("image_groups", None)
                state.pop("audio_clips", None)
        operation.changes = changes
    with flow.factory() as session:
        preview = task_service(session, flow.settings).undo_preview(flow.operation_id)
        assert preview.items and all(not item.allowed for item in preview.items)
        assert all("旧" in item.reason and "回执" in item.reason for item in preview.items)


def test_batch_undo_media_restores_previous_voice_and_keeps_the_binding_version(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain.native_voice import CharacterVoice
    from short_drama.service.native_voice_service import NativeVoiceService

    first = settled_media_undo_flow(task_workspace, tmp_path, "voice")
    with first.factory() as session:
        original = session.get(
            CharacterVoice, (int(first.reference["project_id"]), int(first.reference["id"]))
        )
        original_media, original_record = original.media_id, original.record_id
    second = settled_media_undo_flow(
        task_workspace,
        tmp_path,
        "voice",
        reference=first.reference,
        identity_key="voice-undo-replacement",
        item_patch={"voice_version": "1"},
    )
    assert collect_media_results(second.factory, second.settings) == 0
    with second.factory() as session:
        service = task_service(session, second.settings)
        preview = service.undo_preview(second.operation_id)
        assert preview.items[0].allowed
        service.undo_batch(
            second.operation_id,
            {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]},
            "restore-old-voice",
        )
    with second.factory.begin() as session:
        task_service(session, second.settings)
        binding = session.get(
            CharacterVoice, (int(first.reference["project_id"]), int(first.reference["id"]))
        )
        assert (binding.media_id, binding.record_id, binding.row_version) == (
            original_media,
            original_record,
            3,
        )
        native = NativeVoiceService(session, second.settings)
        current = native._voices(first.reference["project_id"], first.reference["id"])
        next_record = next(
            item["record_id"]
            for item in current["candidates"]
            if item["record_id"] != str(original_record)
        )
        with pytest.raises(Conflict, match="音色已变化"):
            native.adopt_locked(
                first.reference["project_id"],
                first.reference["id"],
                {"record_id": next_record, "row_version": 1},
            )
        assert (
            native.adopt_locked(
                first.reference["project_id"],
                first.reference["id"],
                {"record_id": next_record, "row_version": 3},
            )["row_version"]
            == 4
        )


def test_batch_undo_media_rollback_keeps_binding_and_receipt_atomic(
    task_workspace, tmp_path, monkeypatch
):
    from short_drama.dao.agent_undo_dao import AgentUndoDAO
    from short_drama.domain import Asset
    from short_drama.domain.agent_undo import AgentBatchUndo

    flow = settled_media_undo_flow(task_workspace, tmp_path, "image")
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        preview = service.undo_preview(flow.operation_id)
        body = {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]}
        with monkeypatch.context() as context:

            def fail_receipt(*args, **kwargs):
                raise RuntimeError("media undo receipt failure")

            context.setattr(AgentUndoDAO, "add", fail_receipt)
            with pytest.raises(RuntimeError, match="media undo receipt failure"):
                service.undo_batch(flow.operation_id, body, "atomic-media-undo")
        row = session.get(Asset, int(flow.reference["id"]))
        assert row.media_id is not None and row.row_version == 2
        assert session.scalar(select(AgentBatchUndo.id)) is None
        session.rollback()
        assert service.undo_preview(flow.operation_id).preview_hash == preview.preview_hash
        assert (
            service.undo_batch(flow.operation_id, body, "atomic-media-undo").selected_keys
            == body["selected_keys"]
        )


def test_batch_undo_canvas_creation_requires_edges_and_preserves_native_private_root(
    task_workspace,
):
    from short_drama.service.canvas_service import CanvasService

    pid, canvas_id, saved = canvas_patch_workspace(task_workspace)
    _conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        pid,
        saved["source_key"],
        "创建两个文本节点并连接：从雨夜到清晨",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": key,
                        "values": {"type": "text", "content": key},
                    }
                    for key in ("rain", "morning")
                ]
                + [{"action": "connect_nodes", "source": "$rain", "target": "$morning"}],
            }
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded", operation.error
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-canvas-create")
        preview = service.undo_preview(operation.id)
        assert len(preview.items) == 3 and all(item.allowed for item in preview.items)
        node = next(item for item in preview.items if item.kind == "canvas_node" and item.requires)
        with pytest.raises(WorkflowError) as dependent:
            service.undo_batch(
                operation.id,
                {
                    "preview_hash": preview.preview_hash,
                    "selected_keys": [node.key],
                },
                "missing-undo-edge",
            )
        assert dependent.value.code == "assistant_undo_dependencies"
        receipt = service.undo_batch(
            operation.id,
            {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            },
            "undo-native-canvas",
        )
        assert len(receipt.selected_keys) == 3
    with factory() as session:
        task_service(session, settings)
        current = CanvasService(session).read(pid, canvas_id, private=True)
        assert {
            key: value
            for key, value in current["source_document"].items()
            if key not in {"revision", "updatedAt"}
        } == {
            key: value
            for key, value in saved["source_document"].items()
            if key not in {"revision", "updatedAt"}
        }
        assert int(current["row_version"]) > int(saved["row_version"])


def test_batch_undo_canvas_delete_restores_edge_order_and_private_node(task_workspace):
    from short_drama.service.canvas_service import CanvasService

    pid, canvas_id, saved = canvas_patch_workspace(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        document = saved["source_document"]
        document["connections"] = [
            {"id": "old-edge", "fromNodeId": "original-a", "toNodeId": "original-b"}
        ]
        native = CanvasService(session)
        native.commit(
            pid,
            canvas_id,
            {"expected_row_version": "2", "source_document": document},
            "seed-undo-edge",
        )
        saved = native.read(pid, canvas_id, private=True)
    refs = [
        {
            "project_id": str(pid),
            "source_kind": "canvas",
            "source_id": saved["source_key"],
            "kind": "node",
            "id": key,
            "revision": "1",
            "content_revision": "1",
        }
        for key in ("original-a", "original-b")
    ]
    _conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        pid,
        saved["source_key"],
        "删除已选节点",
        [
            {
                "expected_revision": "3",
                "actions": [
                    {
                        "action": "delete_node",
                        "node": "original-a",
                        "row_version": "1",
                        "private_revision": "1",
                    },
                ],
            }
        ],
        references=refs,
    )
    operation = operations[0]
    assert operation.status == "succeeded", operation.error
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-canvas-delete")
        preview = service.undo_preview(operation.id)
        assert len(preview.items) == 3 and all(item.allowed for item in preview.items)
        service.undo_batch(
            operation.id,
            {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            },
            "restore-native-node",
        )
    with factory() as session:
        task_service(session, settings)
        document = CanvasService(session).read(pid, canvas_id, private=True)["source_document"]
        assert {
            key: value for key, value in document.items() if key not in {"revision", "updatedAt"}
        } == {
            key: value
            for key, value in saved["source_document"].items()
            if key not in {"revision", "updatedAt"}
        }


def test_batch_undo_canvas_keeps_later_private_edits_and_allows_an_independent_subset(
    task_workspace,
):
    from short_drama.service.canvas_service import CanvasService

    pid, canvas_id, saved = canvas_patch_workspace(task_workspace)
    _conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        pid,
        saved["source_key"],
        "创建两个图片节点：雨夜",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": key,
                        "values": {"type": "image", "prompt": key},
                    }
                    for key in ("edited", "safe")
                ],
            }
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded", operation.error
    ids = operation.result["temporary_ids"]
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-private-undo")
        native = CanvasService(session)
        document = native.read(pid, canvas_id, private=True)["source_document"]
        next(node for node in document["nodes"] if node["id"] == ids["edited"])["metadata"][
            "prompt"
        ] = "后来人工输入"
        native.commit(
            pid,
            canvas_id,
            {"expected_row_version": "3", "source_document": document},
            "later-private-input",
        )
        preview = service.undo_preview(operation.id)
        assert not next(item for item in preview.items if item.id == ids["edited"]).allowed
        safe = next(item for item in preview.items if item.id == ids["safe"])
        assert safe.allowed
        service.undo_batch(
            operation.id,
            {"preview_hash": preview.preview_hash, "selected_keys": [safe.key]},
            "undo-independent-node",
        )
    with factory() as session:
        task_service(session, settings)
        nodes = CanvasService(session).read(pid, canvas_id, private=True)["source_document"][
            "nodes"
        ]
        assert ids["safe"] not in {node["id"] for node in nodes}
        assert (
            next(node for node in nodes if node["id"] == ids["edited"])["metadata"]["prompt"]
            == "后来人工输入"
        )


@pytest.mark.parametrize("kind", ["canvas", "sound"])
def test_batch_undo_canvas_and_sound_partial_receipts_allow_a_second_explicit_subset(
    task_workspace, kind
):
    if kind == "canvas":
        pid, _canvas_id, saved = canvas_patch_workspace(task_workspace)
        _conversation, accepted, _claim, operations = canvas_tool_run(
            task_workspace,
            pid,
            saved["source_key"],
            "创建两个文本节点：两个片段",
            [
                {
                    "expected_revision": "2",
                    "actions": [
                        {
                            "action": "create_node",
                            "key": key,
                            "values": {"type": "text", "content": key},
                        }
                        for key in ("first", "second")
                    ],
                }
            ],
        )
    else:
        refs, _model, _assembly = dialogue_audio_workspace(task_workspace)
        pid = refs[0]["project_id"]
        _conversation, accepted, _claim, operations = standard_tool_run(
            task_workspace,
            "创建两条台词：两个片段",
            [
                {
                    "sound_revision": sound_text_revision(task_workspace, pid),
                    "actions": [
                        {"action": "create_sound_line", "key": key, "values": {"text": key}}
                        for key in ("first", "second")
                    ],
                }
            ],
            project_id=pid,
        )
    operation = operations[0]
    assert operation.status == "succeeded", operation.error
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-partial-pairs")
        initial = service.undo_preview(operation.id)
        assert len(initial.items) == 2 and all(item.allowed for item in initial.items)
        first = initial.items[0]
        service.undo_batch(
            operation.id,
            {"preview_hash": initial.preview_hash, "selected_keys": [first.key]},
            "explicit-first-subset",
        )
        current = service.undo_preview(operation.id)
        remaining = next(item for item in current.items if item.key != first.key)
        assert (
            remaining.allowed
            and not next(item for item in current.items if item.key == first.key).allowed
        )
        service.undo_batch(
            operation.id,
            {"preview_hash": current.preview_hash, "selected_keys": [remaining.key]},
            "explicit-second-subset",
        )
        completed = service.undo_preview(operation.id)
        assert len(completed.receipts) == 2 and not any(item.allowed for item in completed.items)


def test_batch_undo_canvas_rolls_back_native_writes_and_receipt_after_a_late_failure(
    task_workspace, monkeypatch
):
    from short_drama.domain.agent_undo import AgentBatchUndo
    from short_drama.service.canvas_service import CanvasService

    pid, canvas_id, saved = canvas_patch_workspace(task_workspace)
    _conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        pid,
        saved["source_key"],
        "创建一个文本节点：片段",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": "piece",
                        "values": {"type": "text", "content": "片段正文"},
                    }
                ],
            }
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-atomic-undo")
        before = CanvasService(session).read(pid, canvas_id, private=True)
        session.rollback()
        preview = service.undo_preview(operation.id)
    original = CanvasService._apply_document

    def fail_after_save(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise WorkflowError("fixture_late_undo_failure", "测试：原生写入后失败", 500)

    with monkeypatch.context() as patch:
        patch.setattr(CanvasService, "_apply_document", fail_after_save)
        with factory() as session:
            service = task_service(session, settings)
            with pytest.raises(WorkflowError) as failed:
                service.undo_batch(
                    operation.id,
                    {
                        "preview_hash": preview.preview_hash,
                        "selected_keys": [item.key for item in preview.items],
                    },
                    "late-undo-failure",
                )
            assert failed.value.code == "fixture_late_undo_failure"
    with factory() as session:
        service = task_service(session, settings)
        assert session.scalar(select(AgentBatchUndo.id)) is None
        session.rollback()
        assert CanvasService(session).read(pid, canvas_id, private=True) == before
        session.rollback()
        assert all(item.allowed for item in service.undo_preview(operation.id).items)


def test_batch_undo_canvas_protects_a_later_graph_reference_and_stale_preview(task_workspace):
    from short_drama.service.canvas_service import CanvasService

    pid, canvas_id, saved = canvas_patch_workspace(task_workspace)
    _conversation, accepted, _claim, operations = canvas_tool_run(
        task_workspace,
        pid,
        saved["source_key"],
        "创建一个文本节点：片段",
        [
            {
                "expected_revision": "2",
                "actions": [
                    {
                        "action": "create_node",
                        "key": "piece",
                        "values": {"type": "text", "content": "片段正文"},
                    }
                ],
            }
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-later-graph")
        original = service.undo_preview(operation.id)
        native = CanvasService(session)
        document = native.read(pid, canvas_id, private=True)["source_document"]
        document["connections"].append(
            {
                "id": "later-reference",
                "fromNodeId": operation.result["temporary_ids"]["piece"],
                "toNodeId": "original-b",
            }
        )
        native.commit(
            pid,
            canvas_id,
            {"expected_row_version": "3", "source_document": document},
            "later-native-reference",
        )
        assert not any(item.allowed for item in service.undo_preview(operation.id).items)
        with pytest.raises(WorkflowError) as stale:
            service.undo_batch(
                operation.id,
                {
                    "preview_hash": original.preview_hash,
                    "selected_keys": [item.key for item in original.items],
                },
                "stale-native-undo",
            )
        assert stale.value.code == "assistant_undo_stale"
    with factory() as session:
        task_service(session, settings)
        assert (
            CanvasService(session).read(pid, canvas_id, private=True)["source_document"][
                "connections"
            ]
            == document["connections"]
        )


def test_batch_undo_sound_keeps_a_later_native_edit(task_workspace):
    from copy import deepcopy

    from short_drama.domain.project_sound import ProjectSound
    from short_drama.service.project_sound_service import ProjectSoundService

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    pid = refs[0]["project_id"]
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "修改已选台词的正文：助手台词",
        [
            {
                "sound_revision": sound_text_revision(task_workspace, pid),
                "actions": [
                    {"action": "update_sound_line", "line": "line_0", "patch": {"text": "助手台词"}}
                ],
            }
        ],
        references=[refs[0]],
        project_id=pid,
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    factory, settings, _model = task_workspace
    current = sound_text_revision(task_workspace, pid)
    with factory() as session:
        document = deepcopy(session.get(ProjectSound, assembly_id).document)
    document["dialogue"][0]["text"] = "后续人工编辑"
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-later-sound")
        ProjectSoundService(session, settings).save(
            pid,
            {
                "row_version": current["row_version"],
                "timeline_hash": current["timeline_hash"],
                "request_id": "later-human-sound",
                "document": document,
            },
        )
        preview = service.undo_preview(operation.id)
        assert len(preview.items) == 1 and not preview.items[0].allowed
    with factory() as session:
        assert session.get(ProjectSound, assembly_id).document == document


@pytest.mark.parametrize("action", ["create_sound_line", "update_sound_line"])
def test_batch_undo_sound_restores_only_selected_lines_and_preserves_multitrack(
    task_workspace, action
):
    from copy import deepcopy

    from short_drama.domain.project_sound import ProjectSound

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    pid = refs[0]["project_id"]
    factory, settings, _text = task_workspace
    with factory() as session:
        original = deepcopy(session.get(ProjectSound, assembly_id).document)
    change = (
        {"action": action, "key": "new", "values": {"text": "新台词"}}
        if action == "create_sound_line"
        else {"action": action, "line": "line_0", "patch": {"text": "助手改写台词"}}
    )
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一条台词：新台词"
        if action == "create_sound_line"
        else "修改已选台词的正文：助手改写台词",
        [{"sound_revision": sound_text_revision(task_workspace, pid), "actions": [change]}],
        project_id=pid,
        references=[] if action == "create_sound_line" else [refs[0]],
    )
    operation = operations[0]
    assert operation.status == "succeeded", operation.error
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-sound-undo")
        preview = service.undo_preview(operation.id)
        assert len(preview.items) == 1 and preview.items[0].allowed
        service.undo_batch(
            operation.id,
            {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]},
            "undo-sound-line",
        )
    with factory() as session:
        sound = session.get(ProjectSound, assembly_id)
        assert sound.document == original and sound.row_version == 3


def test_batch_undo_requires_a_complete_boundary_and_explicit_dependency_selection(task_workspace):
    from short_drama.domain import Asset, ShotAsset, ShotScript
    from short_drama.domain.agent_undo import AgentBatchUndo

    conversation, accepted, operation = undo_test_batch(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        assert not any(item.allowed for item in service.undo_preview(operation.id).items)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-before-undo")
        preview = service.undo_preview(operation.id)
        assert len(preview.items) == 3 and all(item.allowed for item in preview.items)
        dependent = next(item for item in preview.items if item.requires)
        with pytest.raises(WorkflowError) as missing:
            service.undo_batch(
                operation.id,
                {"preview_hash": preview.preview_hash, "selected_keys": [dependent.key]},
                "incomplete-selection",
            )
        assert missing.value.code == "assistant_undo_dependencies"
        body = {
            "preview_hash": preview.preview_hash,
            "selected_keys": [item.key for item in preview.items],
        }
        result = service.undo_batch(operation.id, body, "explicit-undo")
        assert service.undo_batch(operation.id, body, "explicit-undo").id == result.id
        snapshot = service.task_runtime_state(conversation.id)
        assert snapshot.operations[0].undo_receipts[0]["id"] == str(result.id)
        assert service.operation_page(accepted.task.id).items[0].undo_receipts == (
            snapshot.operations[0].undo_receipts
        )
        assert (
            service.operation_detail(operation.id).undo_receipts
            == snapshot.operations[0].undo_receipts
        )
        assert not any(item.allowed for item in service.undo_preview(operation.id).items)
        with pytest.raises(Conflict):
            service.undo_batch(
                operation.id, {**body, "selected_keys": [dependent.key]}, "explicit-undo"
            )
    with factory() as session:
        assert session.get(AgentOperation, operation.id).changes == operation.changes
        for item in operation.changes:
            row = session.get(Asset if item["kind"] == "asset" else ShotScript, int(item["id"]))
            assert row.deleted_at is not None and row.row_version > int(
                item["after"]["row_version"]
            )
        assert (
            session.scalar(
                select(ShotAsset.id).where(
                    ShotAsset.shot_id == int(operation.result["temporary_ids"]["opening"])
                )
            )
            is None
        )
        assert session.get(AgentBatchUndo, result.id).selected_keys == sorted(body["selected_keys"])
    with factory() as session:
        service = task_service(session, settings, owner=2)
        with pytest.raises(NotFound):
            service.undo_preview(operation.id)
    with pytest.raises(WorkflowError) as immutable, factory.begin() as session:
        task_service(session, settings)
        session.get(AgentBatchUndo, result.id).changes = []
    assert immutable.value.code == "agent_record_immutable"


def test_batch_undo_keeps_later_edits_and_only_reverses_explicit_safe_items(task_workspace):
    from short_drama.domain import Asset, ShotScript
    from short_drama.service.asset_service import AssetService

    _conversation, accepted, operation = undo_test_batch(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-before-partial")
        original = service.undo_preview(operation.id)
        hero = int(operation.result["temporary_ids"]["hero"])
        AssetService(session).update(hero, {"row_version": "1", "prompt": "后续人工编辑"})
        with pytest.raises(WorkflowError) as stale:
            service.undo_batch(
                operation.id,
                {
                    "preview_hash": original.preview_hash,
                    "selected_keys": [item.key for item in original.items],
                },
                "stale-preview",
            )
        assert stale.value.code == "assistant_undo_stale"
        current = service.undo_preview(operation.id)
        assert next(item for item in current.items if item.id == str(hero)).allowed is False
        safe = [item.key for item in current.items if item.allowed]
        assert len(safe) == 2
        result = service.undo_batch(
            operation.id,
            {"preview_hash": current.preview_hash, "selected_keys": safe},
            "explicit-safe-subset",
        )
        assert len(result.selected_keys) == 2
    with factory() as session:
        task_service(session, settings)
        retained = session.get(Asset, hero)
        assert retained.prompt == "后续人工编辑" and retained.deleted_at is None
        assert (
            session.get(ShotScript, int(operation.result["temporary_ids"]["opening"])).deleted_at
            is not None
        )


@pytest.mark.parametrize("kind", ["asset", "shot", "canvas", "sound"])
@pytest.mark.parametrize("guard", ["none", "later_version", "legacy_receipt"])
def test_batch_undo_reverse_batches_uses_proven_restore_chain(
    task_workspace, kind, guard, monkeypatch
):
    from short_drama.domain import Asset, Project, ShotScript
    from short_drama.domain.canvas import CanvasNode
    from short_drama.domain.project_sound import ProjectSound
    from short_drama.service import assistant_undo_service
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.service.canvas_service import CanvasService
    from short_drama.utils.snowflake import next_id

    factory, settings, _model = task_workspace
    if kind == "asset":
        reference = subject_assets(task_workspace)[0]
        _conversation, accepted, _claim, operations = standard_tool_run(
            task_workspace,
            "修改已选素材的提示词：先写雨夜，再改成清晨",
            [
                {
                    "actions": [
                        {
                            "action": "update_asset",
                            "asset": reference["id"],
                            "patch": {"row_version": str(index), "prompt": content},
                        }
                    ]
                }
                for index, content in enumerate(("雨夜", "清晨"), 1)
            ],
            references=[reference],
        )
    elif kind == "shot":
        reference = subject_assets(task_workspace)[0]
        pid = reference["project_id"]
        with factory.begin() as session:
            task_service(session, settings)
            seeded = AssistantStandardService(session, settings).apply_locked(
                pid,
                {
                    "storyboard_version": "1",
                    "actions": [
                        {
                            "action": "create_shot",
                            "key": "opening",
                            "values": {"title": "原分镜", "script": "原脚本"},
                        }
                    ],
                },
                owner_user_id=1,
                operation_id=next_id(),
            )
        shot_id = seeded["temporary_ids"]["opening"]
        _conversation, accepted, _claim, operations = standard_tool_run(
            task_workspace,
            "修改已选分镜的正文：先写雨夜，再改成清晨",
            [
                {
                    "storyboard_version": str(index + 1),
                    "actions": [
                        {
                            "action": "update_shot",
                            "shot": shot_id,
                            "patch": {"row_version": str(index), "script": content},
                        }
                    ],
                }
                for index, content in enumerate(("雨夜", "清晨"), 1)
            ],
            references=[{**reference, "kind": "shot", "id": shot_id}],
        )
    elif kind == "canvas":
        pid, canvas_id, saved = canvas_patch_workspace(task_workspace)
        _conversation, accepted, _claim, operations = canvas_tool_run(
            task_workspace,
            pid,
            saved["source_key"],
            "修改已选节点的正文：先写雨夜，再改成清晨",
            [
                {
                    "expected_revision": str(index + 1),
                    "actions": [
                        {
                            "action": "update_node",
                            "node": "original-a",
                            "row_version": str(index),
                            "private_revision": "1",
                            "patch": {"content": content},
                        }
                    ],
                }
                for index, content in enumerate(("雨夜", "清晨"), 1)
            ],
            references=[
                {
                    "project_id": str(pid),
                    "source_kind": "canvas",
                    "source_id": saved["source_key"],
                    "kind": "node",
                    "id": "original-a",
                    "revision": "1",
                    "content_revision": "1",
                }
            ],
        )
    else:
        references, _voice_model, assembly_id = dialogue_audio_workspace(task_workspace)
        pid = references[0]["project_id"]
        revision = sound_text_revision(task_workspace, pid)
        _conversation, accepted, _claim, operations = standard_tool_run(
            task_workspace,
            "修改已选台词的正文：先写雨夜，再改成清晨",
            [
                {
                    "sound_revision": {
                        **revision,
                        "row_version": str(index),
                        "assembly_version": str(index),
                    },
                    "actions": [
                        {
                            "action": "update_sound_line",
                            "line": "line_0",
                            "patch": {"text": content},
                        }
                    ],
                }
                for index, content in enumerate(("雨夜", "清晨"), 1)
            ],
            references=[references[0]],
            project_id=pid,
        )
    assert len(operations) == 2, [(row.status, row.error) for row in operations]
    assert all(row.status == "succeeded" for row in operations), [row.error for row in operations]
    first, second = operations
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-reverse-chain")
        blocked = service.undo_preview(first.id)
        assert not any(item.allowed for item in blocked.items)
        latest = service.undo_preview(second.id)
        assert latest.items and all(item.allowed for item in latest.items)
        if guard == "legacy_receipt":
            record = assistant_undo_service.record_restorations

            def without_proofs(*args):
                record(*args)
                for inverse in args[-1]:
                    inverse.pop("restored_from", None)

            monkeypatch.setattr(assistant_undo_service, "record_restorations", without_proofs)
        service.undo_batch(
            second.id,
            {"preview_hash": latest.preview_hash, "selected_keys": [i.key for i in latest.items]},
            "reverse-second-batch",
        )
        if guard == "later_version":
            # A same-content save still advances a real optimistic version; no semantic shortcut.
            with factory.begin() as later:
                task_service(later, settings)
                row = (
                    later.get(Asset, int(reference["id"]))
                    if kind == "asset"
                    else later.get(ShotScript, int(shot_id))
                    if kind == "shot"
                    else later.scalar(
                        select(CanvasNode).where(
                            CanvasNode.project_id == pid, CanvasNode.node_key == "original-a"
                        )
                    )
                    if kind == "canvas"
                    else later.get(ProjectSound, assembly_id)
                )
                row.row_version += 1
        earlier = service.undo_preview(first.id)
        if guard != "none":
            assert not any(item.allowed for item in earlier.items)
            with pytest.raises(WorkflowError) as protected:
                service.undo_batch(
                    first.id,
                    {
                        "preview_hash": earlier.preview_hash,
                        "selected_keys": [i.key for i in earlier.items],
                    },
                    "protected-first-batch",
                )
            assert protected.value.code == "assistant_undo_conflict"
            return
        assert all(item.allowed for item in earlier.items), [item.reason for item in earlier.items]
        receipt = service.undo_batch(
            first.id,
            {"preview_hash": earlier.preview_hash, "selected_keys": [i.key for i in earlier.items]},
            "reverse-first-batch",
        )
        assert receipt.operation_id == first.id
    with factory() as session:
        task_service(session, settings)
        original = first.changes[0]["before"]
        if kind == "asset":
            row = session.get(Asset, int(reference["id"]))
            assert row.prompt == original["prompt"] and row.row_version == 5
        elif kind == "shot":
            row = session.get(ShotScript, int(shot_id))
            assert row.script == original["script"] and row.row_version == 5
            assert session.get(Project, int(pid)).storyboard_version == 6
        elif kind == "canvas":
            current = CanvasService(session).read(pid, canvas_id, private=True)
            node = next(n for n in current["source_document"]["nodes"] if n["id"] == "original-a")
            assert node["metadata"]["content"] == original["document"]["metadata"]["content"]
            assert current["row_version"] == "6"
        else:
            row = session.get(ProjectSound, assembly_id)
            assert row.document["dialogue"][0]["text"] == original["text"]
            assert row.row_version == 5


@pytest.mark.parametrize("kind", ["canvas", "sound"])
def test_batch_undo_reverse_batches_waits_for_complete_shared_document_restore(
    task_workspace, kind
):
    factory, settings, _model = task_workspace
    if kind == "canvas":
        pid, _canvas_id, saved = canvas_patch_workspace(task_workspace)
        _conversation, accepted, _claim, operations = canvas_tool_run(
            task_workspace,
            pid,
            saved["source_key"],
            "创建三个文本节点：三个片段",
            [
                {
                    "expected_revision": str(index + 2),
                    "actions": [
                        {
                            "action": "create_node",
                            "key": key,
                            "values": {"type": "text", "content": key},
                        }
                        for key in keys
                    ],
                }
                for index, keys in enumerate((("first",), ("second", "third")))
            ],
        )
    else:
        refs, _voice_model, _assembly_id = dialogue_audio_workspace(task_workspace)
        pid = refs[0]["project_id"]
        revision = sound_text_revision(task_workspace, pid)
        _conversation, accepted, _claim, operations = standard_tool_run(
            task_workspace,
            "创建三条台词：三个片段",
            [
                {
                    "sound_revision": {
                        **revision,
                        "row_version": str(index + 1),
                        "assembly_version": str(index + 1),
                    },
                    "actions": [
                        {"action": "create_sound_line", "key": key, "values": {"text": key}}
                        for key in keys
                    ],
                }
                for index, keys in enumerate((("first",), ("second", "third")))
            ],
            project_id=pid,
        )
    first, second = operations
    assert first.status == second.status == "succeeded", [row.error for row in operations]
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-partial-reverse")
        selected = service.undo_preview(second.id)
        assert len(selected.items) == 2 and all(item.allowed for item in selected.items)
        for index, item in enumerate(selected.items):
            current = service.undo_preview(second.id)
            service.undo_batch(
                second.id,
                {"preview_hash": current.preview_hash, "selected_keys": [item.key]},
                f"partial-reverse-{index}",
            )
            earlier = service.undo_preview(first.id)
            assert all(item.allowed for item in earlier.items) == (index == 1)
        service.undo_batch(
            first.id,
            {"preview_hash": earlier.preview_hash, "selected_keys": [i.key for i in earlier.items]},
            "partial-reverse-earlier",
        )


@pytest.mark.parametrize("action", ["update", "delete"])
def test_batch_undo_restores_existing_asset_with_a_new_version(task_workspace, action):
    from short_drama.domain import Asset

    reference = subject_assets(task_workspace)[0]
    patch = (
        {
            "actions": [
                {
                    "action": "update_asset",
                    "asset": reference["id"],
                    "patch": {"row_version": "1", "prompt": "助手改写"},
                }
            ]
        }
        if action == "update"
        else {"actions": [{"action": "delete_asset", "asset": reference["id"], "row_version": "1"}]}
    )
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "修改已选素材的提示词：助手改写" if action == "update" else "删除已选素材",
        [patch],
        references=[reference],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-before-restore")
        preview = service.undo_preview(operation.id)
        assert preview.items[0].allowed
        service.undo_batch(
            operation.id,
            {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]},
            "restore-existing",
        )
    with factory() as session:
        task_service(session, settings)
        row = session.get(Asset, int(reference["id"]))
        assert row.prompt == operation.changes[0]["before"]["prompt"]
        assert row.deleted_at is None and row.row_version == 3


def test_batch_undo_restores_deleted_middle_shot_order_and_links(task_workspace):
    from short_drama.domain import Project, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.service.project_storyboard_service import ProjectStoryboardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        created = AssistantStandardService(session).apply_locked(
            reference["project_id"],
            {
                "storyboard_version": "1",
                "actions": [
                    {
                        "action": "create_shot",
                        "key": key,
                        "values": {"title": key},
                        "assets": [reference["id"]],
                    }
                    for key in ("first", "middle", "last")
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
            allowed_asset_ids=[reference["id"]],
        )
    ids = created["temporary_ids"]
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "删除已选分镜",
        [
            {
                "storyboard_version": "4",
                "actions": [{"action": "delete_shot", "shot": ids["middle"], "row_version": "1"}],
            }
        ],
        references=[{**reference, "kind": "shot", "id": ids["middle"]}],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-order")
        preview = service.undo_preview(operation.id)
        assert len(preview.items) == 2 and all(item.allowed for item in preview.items)
        service.undo_batch(
            operation.id,
            {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            },
            "restore-order",
        )
    with factory() as session:
        task_service(session, settings)
        storyboard = ProjectStoryboardService(session)
        assert [
            session.get(ShotScript, int(ids[key])).position for key in ("first", "middle", "last")
        ] == [1, 2, 3]
        middle = session.get(ShotScript, int(ids["middle"]))
        assert middle.deleted_at is None and middle.row_version == 3
        assert storyboard.dao.asset_ids(middle.id) == [int(reference["id"])]
        assert session.get(Project, int(reference["project_id"])).storyboard_version == 6


@pytest.mark.parametrize("action", ["edit_dialogue", "delete_shot"])
def test_batch_undo_restores_native_dialogue_without_rewinding_its_version(task_workspace, action):
    from short_drama.domain import ShotDialogue, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    settings.native_video_enabled = settings.audio_production_enabled = True
    with factory.begin() as session:
        task_service(session, settings)
        created = AssistantStandardService(session, settings).apply_locked(
            reference["project_id"], dialogue_batch(), owner_user_id=1, operation_id=next_id()
        )
    hero, shot = (created["temporary_ids"][key] for key in ("hero", "opening"))
    change = {"action": action, "shot": shot, "row_version": "2"}
    if action == "edit_dialogue":
        change.update(
            dialogue_version="1",
            document={"lines": [{"character_id": hero, "text": "别让他上车。"}]},
        )
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "编辑已选分镜的对白：别让他上车。" if action == "edit_dialogue" else "删除已选分镜",
        [{"storyboard_version": "3", "actions": [change]}],
        references=[
            {**reference, "kind": "shot", "id": shot, "revision": "2"},
            {**reference, "id": hero},
        ],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-dialogue-undo")
        preview = service.undo_preview(operation.id)
        assert all(item.allowed for item in preview.items)
        service.undo_batch(
            operation.id,
            {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            },
            "restore-dialogue",
        )
    with factory() as session:
        task_service(session, settings)
        dialogue = session.get(ShotDialogue, int(shot))
        assert dialogue.document["lines"][0]["text"] == "车来了。"
        assert dialogue.row_version == (3 if action == "edit_dialogue" else 1)
        assert session.get(ShotScript, int(shot)).deleted_at is None


def test_batch_undo_deleted_asset_query_cannot_read_other_batches_or_accounts(task_workspace):
    from sqlalchemy import update

    from short_drama.domain import Asset

    references = subject_assets(task_workspace)
    reference, other = references[0], references[1]
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "删除已选素材",
        [{"actions": [{"action": "delete_asset", "asset": reference["id"], "row_version": "1"}]}],
        references=[reference],
    )
    operation = operations[0]
    assert operation.status == "succeeded"
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        row = session.get(Asset, int(other["id"]))
        row.deleted_at, row.row_version = utcnow(), row.row_version + 1
    with factory() as session:
        task_service(session, settings)
        assert session.scalar(select(Asset).where(Asset.id == int(reference["id"]))) is None
        query = select(Asset).execution_options(assistant_undo_operation_id=operation.id)
        assert {row.id for row in session.scalars(query)} == {int(reference["id"])}
        for statement in (
            select(AgentTask),
            update(Asset).values(name="不得更新"),
            select(Asset, AgentTask),
        ):
            with pytest.raises(WorkflowError) as invalid:
                session.execute(
                    statement.execution_options(assistant_undo_operation_id=operation.id)
                )
            assert invalid.value.code == "invalid_undo_query"
    with factory() as session:
        task_service(session, settings, owner=2)
        with pytest.raises(NotFound):
            session.scalars(query).all()
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-hidden-read")
        assert service.undo_preview(operation.id).items[0].allowed


@pytest.mark.parametrize("later", ["shot_reference", "assembly_reference", "media", "native_task"])
def test_batch_undo_protects_later_references_media_and_native_tasks(task_workspace, later):
    from short_drama.domain import Asset, AsyncTask, MediaFile, Project
    from short_drama.domain.project_assembly import ProjectAssembly, ProjectAssemblyClip
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    _conversation, accepted, operation = undo_test_batch(task_workspace)
    factory, settings, _model = task_workspace
    ids, pid = operation.result["temporary_ids"], int(operation.result["project_id"])
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-later-protection")
    with factory.begin() as session:
        task_service(session, settings)
        if later == "shot_reference":
            project = session.get(Project, pid)
            AssistantStandardService(session).apply_locked(
                pid,
                {
                    "storyboard_version": str(project.storyboard_version),
                    "actions": [
                        {
                            "action": "create_shot",
                            "key": "later",
                            "values": {"title": "后续分镜"},
                            "assets": [ids["hero"]],
                        }
                    ],
                },
                owner_user_id=1,
                operation_id=next_id(),
                allowed_asset_ids=[ids["hero"]],
            )
        elif later == "assembly_reference":
            assembly = ProjectAssembly(
                id=next_id(),
                project_id=pid,
                aspect="16:9",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(assembly)
            session.flush()
            session.add(
                ProjectAssemblyClip(
                    id=next_id(), assembly_id=assembly.id, shot_id=int(ids["opening"]), position=1
                )
            )
        elif later == "media":
            media = MediaFile(
                id=next_id(),
                project_id=pid,
                created_by=1,
                format_code="image/png",
                storage_locator="minio://images/later",
                byte_size=100,
                original_name="later.png",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(media)
            session.flush()
            session.get(Asset, int(ids["other"])).media_id = media.id
        else:
            session.add(
                AsyncTask(
                    id=next_id(),
                    project_id=pid,
                    initiated_by=1,
                    service_type="image",
                    status="queued",
                    idempotency_key="later-native-task",
                    request_hash="a" * 64,
                )
            )
    with factory() as session:
        service = task_service(session, settings)
        preview = service.undo_preview(operation.id)
        if later == "native_task":
            assert not any(item.allowed for item in preview.items)
        else:
            target = (
                ids["hero"]
                if later == "shot_reference"
                else ids["opening" if later == "assembly_reference" else "other"]
            )
            protected = next(item for item in preview.items if item.id == target)
            assert not protected.allowed
            with pytest.raises(WorkflowError) as rejected:
                service.undo_batch(
                    operation.id,
                    {
                        "preview_hash": preview.preview_hash,
                        "selected_keys": [protected.key],
                    },
                    "protected-selection",
                )
            assert rejected.value.code == "assistant_undo_conflict"


def test_batch_undo_receipt_survives_resume_and_is_scrubbed_with_deleted_conversation(
    task_workspace,
):
    from short_drama.agent.session_purge import purge_one
    from short_drama.domain.agent_undo import AgentBatchUndo

    conversation, accepted, operation = undo_test_batch(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "stop-resume-undo")
        preview = service.undo_preview(operation.id)
        receipt = service.undo_batch(
            operation.id,
            {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            },
            "resume-after-undo",
        )
        service.control_task(
            accepted.task.id,
            {"kind": "resume", "expected_control_seq": str(stopped.task.control_seq)},
            "resume-after-undo",
        )
    with factory() as session:
        run = session.scalar(
            select(AgentRun)
            .where(AgentRun.task_id == accepted.task.id)
            .order_by(AgentRun.id.desc())
            .limit(1)
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(run.id, run.message_version, prepare_decision)
    assert claim is not None
    assert "operation_undo_receipts" in str(claim.inputs)
    assert str(receipt.id) in str(claim.inputs)
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-after-undo-resume")
        current = service.get_conversation(conversation.id)
        service.delete_global_conversation(
            conversation.id, {"row_version": str(current.row_version)}, "delete-undone-conversation"
        )
    assert purge_one(factory, SimpleNamespace(delete=lambda locator: None))
    with factory() as session:
        row = session.get(AgentBatchUndo, receipt.id)
        assert row.selected_keys == [] and row.changes == [] and row.result_refs == []


def test_standard_tool_real_batch_and_receipt_are_atomic_and_duplicate_calls_do_not_create_more(
    task_workspace,
):
    from short_drama.domain import Asset, ShotScript

    reference = subject_assets(task_workspace)[0]
    patch = {
        "storyboard_version": "1",
        "actions": [
            {
                "action": "create_asset",
                "key": "hero",
                "values": {"kind": "character", "name": "新主角"},
            },
            {
                "action": "create_shot",
                "key": "opening",
                "values": {"title": "车站"},
                "assets": ["$hero"],
            },
        ],
    }
    excess = {
        "actions": [
            {
                "action": "create_asset",
                "key": "extra",
                "values": {"kind": "character", "name": "不应创建"},
            }
        ]
    }
    conversation, accepted, claim, operations = standard_tool_run(
        task_workspace,
        "创建一个角色和一个分镜并关联：雨夜车站",
        [patch, patch, excess],
        references=[reference],
    )
    assert "apply_standard_patch" in [item["name"] for item in claim.inputs["kwargs"]["tools"]]
    assert [item.status for item in operations] == ["succeeded", "succeeded", "failed"]
    assert len(operations[0].changes) == 2 and operations[1].changes == []
    assert operations[1].result["already_applied"] is True
    assert operations[1].result["operation_id"] == str(operations[0].id)
    assert operations[2].error["code"] == "assistant_write_quantity"
    assert "changes" not in operations[0].result and "before" not in str(operations[0].result)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        state = service.task_runtime_state(conversation.id)
        assert len(state.operations) == 3 and state.tasks[0].status == "paused_error"
        detail = service.operation_detail(operations[0].id)
        assert len(session.scalars(select(Asset).where(Asset.name == "新主角")).all()) == 1
        assert session.scalar(select(func.count()).select_from(ShotScript)) == 1
        assert detail.result["result_refs"] == operations[0].result_refs
        assert "第一部的设定" not in str(detail.result)


@pytest.mark.parametrize(
    "violation", ["forged_hash", "other_task_message", "unselected", "field", "association"]
)
def test_standard_tool_independently_rejects_forged_authority_fields_and_scope(
    task_workspace, violation
):
    references = subject_assets(task_workspace)
    patch = {
        "actions": [
            {
                "action": "update_asset",
                "asset": references[0]["id"],
                "patch": {"row_version": "1", "prompt": "获准字段"},
            }
        ]
    }
    authority = {}
    if violation == "forged_hash":
        authority["request_hash"] = "f" * 64
    elif violation == "other_task_message":
        authority["request_message_id"] = "1"
    elif violation == "unselected":
        patch["actions"][0]["asset"] = references[1]["id"]
    elif violation == "field":
        patch["actions"][0]["patch"]["name"] = "越界字段"
    else:
        patch = {
            "storyboard_version": "1",
            "actions": [
                {
                    "action": "create_shot",
                    "key": "shot",
                    "values": {},
                    "assets": [references[0]["id"]],
                }
            ],
        }
    _conversation, _accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一个分镜" if violation == "association" else "修改已选素材的提示词：逆光",
        [patch],
        references=[references[0]],
        authority=authority,
    )
    assert operations[0].status == "failed" and operations[0].changes == []
    assert operations[0].error["code"] in {
        "standard_request_required",
        "assistant_write_scope",
        "assistant_related_scope",
    }


def test_standard_tool_rejects_guessed_new_version_and_preserves_the_users_edit(task_workspace):
    from short_drama.domain import Asset
    from short_drama.service.asset_service import AssetService

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace

    def edit(_conversation, _accepted):
        with factory() as session:
            task_service(session, settings)
            AssetService(session).update(
                reference["id"], {"row_version": "1", "prompt": "本人后来编辑"}
            )

    _conversation, _accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "修改已选素材的提示词：雨夜",
        [
            {
                "actions": [
                    {
                        "action": "update_asset",
                        "asset": reference["id"],
                        "patch": {"row_version": "2", "prompt": "模型猜到新版本"},
                    }
                ]
            }
        ],
        references=[reference],
        before_execute=edit,
    )
    assert operations[0].status == "conflict" and operations[0].changes == []
    with factory() as session:
        task_service(session, settings)
        assert session.get(Asset, int(reference["id"])).prompt == "本人后来编辑"


def test_standard_tool_completes_started_batch_before_stop_and_preserves_business_results(
    task_workspace,
):
    from short_drama.domain import Asset

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    patch = {
        "actions": [
            {
                "action": "create_asset",
                "key": "prop",
                "values": {"kind": "prop", "name": "完成后停止"},
            }
        ]
    }

    def stop(conversation, accepted):
        with factory() as session:
            service = task_service(session, settings)
            service.send_task_message(
                conversation.id, {"content": "不要再创建其他对象"}, "pending-standard-correction"
            )
            assert (
                service.control_task(
                    accepted.task.id, {"kind": "stop"}, "standard-stop"
                ).task.control_state
                == "stop_pending"
            )

    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一个道具",
        [patch, patch],
        references=[reference],
        before_execute=stop,
    )
    assert len(operations) == 1 and operations[0].status == "succeeded"
    with factory() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        assert (
            task.status == "stopped"
            and task.applied_input_revision == 1
            and task.input_revision == 2
        )
        assert session.scalar(select(Asset.id).where(Asset.name == "完成后停止")) is not None


def test_standard_tool_late_business_failure_rolls_back_objects_history_and_receipt_changes(
    task_workspace,
):
    from short_drama.domain import Asset, AssetVersion

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        count = session.scalar(select(func.count()).select_from(AssetVersion))
    _conversation, _accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一个角色",
        [
            {
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {"kind": "character", "name": "回滚角色"},
                    },
                    {
                        "action": "update_asset",
                        "asset": "$hero",
                        "patch": {"row_version": "1", "scene_time": "深夜"},
                    },
                ]
            }
        ],
        references=[reference],
    )
    assert operations[0].status == "failed" and operations[0].error["code"] == "invalid_asset"
    assert operations[0].changes == [] and operations[0].result_refs == []
    with factory() as session:
        task_service(session, settings)
        assert session.scalar(select(Asset.id).where(Asset.name == "回滚角色")) is None
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == count


def test_standard_tool_updates_only_the_granted_field_and_reuses_its_original_effect(
    task_workspace,
):
    from short_drama.domain import Asset

    reference = subject_assets(task_workspace)[0]
    patch = {
        "actions": [
            {
                "action": "update_asset",
                "asset": reference["id"],
                "patch": {"row_version": "1", "prompt": "雨中逆光"},
            }
        ]
    }
    _conversation, _accepted, _claim, operations = standard_tool_run(
        task_workspace, "修改已选素材的提示词：雨中逆光", [patch, patch], references=[reference]
    )
    assert [item.status for item in operations] == ["succeeded", "succeeded"]
    assert operations[1].result["already_applied"] and operations[1].changes == []
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        asset = session.get(Asset, int(reference["id"]))
        assert asset.prompt == "雨中逆光" and asset.row_version == 2
        assert asset.name == "第一部" and asset.description == "第一部的设定"


def test_standard_tool_mechanical_compaction_is_recorded_without_exposing_unselected_content(
    task_workspace,
):
    from short_drama.domain import Project
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        task_service(session, settings)
        project = session.get(Project, int(reference["project_id"]))
        created = AssistantStandardService(session).apply_locked(
            project.id,
            {
                "storyboard_version": "1",
                "actions": [
                    {
                        "action": "create_shot",
                        "key": key,
                        "values": {"title": key, "script": "未选入正文"},
                    }
                    for key in ("first", "middle", "last")
                ],
            },
            owner_user_id=1,
            operation_id=next_id(),
        )
    middle, last = (created["temporary_ids"][key] for key in ("middle", "last"))
    shot_ref = {**reference, "kind": "shot", "id": middle}
    _conversation, _accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "删除已选分镜",
        [
            {
                "storyboard_version": "4",
                "actions": [{"action": "delete_shot", "shot": middle, "row_version": "1"}],
            }
        ],
        references=[shot_ref],
    )
    assert operations[0].status == "succeeded"
    assert {item["id"] for item in operations[0].changes} == {middle, last}
    assert [item["id"] for item in operations[0].result_refs] == [middle]
    assert "未选入正文" not in str(operations[0].result) and last not in str(operations[0].result)

    from short_drama.agent.assistant_tools import execute_locked

    with factory() as session:
        task_service(session, settings)
        with pytest.raises(WorkflowError) as raised:
            execute_locked(
                session,
                session.get(AgentConversation, _conversation.id),
                session.get(AgentTask, _accepted.task.id),
                SimpleNamespace(
                    tool_name="read_task_results",
                    arguments={"operation_id": str(operations[0].id), "ids": [last]},
                ),
            )
        assert raised.value.code == "assistant_result_scope"


def test_standard_results_are_read_on_demand_through_real_receipts_and_paginate(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked

    reference = subject_assets(task_workspace)[0]
    description = "只属于本任务的新素材说明。" * 300
    conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一个角色和一个分镜并关联：新故事",
        [
            {
                "storyboard_version": "1",
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {
                            "kind": "character",
                            "name": "新人物",
                            "description": description,
                        },
                    },
                    {
                        "action": "create_shot",
                        "key": "opening",
                        "values": {"title": "新开场", "script": "本任务的新正文"},
                        "assets": ["$hero"],
                    },
                ],
            }
        ],
        references=[reference],
    )
    factory, settings, _model = task_workspace
    operation = operations[0]
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert claim is not None
    kwargs = claim.inputs["kwargs"]
    assert "read_task_results" in [item["name"] for item in kwargs["tools"]]
    assert description not in kwargs["instructions"]
    directory = json.loads(kwargs["instructions"].split("任务状态与用户原始输入：", 1)[1])
    request = directory["standard_write_requests"][0]
    assert request["create_remaining"] == {"character": 0, "shot": 0}
    assert operation.result_refs[0]["id"] in [item["id"] for item in request["allowed_assets"]]
    assert request["allowed_shots"] == [{"id": operation.result_refs[1]["id"], "row_version": "1"}]
    store.before_send(claim, {"fixture": "read-created-results"})
    store.save_raw(claim, {"fixture_receipt": True})
    arguments = {"operation_id": str(operation.id), "character_limit": 97}
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "read-results",
                        "tool_name": "read_task_results",
                        "args": arguments,
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        task_service(session, settings)
        read = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "read_task_results",
            )
        )
        assert read.status == "succeeded" and read.changes == []
        result = read.result
        assert result["operation_id"] == str(operation.id)
        assert len(result["results"][0]["content"]) == 97
        contents = {}
        while True:
            for item in result["results"]:
                contents[item["id"]] = contents.get(item["id"], "") + item["content"]
                assert item["row_version"] == "1" and item["source_changed"] is False
            if result["next"] is None:
                break
            result = execute_locked(
                session,
                session.get(AgentConversation, conversation.id),
                session.get(AgentTask, accepted.task.id),
                SimpleNamespace(tool_name="read_task_results", arguments=result["next"]),
            )
        asset, shot = (json.loads(contents[item["id"]]) for item in operation.result_refs)
        assert asset["description"] == description and shot["script"] == "本任务的新正文"
        assert shot["asset_ids"] == [operation.result_refs[0]["id"]]
        assert "current_version_id" not in asset and "before" not in str(read.result)
        version = session.get(AgentRun, accepted.run.id).message_version
    pending = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert pending is not None
    assert "只属于本任务" in str(pending.inputs["kwargs"]["deferred_results"])
    from short_drama.domain import Asset

    with factory.begin() as session:
        task_service(session, settings)
        session.get(Asset, int(operation.result_refs[0]["id"])).deleted_at = utcnow()
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(pending, {"fixture": "deleted-result"})
    assert raised.value.code == "assistant_subjects_superseded"
    store.fail(pending, raised.value)
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    refreshed = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert refreshed is not None
    assert refreshed.inputs["kwargs"]["history"] is None
    assert refreshed.inputs["kwargs"]["deferred_results"] is None
    assert "只属于本任务" not in str(refreshed.inputs["kwargs"])


def test_standard_result_body_does_not_leak_into_an_unselected_new_task(task_workspace):
    from short_drama.agent.assistant_tools import ReadHistory, _read_history

    reference = subject_assets(task_workspace)[0]
    secret = "只由工具生成的本任务角色设定"
    conversation, accepted, _claim, _operations = standard_tool_run(
        task_workspace,
        "创建一个角色：简短要求",
        [
            {
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {
                            "kind": "character",
                            "name": "新人物",
                            "description": secret,
                        },
                    }
                ]
            }
        ],
        references=[reference],
    )
    factory, settings, model = task_workspace
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    store = AgentRuntimeStore(factory, settings)
    final = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert final is not None and final.inputs["result_access_keys"]
    store.before_send(final, {"fixture": "final-created-result"})
    store.save_raw(final, {"fixture_receipt": True})
    store.save_result(
        final,
        {
            "output_kind": "text",
            "output": secret,
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    with factory() as session:
        service = task_service(session, settings)
        next_task = service.send_task_message(
            conversation.id,
            {"content": "讨论另一个故事", "new_task": True, "model_config_id": model},
            "unselected-follow-up",
        )
    following = store.claim_execution(next_task.run.id, 1, prepare_decision)
    assert following is not None and following.inputs["result_access_keys"] == []
    assert secret not in str(following.inputs)
    with factory() as session:
        task_service(session, settings)
        history = _read_history(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, next_task.task.id),
            ReadHistory(),
            6000,
        )
        assert secret not in str(history)


@pytest.mark.parametrize("change", ["edited", "deleted", "archived", "other_owner"])
def test_standard_result_reads_keep_saved_content_and_recheck_live_access(task_workspace, change):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.domain import Asset, Project
    from short_drama.service.asset_service import AssetService

    reference = subject_assets(task_workspace)[0]
    conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一个角色：原始描述",
        [
            {
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {
                            "kind": "character",
                            "name": "新人物",
                            "description": "原始描述",
                        },
                    }
                ]
            }
        ],
        references=[reference],
    )
    factory, settings, _model = task_workspace
    operation = operations[0]
    identifier = int(operation.result_refs[0]["id"])
    with factory.begin() as session:
        if change != "other_owner":
            task_service(session, settings)
        # A fixture-only administrative revocation, outside the former owner's write scope.
        row = session.get(Asset, identifier)
        if change == "edited":
            AssetService(session).apply_patch_locked(
                row, {"description": "后来人工编辑的私密正文"}, 1
            )
        elif change == "deleted":
            row.deleted_at = utcnow()
        else:
            project = session.get(Project, row.project_id)
            if change == "archived":
                project.archived_at = utcnow()
            else:
                session.add(
                    User(
                        id=2,
                        username="new-owner",
                        display_name="New owner",
                        email="new-owner@example.test",
                        password_hash="test-only",
                        status="active",
                        email_verified_at=utcnow(),
                        created_at=utcnow(),
                    )
                )
                session.flush()
                project.owner_user_id = 2
    with factory() as session:
        task_service(session, settings)
        args = {"operation_id": str(operation.id), "ids": [str(identifier)]}
        conversation = session.get(AgentConversation, conversation.id)
        task = session.get(AgentTask, accepted.task.id)
        if change == "edited":
            result = execute_locked(
                session,
                conversation,
                task,
                SimpleNamespace(
                    tool_name="read_task_results",
                    arguments=args,
                ),
            )
            item = result["results"][0]
            assert item["source_changed"] is True and item["current_revision"] == "2"
            assert json.loads(item["content"])["description"] == "原始描述"
            assert "后来人工编辑的私密正文" not in str(result)
        else:
            with pytest.raises(NotFound):
                execute_locked(
                    session,
                    conversation,
                    task,
                    SimpleNamespace(
                        tool_name="read_task_results",
                        arguments=args,
                    ),
                )


def test_standard_result_reads_reject_selected_unrelated_and_other_task_objects(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked

    references = subject_assets(task_workspace)
    conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一个角色：新人物",
        [
            {
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {
                            "kind": "character",
                            "name": "新人物",
                        },
                    }
                ]
            }
        ],
        references=[references[0]],
    )
    other_conversation, other_accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        task_service(session, settings)
        task = session.get(AgentTask, accepted.task.id)
        conversation = session.get(AgentConversation, conversation.id)
        for identifier in (references[0]["id"], references[1]["id"]):
            with pytest.raises(WorkflowError) as raised:
                execute_locked(
                    session,
                    conversation,
                    task,
                    SimpleNamespace(
                        tool_name="read_task_results",
                        arguments={"operation_id": str(operations[0].id), "ids": [identifier]},
                    ),
                )
            assert raised.value.code == "assistant_result_scope"
        with pytest.raises(WorkflowError) as raised:
            execute_locked(
                session,
                conversation,
                task,
                SimpleNamespace(
                    tool_name="read_task_results",
                    arguments={
                        "operation_id": str(operations[0].id),
                        "ids": [
                            operations[0].result_refs[0]["id"],
                            operations[0].result_refs[0]["id"],
                        ],
                    },
                ),
            )
        assert raised.value.code == "invalid_tool_arguments"
        with pytest.raises(NotFound):
            execute_locked(
                session,
                session.get(AgentConversation, other_conversation.id),
                session.get(AgentTask, other_accepted.task.id),
                SimpleNamespace(
                    tool_name="read_task_results",
                    arguments={"operation_id": str(operations[0].id)},
                ),
            )


@pytest.mark.parametrize("content", ["只讨论创建一个角色", "请翻译：删除已选素材", "创建全部素材"])
def test_standard_tool_stays_hidden_and_rejects_forged_calls_during_discussion(
    task_workspace, content
):
    reference = subject_assets(task_workspace)[0]
    _conversation, _accepted, claim, operations = standard_tool_run(
        task_workspace,
        content,
        [
            {
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {"kind": "character", "name": "不可创建"},
                    }
                ]
            }
        ],
        references=[reference],
    )
    assert "apply_standard_patch" not in [item["name"] for item in claim.inputs["kwargs"]["tools"]]
    assert (
        operations[0].status == "failed"
        and operations[0].error["code"] == "standard_request_required"
    )


def dialogue_batch():
    return {
        "storyboard_version": "1",
        "actions": [
            {
                "action": "create_asset",
                "key": "hero",
                "values": {"kind": "character", "name": "对白角色"},
            },
            {
                "action": "create_shot",
                "key": "opening",
                "values": {"title": "等车"},
                "assets": ["$hero"],
            },
            {
                "action": "edit_dialogue",
                "shot": "$opening",
                "row_version": "1",
                "dialogue_version": "0",
                "document": {
                    "lines": [
                        {
                            "character_id": "$hero",
                            "text": "车来了。",
                            "delivery": "低声",
                            "speech": "voiceover",
                        }
                    ]
                },
            },
        ],
    }


def test_standard_patch_dialogue_is_a_real_native_document_and_participates_in_atomic_versions(
    task_workspace,
):
    from short_drama.domain import Project, ShotDialogue, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    settings.native_video_enabled = settings.audio_production_enabled = True
    with factory.begin() as session:
        task_service(session, settings)
        service = AssistantStandardService(session, settings)
        result = service.apply_locked(
            reference["project_id"], dialogue_batch(), owner_user_id=1, operation_id=next_id()
        )
        hero, shot = (int(result["temporary_ids"][key]) for key in ("hero", "opening"))
        doc = session.get(ShotDialogue, shot)
        assert doc.row_version == 1 and doc.document["reviewed"] is False
        assert doc.document["lines"] == [
            {
                "character_id": str(hero),
                "text": "车来了。",
                "delivery": "低声",
                "speech": "voiceover",
            }
        ]
        assert session.get(ShotScript, shot).row_version == 2
        assert session.get(Project, int(reference["project_id"])).storyboard_version == 3
        assert result["result_refs"][1]["dialogue_version"] == "1"
        assert result["changes"][1]["after"]["dialogue"]["document"] == doc.document
        action = {
            "action": "edit_dialogue",
            "shot": str(shot),
            "row_version": "2",
            "dialogue_version": "1",
            "document": doc.document,
        }
        no_op = service.apply_locked(
            reference["project_id"],
            {"storyboard_version": "3", "actions": [action]},
            owner_user_id=1,
            operation_id=next_id(),
            allowed_asset_ids=[hero],
            allowed_shot_ids=[shot],
        )
        assert no_op["changes"] == [] and no_op["result_refs"][0]["status"] == "unchanged"
        with pytest.raises(Conflict), session.begin_nested():
            service.apply_locked(
                reference["project_id"],
                {"storyboard_version": "3", "actions": [{**action, "dialogue_version": "0"}]},
                owner_user_id=1,
                operation_id=next_id(),
                allowed_asset_ids=[hero],
                allowed_shot_ids=[shot],
            )
        assert doc.row_version == 1 and session.get(ShotScript, shot).row_version == 2


@pytest.mark.parametrize("disabled", ["native_video_enabled", "audio_production_enabled"])
def test_standard_patch_dialogue_respects_native_feature_admission_and_rolls_back_creation(
    task_workspace, disabled
):
    from short_drama.domain import Asset, Project, ShotScript
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    settings.native_video_enabled = settings.audio_production_enabled = True
    setattr(settings, disabled, False)
    with factory.begin() as session:
        task_service(session, settings)
        with pytest.raises(NotFound), session.begin_nested():
            AssistantStandardService(session, settings).apply_locked(
                reference["project_id"], dialogue_batch(), owner_user_id=1, operation_id=next_id()
            )
        assert session.scalar(select(Asset.id).where(Asset.name == "对白角色")) is None
        assert session.scalar(select(func.count()).select_from(ShotScript)) == 0
        assert session.get(Project, int(reference["project_id"])).storyboard_version == 1


def test_standard_tool_edits_native_dialogue_with_real_character_scope_and_exposes_saved_version(
    task_workspace,
):
    from short_drama.domain import ShotDialogue
    from short_drama.service.assistant_standard_service import AssistantStandardService
    from short_drama.utils.snowflake import next_id

    reference = subject_assets(task_workspace)[0]
    factory, settings, _model = task_workspace
    settings.native_video_enabled = settings.audio_production_enabled = True
    with factory.begin() as session:
        task_service(session, settings)
        result = AssistantStandardService(session, settings).apply_locked(
            reference["project_id"], dialogue_batch(), owner_user_id=1, operation_id=next_id()
        )
    hero, shot = (result["temporary_ids"][key] for key in ("hero", "opening"))
    refs = [{**reference, "id": shot, "kind": "shot", "revision": "2"}, {**reference, "id": hero}]
    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "编辑已选分镜的对白：别让他上车。",
        [
            {
                "storyboard_version": "3",
                "actions": [
                    {
                        "action": "edit_dialogue",
                        "shot": shot,
                        "row_version": "2",
                        "dialogue_version": "1",
                        "document": {"lines": [{"character_id": hero, "text": "别让他上车。"}]},
                    }
                ],
            }
        ],
        references=refs,
    )
    assert (
        operations[0].status == "succeeded"
        and operations[0].result_refs[0]["dialogue_version"] == "2"
    )
    assert "车来了。" not in str(operations[0].result) and "别让他上车。" not in str(
        operations[0].result
    )
    with factory() as session:
        task_service(session, settings)
        assert session.get(ShotDialogue, int(shot)).document["lines"][0]["text"] == "别让他上车。"
        selected = session.get(AgentTask, accepted.task.id).context_snapshot["subjects"]
        assert (
            next(item for item in selected if item["id"] == shot)["fields"]["dialogue"]["document"][
                "lines"
            ][0]["text"]
            == "车来了。"
        )


def media_tool_run(
    workspace,
    *,
    count=1,
    limit=None,
    content=None,
    before_execute=None,
    item_patch=None,
    references=None,
    targets=None,
    execution_target=None,
    media_kind="image",
    model_patch=None,
    identity_key="media",
    existing_model=None,
):
    import base64

    from short_drama.agent.authorization import digest
    from short_drama.core.config import Settings

    factory, settings, model = workspace
    runtime = Settings(_env_file=None, encryption_key=base64.b64encode(b"0" * 32).decode())
    for key in (
        "generation_lease_seconds",
        "generation_poll_seconds",
        "generation_archive_budget_seconds",
        "minio_image_bucket",
        "minio_video_bucket",
        "minio_audio_bucket",
        "minio_presign_expiry",
        "render_ffmpeg_path",
        "render_ffprobe_path",
        "encryption_key",
    ):
        setattr(settings, key, getattr(runtime, key))
    references = references or [subject_assets(workspace)[0]]
    targets = targets or references
    reference = references[0]
    native_canvas = execution_target and execution_target["mode"] == "infinite_canvas"
    provider = (
        "openai" if media_kind == "audio" or native_canvas and media_kind == "image" else "ark"
    )
    if media_kind == "video" and not native_canvas:
        provider = "modelhub"
    with factory() as session:
        task_service(session, settings)
        image_model = existing_model or AIModelConfigService(session).create(
            {
                "service_type": media_kind,
                "name": "Assistant image fixture",
                "model_key": (
                    {
                        "image": "gpt-image-1",
                        "video": "doubao-seedance-1-0-pro-250528",
                        "audio": "gpt-4o-mini-tts",
                    }[media_kind]
                    if native_canvas
                    else "seedance-2.0-mini"
                    if media_kind == "video"
                    else "fixture"
                ),
                "provider": provider,
                "base_url": (
                    "https://api.openai.com/v1"
                    if provider == "openai"
                    else "https://api.modelhub.cc"
                    if provider == "modelhub"
                    else "https://ark.cn-beijing.volces.com/api/v3"
                ),
                **(model_patch or {}),
            }
        )
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, f"{identity_key}-conversation")
        label = "素材" if reference["kind"] == "asset" else "分镜"
        content = content or f"为已选{label}生成{count}张图片：雨中的人物"
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "model_config_id": model,
                "execution_target": execution_target
                or {"mode": "standard", "project_id": reference["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": references},
                **(
                    {
                        "media_limits": {
                            "audio" if media_kind == "audio" else f"{media_kind}s": limit
                        }
                    }
                    if limit is not None
                    else {}
                ),
            },
            f"{identity_key}-message",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    with factory() as session:
        selected_snapshots = session.get(AgentTask, accepted.task.id).context_snapshot["subjects"]
    body = {
        "request_message_id": str(accepted.message.id),
        "request_hash": digest({"role": "user", "content": content}),
        "requests": [
            {
                "target_kind": target["kind"],
                "target_id": target["id"],
                "row_version": target["revision"],
                "model_config_id": str(image_model.id),
                "model_version": str(image_model.row_version),
                "count": count,
                **(
                    {"prompt": "雨中的人物"}
                    if target["kind"] != "sound_line"
                    else {
                        key: next(
                            item["sound_binding"][key]
                            for item in selected_snapshots
                            if item["kind"] == "sound_line" and item["id"] == target["id"]
                        )
                        for key in ("assembly_version", "timeline_hash")
                    }
                ),
                **(
                    {
                        "media_kind": media_kind,
                        "private_revision": next(
                            item["write_versions"]["private_revision"]
                            for item in selected_snapshots
                            if item["id"] == target["id"]
                        ),
                    }
                    if target["kind"] == "node"
                    else {}
                ),
                **(item_patch or {}),
            }
            for target in targets
        ],
    }
    store.before_send(claim, {"fixture": "media-command"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "media-1",
                        "tool_name": "submit_generation_batch",
                        "args": body,
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    operation_id = claim_operation(factory, settings, accepted.run.id)
    assert operation_id is not None
    if before_execute:
        before_execute(accepted, reference)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    return SimpleNamespace(
        factory=factory,
        settings=settings,
        conversation=conversation,
        accepted=accepted,
        reference=reference,
        references=references,
        operation_id=operation_id,
        claim=claim,
        body=body,
    )


def media_worker(flow, provider=None, *, storage=None):
    from short_drama.service.generation_execution_service import GenerationExecutionService
    from tests.integration.standard.assets.test_asset_image_generation import (
        ImageProvider,
        MemoryStorage,
    )

    class CanvasImageProvider(ImageProvider):
        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            result.adapter = "openai_images.v1"
            return result

    provider = provider or (
        CanvasImageProvider() if flow.reference["kind"] == "node" else ImageProvider()
    )
    return provider, GenerationExecutionService(
        flow.factory, flow.settings, provider, storage or MemoryStorage()
    )


def shot_video_flow(workspace, *, selected_image=True, **kwargs):
    from short_drama.domain import ShotScript

    reference, groups = image_group_workspace(workspace, kind="shot")
    factory, _settings, _model = workspace
    with factory.begin() as session:
        shot = session.get(ShotScript, int(reference["id"]))
        shot.script = "未授权发送的分镜原文"
        shot.video_prompt = "未授权发送的私人视频提示词"
        shot.video_settings = {"resolution": "720p", "duration_ms": 4000}
    image = groups[0]["items"][0]
    references = [reference]
    if selected_image:
        references.append(
            {
                **reference,
                "kind": "image_slot",
                "id": image["id"],
                "revision": image["item_version"],
            }
        )
    item_patch = {
        "target_kind": "shot_video",
        "media_kind": "video",
        "reference_media_ids": [image["media_id"]],
        **kwargs.pop("item_patch", {}),
    }
    flow = media_tool_run(
        workspace,
        media_kind="video",
        content="为已选分镜生成一段视频：雨中的人物",
        references=references,
        targets=[reference],
        item_patch=item_patch,
        **kwargs,
    )
    flow.image = image
    return flow


def shot_video_provider(tmp_path, *, before_validate=None, before_poll=None, fail=False):
    import base64
    from copy import deepcopy

    from short_drama.ai import GenerationResult
    from tests.integration.canvas.generation.test_canvas_generation_media_runtime import (
        playable_fixture,
    )

    content = (
        (tmp_path / "generated.mp4").read_bytes()
        if (tmp_path / "generated.mp4").exists()
        else playable_fixture(tmp_path, "video")
    )

    class Provider:
        def __init__(self):
            self.calls, self.polls = [], 0

        def validate(self, _snapshot, _request, adapter):
            assert adapter == "modelhub_video.v1"
            if before_validate:
                before_validate()
            return {}

        def submit(self, _snapshot, request, *_args, **_kwargs):
            self.calls.append(deepcopy(request))
            return GenerationResult("submitted", "modelhub_video.v1", provider_task_id="shot-video")

        def poll(self, _snapshot, provider_id, *_args, **_kwargs):
            assert provider_id == "shot-video"
            self.polls += 1
            if before_poll:
                before_poll()
            if fail:
                return GenerationResult("failed", "modelhub_video.v1")
            return GenerationResult(
                "succeeded",
                "modelhub_video.v1",
                outputs=[{"base64": base64.b64encode(content).decode()}],
            )

    return Provider()


def test_assistant_shot_video_waits_for_archive_and_automatic_adoption(task_workspace, tmp_path):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, MediaFile, ShotScript, ShotVideo

    flow = shot_video_flow(task_workspace, limit=1)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id,
            {"kind": "stop"},
            "stop-video",
        )
    provider, worker = media_worker(flow, shot_video_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert len(provider.calls) == provider.polls == 1
    request = provider.calls[0]
    assert request["input"]["prompt"] == "雨中的人物"
    assert list(map(str, request["input"]["reference_media_ids"])) == [flow.image["media_id"]]
    assert request["parameters"]["duration_ms"] == 4000
    assert request["parameters"]["resolution"] == "720p"
    assert "未授权发送" not in str(request)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result or operation.error
        assert operation.result_refs[0]["media_kind"] == "video"
        assert operation.result_refs[0]["status"] == "adopted"
        assert operation.changes[0]["kind"] == "shot"
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.status == "stopped"
        assert task.media_usage["videos"] == {"reserved": 0, "submitted": 1, "succeeded": 1}
        shot = session.get(ShotScript, int(flow.reference["id"]))
        assert shot.row_version == int(flow.reference["revision"]) + 1
        video = session.scalar(select(ShotVideo).where(ShotVideo.shot_id == shot.id))
        media = session.get(MediaFile, video.media_id)
        assert media.duration_ms > 0 and video.prompt == "雨中的人物"
        record = session.scalar(select(AIGenerationRecord))
        assert "未授权发送" not in str(record.request_data)


def test_assistant_shot_video_requires_explicit_main_image_reference(task_workspace):
    from short_drama.domain import AsyncTask

    flow = shot_video_flow(task_workspace, selected_image=False)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed"
        assert operation.error["code"] == "assistant_generation_reference"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


@pytest.mark.parametrize("change", ["edit", "delete", "image"])
def test_assistant_shot_video_claim_rechecks_target_and_reference(task_workspace, tmp_path, change):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import ShotImage, ShotScript, ShotVideo

    flow = shot_video_flow(task_workspace)
    with flow.factory.begin() as session:
        shot = session.get(ShotScript, int(flow.reference["id"]))
        if change == "edit":
            shot.video_prompt = "人工修改"
            shot.row_version += 1
        elif change == "delete":
            shot.deleted_at = utcnow()
        else:
            image = session.scalar(select(ShotImage).where(ShotImage.shot_id == shot.id))
            image.layout = "four"
    provider, worker = media_worker(flow, shot_video_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert provider.calls == []
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.result["items"][0]["status"] == "not_submitted"
        assert (
            operation.result["items"][0]["error"]["code"] == "assistant_shot_video_source_changed"
        )
        assert session.scalar(select(ShotVideo)) is None
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["videos"] == {
            "reserved": 0,
            "submitted": 0,
            "succeeded": 0,
        }


@pytest.mark.parametrize("when", ["validate", "poll"])
def test_assistant_shot_video_final_barrier_and_late_edit_preserve_human_work(
    task_workspace, tmp_path, when
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import MediaAsset, ShotScript, ShotVideo

    flow = shot_video_flow(task_workspace)

    def edit():
        with flow.factory.begin() as session:
            shot = session.get(ShotScript, int(flow.reference["id"]))
            shot.video_prompt = "人工新视频提示词"
            shot.row_version += 1

    provider, worker = media_worker(flow, shot_video_provider(tmp_path, **{f"before_{when}": edit}))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert len(provider.calls) == int(when == "poll")
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        intent = operation.result["items"][0]
        assert intent["status"] == ("conflict" if when == "poll" else "not_submitted")
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == int(when == "poll")
        assert session.scalar(select(ShotVideo)) is None
        assert session.get(ShotScript, int(flow.reference["id"])).video_prompt == "人工新视频提示词"


def test_assistant_shot_video_reserves_video_budget_before_native_admission(task_workspace):
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    flow = shot_video_flow(task_workspace, limit=0)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.error["code"] == "assistant_media_budget_exhausted"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0
        assert session.get(AgentTask, flow.accepted.task.id).media_usage == {}


def test_assistant_shot_video_failure_retries_only_after_explicit_resume(task_workspace, tmp_path):
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AsyncTask, ShotVideo

    flow = shot_video_flow(task_workspace, limit=2)
    _provider, worker = media_worker(flow, shot_video_provider(tmp_path, fail=True))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed"
        original = deepcopy(operation.result)
        body = {**deepcopy(flow.body), "retry_operation_id": str(operation.id)}
    retry_id, _claim = resume_media_tool(flow, body)
    retry_flow = SimpleNamespace(**{**vars(flow), "operation_id": retry_id})
    with flow.factory() as session:
        retry = session.get(AgentOperation, retry_id)
        assert retry.status == "waiting_media", retry.result or retry.error
    provider, worker = media_worker(retry_flow, shot_video_provider(tmp_path))
    drain_assistant_media(retry_flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert len(provider.calls) == 1
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).result == original
        assert session.get(AgentOperation, retry_id).status == "succeeded"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 2
        assert session.scalar(select(ShotVideo)) is not None
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["videos"] == {
            "reserved": 0,
            "submitted": 2,
            "succeeded": 1,
        }


def test_assistant_shot_video_unknown_preserves_original_receipt_and_pending_stop(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationError
    from short_drama.domain import AsyncTask

    flow = shot_video_flow(task_workspace)
    provider = shot_video_provider(tmp_path)

    def unknown(*_args, **_kwargs):
        provider.calls.append("unknown")
        raise GenerationError("timeout", accepted_unknown=True)

    provider.submit = unknown
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-unknown-video"
        )
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        task = session.get(AgentTask, flow.accepted.task.id)
        assert operation.status == "reconciling" and operation.finished_at is None
        assert task.control_state == "stop_pending"
        assert task.media_usage["videos"] == {"reserved": 1, "submitted": 0, "succeeded": 0}
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
    assert provider.calls == ["unknown"] and provider.polls == 0


@pytest.mark.parametrize("valid", [True, False])
def test_assistant_shot_video_adoption_keeps_old_history_and_rejects_invalid_tracks(
    task_workspace, tmp_path, valid
):
    import base64

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationResult
    from short_drama.domain import MediaAsset, MediaFile, MediaRecycleBin, ShotVideo
    from short_drama.utils.snowflake import next_id

    factory, settings, _model = task_workspace
    previous_id = next_id()

    def existing_video(_accepted, reference):
        with factory.begin() as session:
            session.add(
                MediaFile(
                    id=previous_id,
                    project_id=int(reference["project_id"]),
                    created_by=1,
                    format_code="video/mp4",
                    storage_locator=f"minio://{settings.minio_video_bucket}/old",
                    byte_size=100,
                    checksum_sha256="b" * 64,
                    duration_ms=1000,
                    width=160,
                    height=90,
                    original_name="old.mp4",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            session.flush()
            session.add(
                ShotVideo(
                    id=next_id(),
                    project_id=int(reference["project_id"]),
                    shot_id=int(reference["id"]),
                    media_id=previous_id,
                    prompt="旧视频提示词",
                    resolution="720p",
                    duration=1000,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )

    flow = shot_video_flow(task_workspace, before_execute=existing_video)
    provider = shot_video_provider(tmp_path)
    if not valid:

        def invalid(*_args, **_kwargs):
            return GenerationResult(
                "succeeded",
                "modelhub_video.v1",
                outputs=[
                    {
                        "base64": base64.b64encode(
                            b"\x00\x00\x00\x20ftypisom" + b"\x00" * 24
                        ).decode(),
                    }
                ],
            )

        provider.poll = invalid
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(flow, worker)
    assert collect_media_results(factory, settings) == 1
    with factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == ("succeeded" if valid else "failed")
        video = session.scalar(select(ShotVideo))
        if valid:
            assert video.media_id != previous_id
            assert session.scalar(select(MediaRecycleBin)).media_id == previous_id
        else:
            assert video.media_id == previous_id
            assert session.scalar(select(MediaRecycleBin)) is None
            assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0


def test_assistant_shot_video_can_use_this_task_generated_main_image(task_workspace, tmp_path):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import ShotScript, ShotVideo

    reference, _groups = image_group_workspace(task_workspace, kind="shot")
    factory, settings, _model = task_workspace
    with factory.begin() as session:
        session.get(ShotScript, int(reference["id"])).video_settings = {
            "resolution": "720p",
            "duration_ms": 4000,
        }
    flow = media_tool_run(task_workspace, references=[reference], targets=[reference])
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(factory, settings) == 1
    with factory() as session:
        original = session.get(AgentOperation, flow.operation_id)
        media_id = original.result["items"][0]["result"]["items"][0]["media_id"]
    with factory() as session:
        task_service(session, settings)
        model = AIModelConfigService(session).create(
            {
                "service_type": "video",
                "name": "Follow-up video",
                "provider": "modelhub",
                "model_key": "seedance-2.0-mini",
                "base_url": "https://api.modelhub.cc",
            }
        )
    body = media_reference_body(flow, content="为已选分镜生成视频：人物走进车站", media_id=media_id)
    item = body["requests"][0]
    body["requests"] = [
        {
            key: item[key]
            for key in ("target_id", "row_version", "prompt", "count", "reference_media_ids")
        }
    ]
    body["requests"][0].update(
        target_kind="shot_video",
        media_kind="video",
        model_config_id=str(model.id),
        model_version=str(model.row_version),
    )
    operation_id, _claim = resume_media_tool(flow, body)
    next_flow = SimpleNamespace(**{**vars(flow), "operation_id": operation_id})
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    provider, worker = media_worker(next_flow, shot_video_provider(tmp_path))
    drain_assistant_media(next_flow, worker)
    assert collect_media_results(factory, settings) == 1
    assert list(map(str, provider.calls[0]["input"]["reference_media_ids"])) == [media_id]
    with factory() as session:
        assert session.get(AgentOperation, operation_id).status == "succeeded"
        assert session.scalar(select(ShotVideo)) is not None


def drain_assistant_media(flow, worker):
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    with flow.factory() as session:
        ids = session.scalars(
            select(AgentMediaRequest.generation_task_id).where(
                AgentMediaRequest.operation_id == flow.operation_id,
                AgentMediaRequest.generation_task_id.is_not(None),
            )
        ).all()
    for identifier in ids:
        for _ in range(6):
            with flow.factory.begin() as session:
                task = session.get(AsyncTask, identifier)
                if task.status in {"succeeded", "failed", "cancelled"}:
                    break
                version = task.message_version
                task.next_run_at = utcnow()
            worker.execute(identifier, version)
            worker.execute(identifier, version)
        else:
            pytest.fail("Assistant native image task did not settle")


def test_assistant_media_waits_through_generation_and_adoption_before_applying_stop(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset, ImageGroup, ImageGroupItem
    from short_drama.domain.agent_media import AgentMediaRequest

    flow = media_tool_run(task_workspace, count=2, limit=2)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media" and operation.finished_at is None
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"]["reserved"] == 2
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 1
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "media-stop"
        )
    complete_operation(flow.factory, flow.settings, flow.accepted.run.id, flow.operation_id)
    execute_tools(flow.factory, flow.settings, flow.accepted.run.id)
    assert collect_media_results(flow.factory, flow.settings) == 0
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert len(provider.calls) == 1
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        task = session.get(AgentTask, flow.accepted.task.id)
        assert operation.status == "succeeded" and operation.result["settled"] is True
        assert task.status == "stopped" and task.applied_control_seq == 1
        assert task.media_usage["images"] == {"reserved": 0, "submitted": 2, "succeeded": 2}
        group = session.scalar(
            select(ImageGroup).where(ImageGroup.asset_id == int(flow.reference["id"]))
        )
        assert group.active_slot == 1 and group.primary_index == 1 and group.primary_mode == "auto"
        primary = session.scalar(
            select(ImageGroupItem).where(
                ImageGroupItem.group_id == group.id, ImageGroupItem.output_index == 1
            )
        )
        assert session.get(Asset, int(flow.reference["id"])).media_id == primary.media_id
        assert operation.result_refs[0]["status"] == "adopted"


def test_assistant_media_reserves_whole_batch_and_pauses_without_admitting_when_over_limit(
    task_workspace,
):
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    flow = media_tool_run(task_workspace, count=2, limit=1)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.error["code"] == "assistant_media_budget_exhausted"
        assert session.get(AgentTask, flow.accepted.task.id).status == "paused_budget"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0


def test_assistant_media_switch_blocks_unsent_work_and_releases_reservation(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    flow = media_tool_run(task_workspace)
    with flow.factory() as session:
        task_service(session, flow.settings)
        AssistantPolicyService(session).update_generation_policy(
            {"media_generation_enabled": False, "policy_version": "0"}
        )
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert provider.calls == []
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.result["items"][0]["status"] == "not_submitted"
        assert operation.result["items"][0]["error"]["code"] == "assistant_media_disabled"
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"]["reserved"] == 0


def test_assistant_media_keeps_outputs_but_never_overwrites_a_human_edit(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset, ImageGroup

    flow = media_tool_run(task_workspace)
    with flow.factory.begin() as session:
        asset = session.get(Asset, int(flow.reference["id"]))
        asset.description = "人工刚修改的描述"
        asset.row_version += 1
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        asset = session.get(Asset, int(flow.reference["id"]))
        assert asset.description == "人工刚修改的描述" and asset.media_id is None
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "conflict" and operation.changes == []
        group = session.scalar(select(ImageGroup).where(ImageGroup.asset_id == asset.id))
        assert group.status == "completed" and group.primary_index is None


@pytest.mark.parametrize(
    "content", ["只搭建图片流程", "为已选素材生成图片？", "引用：为已选素材生成图片"]
)
def test_assistant_media_discussion_never_grants_a_forged_generation_call(task_workspace, content):
    from short_drama.domain import AsyncTask

    flow = media_tool_run(task_workspace, content=content)
    assert "submit_generation_batch" not in {
        item["name"] for item in flow.claim.inputs["kwargs"]["tools"]
    }
    with flow.factory() as session:
        assert (
            session.get(AgentOperation, flow.operation_id).error["code"]
            == "assistant_generation_request_required"
        )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_assistant_media_unknown_keeps_identity_reservation_and_stop_pending(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationError
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    class UnknownProvider(ImageProvider):
        def submit(self, *_args, **_kwargs):
            self.calls.append("unknown")
            raise GenerationError("timeout", accepted_unknown=True)

    flow = media_tool_run(task_workspace, count=2)
    provider, worker = media_worker(flow, UnknownProvider())
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "unknown-stop"
        )
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        task = session.get(AgentTask, flow.accepted.task.id)
        assert operation.status == "reconciling" and operation.finished_at is None
        assert task.status == "waiting_media" and task.control_state == "stop_pending"
        assert task.media_usage["images"] == {"reserved": 2, "submitted": 0, "succeeded": 0}
    assert provider.calls == ["unknown"]


@pytest.mark.parametrize("disable_at", ["validate", "submit"])
def test_assistant_media_final_send_barrier_and_sent_receipt_have_distinct_policy_rules(
    task_workspace, disable_at
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.service.assistant_policy_service import AssistantPolicyService
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    flow = media_tool_run(task_workspace)

    class SwitchingProvider(ImageProvider):
        def disable(self):
            with flow.factory() as session:
                task_service(session, flow.settings)
                AssistantPolicyService(session).update_generation_policy(
                    {"media_generation_enabled": False, "policy_version": "0"}
                )

        def validate(self, *_args):
            if disable_at == "validate":
                self.disable()
            return {}

        def submit(self, *args, **kwargs):
            if disable_at == "submit":
                self.disable()
            return super().submit(*args, **kwargs)

    provider, worker = media_worker(flow, SwitchingProvider())
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert len(provider.calls) == (1 if disable_at == "submit" else 0)
        assert operation.status == ("succeeded" if disable_at == "submit" else "failed")
        assert operation.result["items"][0]["reservation_state"] == (
            "committed" if disable_at == "submit" else "released"
        )


@pytest.mark.parametrize("undo", [False, True])
def test_assistant_media_primary_waits_for_first_output_then_falls_back_only_after_known_failure(
    task_workspace,
    undo,
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import (
        AIGenerationRecord,
        Asset,
        AsyncTask,
        ImageGroup,
        MediaAsset,
        MediaFile,
    )
    from short_drama.domain.agent_media import AgentMediaRequest
    from short_drama.service.image_group_service import ImageGroupService
    from short_drama.service.task_runtime_service import finish
    from short_drama.utils.snowflake import next_id

    flow = media_tool_run(task_workspace, count=2)
    with flow.factory.begin() as session:
        intent = session.scalar(
            select(AgentMediaRequest).where(AgentMediaRequest.operation_id == flow.operation_id)
        )
        native = session.get(AsyncTask, intent.generation_task_id)
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == native.id)
        )
        record.status, record.started_at = "sent", utcnow()
        media = MediaFile(
            id=next_id(),
            project_id=int(flow.reference["project_id"]),
            created_by=1,
            format_code="image/png",
            storage_locator=f"minio://images/assistant-fallback-{next_id()}.png",
            checksum_sha256="b" * 64,
            byte_size=10,
            width=4,
            height=4,
        )
        session.add(media)
        session.flush()
        session.add(
            MediaAsset(
                id=next_id(),
                record_id=record.id,
                output_index=2,
                media_id=media.id,
                media_type="image",
                name="Saved second output",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.flush()
        ImageGroupService(session).attach(native, record, media.id, 2)
        group = session.scalar(select(ImageGroup).where(ImageGroup.task_id == native.id))
        assert (
            group.primary_index is None
            and session.get(Asset, int(flow.reference["id"])).media_id is None
        )
        record.status = "unknown"
        finish(native, "failed", {"code": "provider_acceptance_unknown"})
        assert group.primary_index is None and group.status == "generating"
        record.status = "failed"
        finish(native, "failed", {"code": "first_output_failed"})
        assert group.primary_index == 2 and group.primary_mode == "auto"
        assert session.get(Asset, int(flow.reference["id"])).media_id == media.id
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert (
            operation.status == "failed"
            and operation.result["items"][0]["result"]["adopted"] is True
        )
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"] == {
            "reserved": 0,
            "submitted": 2,
            "succeeded": 1,
        }
    if undo:
        with flow.factory() as session:
            service = task_service(session, flow.settings)
            service.control_task(flow.accepted.task.id, {"kind": "stop"}, "stop-partial-media-undo")
            preview = service.undo_preview(flow.operation_id)
            assert len(preview.items) == 1 and preview.items[0].allowed, preview
            service.undo_batch(
                flow.operation_id,
                {"preview_hash": preview.preview_hash, "selected_keys": [preview.items[0].key]},
                "partial-media-undo",
            )
        with flow.factory() as session:
            task_service(session, flow.settings)
            assert session.get(Asset, int(flow.reference["id"])).media_id is None
            assert session.get(MediaFile, media.id) is not None
            assert session.get(AgentOperation, flow.operation_id).status == "failed"


@pytest.mark.parametrize(
    "patch,error",
    [
        ({"count": 2}, "assistant_generation_quantity"),
        ({"target_id": "9007199254740993"}, "assistant_generation_scope"),
        ({"reference_media_ids": ["9007199254740993"]}, "assistant_generation_reference"),
        ({"row_version": "2"}, "conflict"),
        ({"model_version": "2"}, "conflict"),
    ],
)
def test_assistant_media_rechecks_quantity_target_reference_and_trusted_versions(
    task_workspace, patch, error
):
    from short_drama.domain import AsyncTask

    flow = media_tool_run(task_workspace, item_patch=patch)
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).error["code"] == error
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_assistant_media_receipt_reads_cannot_cross_logical_tasks(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked

    flow = media_tool_run(task_workspace)
    other_conversation, other = new_task(task_workspace)
    with flow.factory() as session:
        with pytest.raises(NotFound):
            execute_locked(
                session,
                session.get(AgentConversation, other_conversation.id),
                session.get(AgentTask, other.task.id),
                SimpleNamespace(
                    tool_name="get_generation_status",
                    arguments={"operation_id": str(flow.operation_id)},
                ),
            )


def test_assistant_media_recovery_preserves_wait_and_never_starts_a_second_native_task(
    task_workspace,
):
    from short_drama.domain import AsyncTask

    flow = media_tool_run(task_workspace)
    with flow.factory.begin() as session:
        run = session.get(AgentRun, flow.accepted.run.id)
        run.lease_token, run.lease_until = "lost-worker", utcnow() - timedelta(seconds=1)
    assert recover(flow.factory, flow.settings) == 1
    execute_tools(flow.factory, flow.settings, flow.accepted.run.id)
    complete_operation(flow.factory, flow.settings, flow.accepted.run.id, flow.operation_id)
    with flow.factory() as session:
        assert session.get(AgentRun, flow.accepted.run.id).status == "waiting_generation"
        assert session.get(AgentOperation, flow.operation_id).status == "waiting_media"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1


def media_target_references(workspace, kind="asset"):
    from short_drama.service.asset_service import AssetService
    from short_drama.service.shot_script_service import ShotScriptService

    reference = subject_assets(workspace)[0]
    factory, settings, _model = workspace
    references = [] if kind == "shot" else [reference]
    with factory() as session:
        task_service(session, settings)
        for index in range(1, 3 if kind == "shot" else 2):
            row = (
                ShotScriptService(session).create(
                    {"project_id": reference["project_id"], "position": index, "script": "走入车站"}
                )
                if kind == "shot"
                else AssetService(session).create(
                    {"kind": "prop", "name": "手提箱", "description": "磨旧的箱子"},
                    project_id=reference["project_id"],
                )
            )
            references.append(
                {**reference, "kind": kind, "id": str(row.id), "revision": str(row.row_version)}
            )
    return references


@pytest.mark.parametrize("limit", [3, 4])
def test_assistant_media_multiple_targets_reserve_the_total_before_any_admission(
    task_workspace, limit
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    references = media_target_references(task_workspace)
    flow = media_tool_run(task_workspace, count=2, limit=limit, references=references)
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == (
            2 if limit == 4 else 0
        )
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == (
            2 if limit == 4 else 0
        )
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.status == ("waiting_media" if limit == 4 else "paused_budget")
        if limit == 4:
            assert task.media_usage["images"]["reserved"] == 4
    if limit == 4:
        provider, worker = media_worker(flow)
        drain_assistant_media(flow, worker)
        assert len(provider.calls) == 2
        assert collect_media_results(flow.factory, flow.settings) == 1
        with flow.factory() as session:
            task = session.get(AgentTask, flow.accepted.task.id)
            assert task.media_usage["images"] == {"reserved": 0, "submitted": 4, "succeeded": 4}
            assert len(session.get(AgentOperation, flow.operation_id).result_refs) == 2


def test_assistant_media_late_target_conflict_prevents_the_entire_batch(task_workspace):
    from short_drama.domain import Asset, AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    references = media_target_references(task_workspace)
    factory, _settings, _model = task_workspace

    def change_second(_accepted, _reference):
        with factory.begin() as session:
            target = session.get(Asset, int(references[1]["id"]))
            target.description, target.row_version = "后来人工修改", target.row_version + 1

    flow = media_tool_run(task_workspace, references=references, before_execute=change_second)
    with factory() as session:
        assert session.get(AgentOperation, flow.operation_id).error["code"] == "conflict"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0


def test_assistant_media_local_partial_admission_preserves_success_and_releases_only_missing_item(
    task_workspace, monkeypatch
):
    from short_drama.agent import media_tools
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.core.exceptions import BusinessError
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    original = media_tools.admit_media_locked

    def fail_second(session, owner, intent, settings):
        if intent.item_index == 2:
            raise BusinessError("本地模型能力暂不可用")
        return original(session, owner, intent, settings)

    monkeypatch.setattr(media_tools, "admit_media_locked", fail_second)
    flow = media_tool_run(task_workspace, references=media_target_references(task_workspace))
    with flow.factory() as session:
        intents = session.scalars(
            select(AgentMediaRequest).order_by(AgentMediaRequest.item_index)
        ).all()
        assert [item.status for item in intents] == ["accepted", "not_submitted"]
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"]["reserved"] == 1
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and len(provider.calls) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed" and len(operation.result_refs) == 1
        assert [item["reservation_state"] for item in operation.result["items"]] == [
            "committed",
            "released",
        ]
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.status == "paused_error"
        assert task.media_usage["images"] == {"reserved": 0, "submitted": 1, "succeeded": 1}


@pytest.mark.parametrize("explicit_reference", [False, True])
def test_assistant_media_native_shot_adoption_does_not_send_unselected_linked_content(
    task_workspace, explicit_reference
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, Asset, ImageGroup, ShotImage, ShotScript
    from short_drama.service.shot_asset_service import ShotAssetService
    from short_drama.service.shot_script_service import ShotScriptService

    asset_reference, groups = image_group_workspace(task_workspace)
    factory, settings, _model = task_workspace

    with factory() as session:
        task_service(session, settings)
        shot = ShotScriptService(session).create(
            {
                "project_id": asset_reference["project_id"],
                "position": 1,
                "script": "未授权发送的分镜正文",
            }
        )
        ShotAssetService(session).create(
            {
                "project_id": asset_reference["project_id"],
                "shot_id": str(shot.id),
                "asset_id": asset_reference["id"],
            }
        )
    with factory.begin() as session:
        asset = session.get(Asset, int(asset_reference["id"]))
        asset.description, asset.prompt = "未选入关联素材的正文", "未选入关联素材的提示词"
        shot = session.get(ShotScript, shot.id)
        shot.reference_media_ids = [groups[0]["items"][1]["media_id"]]
        reference = {
            **asset_reference,
            "kind": "shot",
            "id": str(shot.id),
            "revision": str(shot.row_version),
        }
    selected = groups[1]["items"][0]
    references = [reference]
    if explicit_reference:
        references.append(
            {
                **asset_reference,
                "kind": "image_slot",
                "id": selected["id"],
                "revision": selected["item_version"],
            }
        )
    flow = media_tool_run(
        task_workspace,
        references=references,
        targets=[reference],
        count=2,
        item_patch={"reference_media_ids": [selected["media_id"]]} if explicit_reference else None,
    )
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and len(provider.calls) == 1
    request = provider.calls[0]
    assert request["input"]["prompt"] == "雨中的人物"
    assert list(map(str, request["input"]["reference_media_ids"])) == (
        [selected["media_id"]] if explicit_reference else []
    )
    assert len(request["input"].get("reference_urls", [])) == int(explicit_reference)
    if explicit_reference:
        assert request["input"]["reference_urls"][0].startswith("https://")
    assert "未选入" not in str(request) and "未授权发送" not in str(request)
    with factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded"
        group = session.scalar(select(ImageGroup).where(ImageGroup.shot_id == int(reference["id"])))
        image = session.scalar(select(ShotImage).where(ShotImage.shot_id == int(reference["id"])))
        assert group.primary_index == 1 and group.primary_mode == "auto" and image.media_id
        assert operation.result_refs[0]["kind"] == "shot"
        record = session.scalar(select(AIGenerationRecord))
        assert "未选入" not in str(record.request_data) and "未授权发送" not in str(
            record.request_data
        )


def test_assistant_media_deleting_conversation_waits_for_adoption_and_preserves_work(
    task_workspace,
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset

    flow = media_tool_run(task_workspace)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        current = service.get_conversation(flow.conversation.id)
        result = service.delete_global_conversation(
            current.id, {"row_version": str(current.row_version)}, "delete-media"
        )
        assert result.status == "deleting"
    assert collect_media_results(flow.factory, flow.settings) == 0
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and len(provider.calls) == 1
    with flow.factory() as session:
        assert session.get(AgentConversation, flow.conversation.id).status == "deleted"
        assert session.get(Asset, int(flow.reference["id"])).media_id is not None


@pytest.mark.parametrize("change", ["frozen_request", "generation_task_id", "foreign_identity"])
def test_assistant_media_private_ledger_rejects_identity_replacement_and_stale_foreign_writes(
    task_workspace, change
):
    from short_drama.domain.agent_media import AgentMediaRequest

    flow = media_tool_run(task_workspace)
    if change == "foreign_identity":
        with flow.factory.begin() as session:
            session.add(
                User(
                    id=2,
                    username="media-second",
                    display_name="Second",
                    email="media-second@example.test",
                    password_hash="test-only",
                    status="active",
                    email_verified_at=utcnow(),
                    created_at=utcnow(),
                )
            )
    with flow.factory() as session:
        intent = session.scalar(select(AgentMediaRequest))
        session.commit()
        session.info["actor"] = task_actor(2 if change == "foreign_identity" else 1)
        with pytest.raises((WorkflowError, NotFound)), session.begin():
            if change == "frozen_request":
                intent.frozen_request = {**intent.frozen_request, "input": {"prompt": "替换"}}
            elif change == "generation_task_id":
                intent.generation_task_id = None
            else:
                intent.error = {"message": "越权修改"}
            session.flush()


def resume_media_tool(
    flow, body, *, tool_name="submit_generation_batch", control_key="continue-media"
):
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        current = next(
            item
            for item in service.task_runtime_state(flow.conversation.id).tasks
            if item.id == flow.accepted.task.id
        )
        resumed = service.control_task(
            current.id,
            {"kind": "resume", "expected_control_seq": str(current.control_seq)},
            control_key,
        )
    run_id = resumed.run.id
    store = AgentRuntimeStore(flow.factory, flow.settings)
    claim = store.claim_execution(run_id, 1, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": "media-retry"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "media-retry",
                        "tool_name": tool_name,
                        "args": body,
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    operation_id = claim_operation(flow.factory, flow.settings, run_id)
    assert operation_id is not None
    complete_operation(flow.factory, flow.settings, run_id, operation_id)
    return operation_id, claim


def test_assistant_media_retry_missing_outputs_requires_explicit_continue_and_keeps_old_success(
    task_workspace,
):
    from copy import deepcopy

    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationResult
    from short_drama.domain import AsyncTask, ImageGroup
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    class PartialProvider(ImageProvider):
        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            return GenerationResult(
                status="succeeded", adapter=result.adapter, outputs=result.outputs[:1]
            )

    flow = media_tool_run(task_workspace, count=2)
    _provider, worker = media_worker(flow, PartialProvider())
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        original = session.get(AgentOperation, flow.operation_id)
        assert original.status == "failed"
        original_receipt = deepcopy(original.result)
        old_group_id = original.result["items"][0]["result"]["image_group_id"]
        body = {**deepcopy(flow.body), "retry_operation_id": str(original.id)}
        body["requests"][0].update(count=1, row_version=original.result_refs[0]["row_version"])
        with pytest.raises(WorkflowError) as raised:
            execute_locked(
                session,
                session.get(AgentConversation, flow.conversation.id),
                session.get(AgentTask, flow.accepted.task.id),
                SimpleNamespace(
                    id=0,
                    run_id=flow.accepted.run.id,
                    input_revision=1,
                    tool_name="submit_generation_batch",
                    arguments=body,
                ),
                settings=flow.settings,
            )
        assert raised.value.code == "assistant_media_resume_required"
    retry_id, claim = resume_media_tool(flow, body)
    assert f'"retry_operation_id": "{flow.operation_id}"' in claim.inputs["kwargs"]["instructions"]
    with flow.factory() as session:
        retry = session.get(AgentOperation, retry_id)
        assert retry.status == "waiting_media"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 2
    retry_flow = SimpleNamespace(**{**vars(flow), "operation_id": retry_id})
    provider, worker = media_worker(retry_flow)
    drain_assistant_media(retry_flow, worker)
    assert len(provider.calls) == 1 and provider.calls[0]["parameters"]["count"] == 1
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        original = session.get(AgentOperation, flow.operation_id)
        retry = session.get(AgentOperation, retry_id)
        assert original.result == original_receipt and original.status == "failed"
        assert retry.status == "succeeded"
        old_group = session.get(ImageGroup, int(old_group_id))
        assert old_group.status == "partial" and old_group.primary_index == 1
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"] == {
            "reserved": 0,
            "submitted": 3,
            "succeeded": 2,
        }
        replay = execute_locked(
            session,
            session.get(AgentConversation, flow.conversation.id),
            session.get(AgentTask, flow.accepted.task.id),
            SimpleNamespace(
                id=0,
                run_id=retry.run_id,
                input_revision=1,
                tool_name="submit_generation_batch",
                arguments=body,
            ),
            settings=flow.settings,
        )
        assert replay["already_applied"] is True and replay["operation_id"] == str(retry_id)
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 2


def test_assistant_media_model_catalog_has_real_owned_versions_and_bounded_pages(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked

    flow = media_tool_run(task_workspace)
    with flow.factory() as session:
        task_service(session, flow.settings)
        extra = AIModelConfigService(session).create(
            {
                "service_type": "image",
                "name": "Second image fixture",
                "model_key": "fixture",
                "provider": "ark",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            }
        )
    with flow.factory() as session:
        conversation = session.get(AgentConversation, flow.conversation.id)
        task = session.get(AgentTask, flow.accepted.task.id)
        first = execute_locked(
            session,
            conversation,
            task,
            SimpleNamespace(tool_name="describe_creation_target", arguments={"model_limit": 1}),
        )
        assert (
            first["write_authorized"] is True
            and "submit_generation_batch" in first["available_tools"]
        )
        assert len(first["image_models"]) == 1 and first["next_model_offset"] == 1
        assert first["image_models"][0]["id"] == flow.body["requests"][0]["model_config_id"]
        assert first["image_models"][0]["row_version"] == "1"
        second = execute_locked(
            session,
            conversation,
            task,
            SimpleNamespace(
                tool_name="describe_creation_target",
                arguments={"model_limit": 1, "model_offset": 1},
            ),
        )
        assert (
            second["image_models"][0]["id"] == str(extra.id) and second["next_model_offset"] is None
        )
        assert "base_url" not in str(first) and "apikey" not in str(first)


def test_assistant_media_result_groups_read_saved_identities_without_refreshing_selected_body(
    task_workspace,
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset, ImageGroupItem

    flow = media_tool_run(task_workspace, count=2)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        group_id = operation.result_refs[0]["image_group_id"]
        arguments = {
            "operation_id": str(operation.id),
            "group_id": group_id,
            "view": "detail",
            "character_limit": 80,
        }
        conversation, task = (
            session.get(AgentConversation, flow.conversation.id),
            session.get(AgentTask, flow.accepted.task.id),
        )
        parts = []
        result = None
        for _ in range(30):
            result = execute_locked(
                session,
                conversation,
                task,
                SimpleNamespace(tool_name="read_image_group", arguments=arguments),
            )
            parts.append(result["content"])
            if result["next"] is None:
                break
            arguments = {**arguments, **result["next"]}
        else:
            pytest.fail("Generated image catalog did not finish its bounded continuation")
        catalog = json.loads("".join(parts))
        assert catalog["target_id"] == flow.reference["id"]
        assert len(catalog["groups"]) == 1 and len(catalog["groups"][0]["items"]) == 2
        frozen_media_id = catalog["groups"][0]["items"][0]["media_id"]
        assert "prompt" not in str(catalog) and "storage_locator" not in str(catalog)
        assert result["source_changed"] is False
        item_id = catalog["groups"][0]["items"][0]["id"]
    with flow.factory.begin() as session:
        item = session.get(ImageGroupItem, int(item_id))
        item.media_id = None
        item.item_version += 1
        asset = session.get(Asset, int(flow.reference["id"]))
        asset.description, asset.row_version = "不能借成果读取的新正文", asset.row_version + 1
    with flow.factory() as session:
        updated = execute_locked(
            session,
            session.get(AgentConversation, flow.conversation.id),
            session.get(AgentTask, flow.accepted.task.id),
            SimpleNamespace(
                tool_name="read_image_group",
                arguments={
                    "operation_id": str(flow.operation_id),
                    "group_id": group_id,
                    "view": "detail",
                },
            ),
        )
        assert updated["source_changed"] is True
        assert frozen_media_id in updated["content"] and "不能借成果读取" not in updated["content"]
        with pytest.raises(WorkflowError):
            execute_locked(
                session,
                session.get(AgentConversation, flow.conversation.id),
                session.get(AgentTask, flow.accepted.task.id),
                SimpleNamespace(
                    tool_name="read_image_group",
                    arguments={
                        "operation_id": str(flow.operation_id),
                        "group_id": "9007199254740993",
                        "view": "detail",
                    },
                ),
            )


@pytest.mark.parametrize("mode", ["standard", "canvas"])
def test_image_content_read_accepts_only_this_tasks_settled_generation(task_workspace, mode):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.media_runtime import collect_media_results

    flow = canvas_media_flow(task_workspace) if mode == "canvas" else media_tool_run(task_workspace)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory.begin() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        saved = operation.result["items"][0]["result"]
        group = saved["catalog"]["groups"][0] if mode == "standard" else None
        item = group["items"][0] if group else saved["items"][0]
        task = session.get(AgentTask, flow.accepted.task.id)
        task.context_snapshot = {"schema_version": 2, "subjects": []}
        owner = session.get(AgentConversation, flow.conversation.id)
        run = session.get(AgentRun, flow.accepted.run.id)
        args = {
            "operation_id": str(operation.id),
            **({"group_id": group["id"], "item_id": item["id"]} if group else {}),
            "media_id": item["media_id"],
        }
        result = execute_locked(
            session,
            owner,
            task,
            SimpleNamespace(
                tool_name="read_image_content",
                run_id=run.id,
                arguments=args,
            ),
        )
        assert result["media_id"] == item["media_id"]
        values = prepare_decision(session, owner, run)
        prompt = values["user_prompt"]
        assert [part["media_id"] for part in prompt["attachments"]] == [item["media_id"]]
        assert "read_image_content" in {entry.name for entry in values["tools"]}
        if mode == "canvas":
            directory = json.loads(values["instructions"].split("任务状态与用户原始输入：", 1)[1])
            assert directory["image_results"][0]["media_id"] == item["media_id"]
        foreign = SimpleNamespace(id=task.id + 1)
        with pytest.raises(NotFound):
            execute_locked(
                session,
                owner,
                foreign,
                SimpleNamespace(
                    tool_name="read_image_content",
                    run_id=run.id,
                    arguments=args,
                ),
            )
    if mode == "canvas":
        from short_drama.agent.image_tools import available_image_contents_locked
        from short_drama.domain.canvas import CanvasNode

        with flow.factory.begin() as session:
            node = session.scalar(
                select(CanvasNode).where(CanvasNode.node_key == flow.reference["id"])
            )
            node.archived_at = utcnow()
        with flow.factory.begin() as session:
            run = session.get(AgentRun, flow.accepted.run.id)
            task = session.get(AgentTask, flow.accepted.task.id)
            owner = session.get(AgentConversation, flow.conversation.id)
            assert available_image_contents_locked(session, owner, task, run) == {}
            values = prepare_decision(session, owner, run)
            assert str(item["media_id"]) not in str(values["user_prompt"])


def test_assistant_media_collector_does_not_attribute_later_human_replacement_to_generated_results(
    task_workspace,
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, ImageGroup, ImageGroupItem, MediaAsset

    flow = media_tool_run(task_workspace)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    with flow.factory.begin() as session:
        record = session.scalar(select(AIGenerationRecord))
        generated = session.scalar(select(MediaAsset).where(MediaAsset.record_id == record.id))
        generated_media_id = str(generated.media_id)
        group = session.scalar(select(ImageGroup).where(ImageGroup.task_id == record.task_id))
        slot = session.scalar(select(ImageGroupItem).where(ImageGroupItem.group_id == group.id))
        slot.media_id = None
        slot.item_version += 1
        group.primary_mode = "manual"
        group.primary_version += 1
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        result = session.get(AgentOperation, flow.operation_id).result["items"][0]["result"]
        assert result["items"][0]["media_id"] == generated_media_id


@pytest.mark.parametrize("kind", ["supplement", "redirect"])
def test_assistant_media_applies_new_requirements_only_after_the_complete_current_batch(
    task_workspace, kind
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset, AsyncTask

    flow = media_tool_run(task_workspace, count=2)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        if kind == "redirect":
            service.control_task(
                flow.accepted.task.id,
                {"kind": "redirect", "content": "改为只讨论后续构图"},
                "redirect-media",
            )
        else:
            service.send_task_message(
                flow.conversation.id,
                {"task_id": str(flow.accepted.task.id), "content": "后续构图用近景"},
                "supplement-media",
            )
    with flow.factory() as session:
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.input_revision == 2 and task.applied_input_revision == 1
        assert session.get(AgentOperation, flow.operation_id).status == "waiting_media"
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    execute_tools(flow.factory, flow.settings, flow.accepted.run.id)
    with flow.factory() as session:
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.applied_input_revision == 2
        assert session.get(Asset, int(flow.reference["id"])).media_id is not None
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
    assert provider.calls[0]["input"]["prompt"] == "雨中的人物"


def test_assistant_media_result_access_is_task_scoped_and_rechecked_before_model_send(
    task_workspace,
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Project

    flow = media_tool_run(task_workspace)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        group_id = session.get(AgentOperation, flow.operation_id).result_refs[0]["image_group_id"]
    other_conversation, other = new_task(task_workspace)
    with flow.factory() as session:
        with pytest.raises(NotFound):
            execute_locked(
                session,
                session.get(AgentConversation, other_conversation.id),
                session.get(AgentTask, other.task.id),
                SimpleNamespace(
                    tool_name="read_image_group",
                    arguments={
                        "operation_id": str(flow.operation_id),
                        "group_id": group_id,
                        "view": "detail",
                    },
                ),
            )
    with flow.factory() as session:
        task_service(session, flow.settings).send_task_message(
            flow.conversation.id,
            {
                "task_id": str(flow.accepted.task.id),
                "content": "只讨论刚才的图片",
                "subject_selection": {"expected_input_revision": "1", "subjects": []},
            },
            "remove-generated-source",
        )
    execute_tools(flow.factory, flow.settings, flow.accepted.run.id)
    with flow.factory() as session:
        version = session.get(AgentRun, flow.accepted.run.id).message_version
    store = AgentRuntimeStore(flow.factory, flow.settings)
    claim = store.claim_execution(flow.accepted.run.id, version, prepare_decision)
    assert claim is not None
    assert claim.inputs["subject_access_keys"] == []
    assert claim.inputs["result_access_keys"] == [
        f"{flow.reference['project_id']}:{flow.reference['project_id']}:image_group:{group_id}"
    ]
    with flow.factory() as session:
        result = execute_locked(
            session,
            session.get(AgentConversation, flow.conversation.id),
            session.get(AgentTask, flow.accepted.task.id),
            SimpleNamespace(
                tool_name="read_image_group",
                arguments={
                    "operation_id": str(flow.operation_id),
                    "group_id": group_id,
                    "view": "detail",
                },
            ),
        )
        assert result["total"] == 1
    with flow.factory.begin() as session:
        session.get(Project, int(flow.reference["project_id"])).archived_at = utcnow()
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(claim, {"fixture": "media-result-before-archive"})
    assert raised.value.code == "assistant_subjects_superseded"
    with flow.factory() as session:
        with pytest.raises(NotFound):
            execute_locked(
                session,
                session.get(AgentConversation, flow.conversation.id),
                session.get(AgentTask, flow.accepted.task.id),
                SimpleNamespace(
                    tool_name="read_image_group",
                    arguments={
                        "operation_id": str(flow.operation_id),
                        "group_id": group_id,
                        "view": "detail",
                    },
                ),
            )


def canvas_media_flow(workspace, *, kind="image", count=1, limit=None, nodes=1, **kwargs):
    from short_drama.domain.canvas import CanvasNode
    from short_drama.service.canvas_service import CanvasService

    project_id, canvas_id, saved = canvas_patch_workspace(workspace)
    factory, settings, _model = workspace
    with factory() as session:
        task_service(session, settings)
        native = CanvasService(session)
        document = saved["source_document"]
        for node in document["nodes"][:nodes]:
            node.update(
                type=kind,
                metadata={"content": "", "prompt": "节点旧私人提示词", "nodeRole": "generator"},
            )
        native.commit(
            project_id,
            canvas_id,
            {"expected_row_version": saved["row_version"], "source_document": document},
            "canvas-media-seed",
        )
        with session.begin():
            rows = session.scalars(
                select(CanvasNode).where(
                    CanvasNode.project_id == project_id,
                    CanvasNode.node_key.in_([node["id"] for node in document["nodes"][:nodes]]),
                )
            ).all()
            references = [
                {
                    "source_kind": "canvas",
                    "project_id": str(project_id),
                    "source_id": saved["source_document"]["id"],
                    "kind": "node",
                    "id": node.node_key,
                    "revision": str(node.row_version),
                }
                for node in sorted(rows, key=lambda row: row.node_key)
            ]
    labels = {"image": "图片", "video": "视频", "audio": "音频"}
    flow = media_tool_run(
        workspace,
        count=count,
        limit=limit,
        references=references,
        media_kind=kind,
        execution_target={
            "mode": "infinite_canvas",
            "project_id": str(project_id),
            "canvas_id": saved["source_document"]["id"],
        },
        content=kwargs.pop("content", f"为已选{labels[kind]}节点生成{count}个结果：雨中的人物"),
        **kwargs,
    )
    flow.canvas_id = canvas_id
    return flow


def test_assistant_canvas_media_waits_for_native_delivery_and_binding_before_stop(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, CanvasLibraryAsset, CanvasResult
    from short_drama.service.canvas_service import CanvasService

    flow = canvas_media_flow(task_workspace, count=2, limit=2)
    assert "submit_generation_batch" in [
        item["name"] for item in flow.claim.inputs["kwargs"]["tools"]
    ]
    with flow.factory() as session:
        task_service(session, flow.settings)
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "canvas-media-stop"
        )
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        task_service(session, flow.settings)
        task = session.get(AgentTask, flow.accepted.task.id)
        assert (
            task.status == "waiting_media"
            and task.control_seq == 1
            and task.applied_control_seq == 0
        )
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert len(provider.calls) == 1 and "original-b原正文" not in str(provider.calls)
    assert "节点旧私人提示词" not in str(provider.calls)
    with flow.factory() as session:
        task_service(session, flow.settings)
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded"
        assert session.get(AgentTask, flow.accepted.task.id).status == "stopped"
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 2
        assert session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 2
        record = session.scalar(select(AIGenerationRecord))
        assert record.config_snapshot["agent_managed"] is True
        assert "原生私有对话" not in str(record.request_data)
        ref = operation.result_refs[0]
        assert ref["kind"] == "canvas_node" and ref["status"] == "bound"
        assert ref["canvas_id"] == flow.reference["source_id"]
        assert ref["generated_count"] == ref["requested_count"] == 2
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"] == {
            "reserved": 0,
            "submitted": 2,
            "succeeded": 2,
        }
    with flow.factory() as session:
        task_service(session, flow.settings)
        saved = CanvasService(session).read(
            flow.reference["project_id"], flow.canvas_id, private=True
        )
        node = next(
            item for item in saved["source_document"]["nodes"] if item["id"] == "original-a"
        )
        assert node["metadata"]["nodeRole"] == "result"
        assert node["metadata"]["generationEffectKeys"] == [
            f"attach-node:{ref['generation_id']}:original-a:0"
        ]
        assert saved["source_document"]["nodes"][1]["metadata"]["content"] == "original-b原正文"


@pytest.mark.parametrize("change", ["private_edit", "replacement", "delete", "disable"])
def test_assistant_canvas_media_preserves_saved_work_and_conflicts(task_workspace, change):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.service.assistant_policy_service import AssistantPolicyService
    from short_drama.service.canvas_service import CanvasService

    flow = canvas_media_flow(task_workspace)
    if change == "disable":
        with flow.factory() as session:
            task_service(session, flow.settings)
            AssistantPolicyService(session).update_generation_policy(
                {"policy_version": "0", "media_generation_enabled": False}
            )
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    if change != "disable":
        with flow.factory() as session:
            task_service(session, flow.settings)
            native = CanvasService(session)
            current = native.read(flow.reference["project_id"], flow.canvas_id, private=True)
            document = current["source_document"]
            node = document["nodes"][0]
            if change == "private_edit":
                node["metadata"]["prompt"] = "人工新提示词"
            elif change == "replacement":
                node["metadata"].update(taskId="999", content="人工替换内容")
            else:
                document["nodes"] = document["nodes"][1:]
            native.commit(
                flow.reference["project_id"],
                flow.canvas_id,
                {"expected_row_version": current["row_version"], "source_document": document},
                "canvas-media-human-change",
            )
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        task_service(session, flow.settings)
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == ("failed" if change == "disable" else "conflict")
        assert len(provider.calls) == (0 if change == "disable" else 1)
        assert operation.result["items"][0]["reservation_state"] == (
            "released" if change == "disable" else "committed"
        )
        assert session.get(AgentTask, flow.accepted.task.id).status == "paused_error"
    if change == "private_edit":
        with flow.factory() as session:
            task_service(session, flow.settings)
            saved = CanvasService(session).read(
                flow.reference["project_id"], flow.canvas_id, private=True
            )
            assert saved["source_document"]["nodes"][0]["metadata"]["prompt"] == "人工新提示词"


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_assistant_canvas_media_video_and_audio_complete_archive_delivery_and_binding(
    task_workspace, tmp_path, kind
):
    import base64
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationResult
    from short_drama.domain import CanvasLibraryAsset, CanvasResult, MediaFile
    from short_drama.service.canvas_service import CanvasService
    from tests.integration.canvas.generation.test_canvas_generation_media_runtime import (
        playable_fixture,
    )

    flow = canvas_media_flow(task_workspace, kind=kind, limit=1)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    content = playable_fixture(tmp_path, kind)
    adapter = "ark_video.v1" if kind == "video" else "openai_speech.v1"

    class Provider:
        def __init__(self):
            self.calls, self.polls = [], 0

        def validate(self, _snapshot, _request, actual):
            assert actual == adapter
            return {}

        def completed(self):
            return GenerationResult(
                "succeeded", adapter, outputs=[{"base64": base64.b64encode(content).decode()}]
            )

        def submit(self, _snapshot, request, *_args, **_kwargs):
            self.calls.append(deepcopy(request))
            return (
                GenerationResult("submitted", adapter, provider_task_id="native-video-fixture")
                if kind == "video"
                else self.completed()
            )

        def poll(self, _snapshot, provider_id, *_args, **_kwargs):
            assert provider_id == "native-video-fixture"
            self.polls += 1
            return self.completed()

    provider, worker = media_worker(flow, Provider())
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert len(provider.calls) == 1 and provider.polls == int(kind == "video")
    assert "节点旧私人提示词" not in str(provider.calls)
    with flow.factory() as session:
        task_service(session, flow.settings)
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result or operation.error
        assert operation.result_refs[0]["media_kind"] == kind
        assert operation.result_refs[0]["status"] == "bound"
        meter_key = "audio" if kind == "audio" else "videos"
        assert session.get(AgentTask, flow.accepted.task.id).media_usage[meter_key] == {
            "reserved": 0,
            "submitted": 1,
            "succeeded": 1,
        }
        result = session.scalar(select(CanvasResult))
        media = session.get(MediaFile, result.media_id)
        assert media.duration_ms > 0 and result.attachment_status == "attached"
        assert session.scalar(select(CanvasLibraryAsset)).kind == kind
    with flow.factory() as session:
        task_service(session, flow.settings)
        saved = CanvasService(session).read(
            flow.reference["project_id"], flow.canvas_id, private=True
        )
        node = saved["source_document"]["nodes"][0]
        assert node["type"] == kind and node["metadata"]["nodeRole"] == "result"
        assert node["metadata"]["durationMs"] > 0


@pytest.mark.parametrize("change", ["private_edit", "delete"])
def test_assistant_canvas_media_final_send_rechecks_node_and_private_version(
    task_workspace, change
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.service.canvas_service import CanvasService

    flow = canvas_media_flow(task_workspace)
    with flow.factory() as session:
        task_service(session, flow.settings)
        native = CanvasService(session)
        current = native.read(flow.reference["project_id"], flow.canvas_id, private=True)
        document = current["source_document"]
        if change == "private_edit":
            document["nodes"][0]["metadata"]["prompt"] = "外发前人工新提示词"
        else:
            document["nodes"] = document["nodes"][1:]
        native.commit(
            flow.reference["project_id"],
            flow.canvas_id,
            {"expected_row_version": current["row_version"], "source_document": document},
            "canvas-media-before-send-change",
        )
    provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and not provider.calls
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed"
        assert operation.result["items"][0]["reservation_state"] == "released"
        assert operation.result["items"][0]["error"]["code"] == "assistant_canvas_source_changed"


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"target_id": "original-b"}, "assistant_generation_scope"),
        ({"row_version": "999"}, "conflict"),
        ({"private_revision": "999"}, "conflict"),
        ({"model_version": "999"}, "conflict"),
        ({"media_kind": "video"}, "assistant_generation_scope"),
        ({"reference_media_ids": ["9007199254740993"]}, "assistant_generation_reference"),
    ],
)
def test_assistant_canvas_media_rejects_untrusted_target_and_versions(task_workspace, patch, code):
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    flow = canvas_media_flow(task_workspace, item_patch=patch)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status in {"failed", "conflict"}
        assert operation.error["code"] == code
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0
        assert session.get(AgentTask, flow.accepted.task.id).media_usage == {}


@pytest.mark.parametrize("failure", ["budget", "second_version"])
def test_assistant_canvas_media_reserves_and_validates_the_whole_batch(task_workspace, failure):
    from short_drama.domain import AsyncTask
    from short_drama.domain.canvas import CanvasNodeUserState

    factory, _settings, _model = task_workspace

    def change_second(_accepted, _reference):
        if failure == "second_version":
            with factory.begin() as session:
                private = session.scalar(
                    select(CanvasNodeUserState).where(CanvasNodeUserState.node_key == "original-b")
                )
                private.row_version += 1

    flow = canvas_media_flow(
        task_workspace,
        nodes=2,
        count=2,
        limit=3 if failure == "budget" else 4,
        before_execute=change_second,
    )
    with factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status in {"failed", "conflict"}
        assert operation.error["code"] == (
            "assistant_media_budget_exhausted" if failure == "budget" else "conflict"
        )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.get(AgentTask, flow.accepted.task.id).media_usage == {}


def test_assistant_canvas_media_unknown_keeps_original_identity_and_stop_pending(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationError
    from short_drama.domain import AsyncTask
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    class UnknownProvider(ImageProvider):
        def submit(self, *_args, **_kwargs):
            self.calls.append("unknown")
            raise GenerationError("timeout", accepted_unknown=True)

    flow = canvas_media_flow(task_workspace)
    complete_operation(flow.factory, flow.settings, flow.accepted.run.id, flow.operation_id)
    provider, worker = media_worker(flow, UnknownProvider())
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "canvas-unknown-stop"
        )
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        task = session.get(AgentTask, flow.accepted.task.id)
        assert operation.status == "reconciling" and operation.finished_at is None
        assert task.status == "waiting_media" and task.control_state == "stop_pending"
        assert task.media_usage["images"] == {"reserved": 1, "submitted": 0, "succeeded": 0}
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
    assert provider.calls == ["unknown"]


def test_assistant_canvas_media_partial_failure_continues_only_missing_nodes(task_workspace):
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationResult
    from short_drama.domain import AsyncTask, CanvasLibraryAsset, CanvasResult
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    class PartialBatchProvider(ImageProvider):
        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            return GenerationResult(
                "failed" if len(self.calls) == 2 else "succeeded",
                "openai_images.v1",
                outputs=[] if len(self.calls) == 2 else result.outputs,
            )

    flow = canvas_media_flow(task_workspace, nodes=2, limit=3)
    provider, worker = media_worker(flow, PartialBatchProvider())
    drain_assistant_media(flow, worker)
    assert len(provider.calls) == 2
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed"
        original_receipt = deepcopy(operation.result)
        assert [item["status"] for item in operation.result["items"]] == ["succeeded", "failed"]
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 1
        assert session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 1
        body = {**deepcopy(flow.body), "retry_operation_id": str(operation.id)}
        body["requests"] = [body["requests"][1]]
        failed_ref = operation.result_refs[1]
        body["requests"][0].update(
            row_version=failed_ref["row_version"], private_revision=failed_ref["private_revision"]
        )
    retry_id, _claim = resume_media_tool(flow, body)
    with flow.factory() as session:
        retry = session.get(AgentOperation, retry_id)
        assert retry.status == "waiting_media", retry.result or retry.error
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 3
    retry_flow = SimpleNamespace(**{**vars(flow), "operation_id": retry_id})
    provider, worker = media_worker(retry_flow)
    drain_assistant_media(retry_flow, worker)
    assert len(provider.calls) == 1
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).result == original_receipt
        assert session.get(AgentOperation, retry_id).status == "succeeded"
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 2
        assert session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 2
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"] == {
            "reserved": 0,
            "submitted": 3,
            "succeeded": 2,
        }


def test_assistant_canvas_media_partial_outputs_are_delivered_before_missing_retry(task_workspace):
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationResult
    from short_drama.domain import CanvasLibraryAsset, CanvasResult
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    class PartialProvider(ImageProvider):
        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            return GenerationResult("succeeded", "openai_images.v1", outputs=result.outputs[:1])

    flow = canvas_media_flow(task_workspace, count=2, limit=3)
    _provider, worker = media_worker(flow, PartialProvider())
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed"
        original = deepcopy(operation.result)
        assert len(operation.result["items"][0]["result"]["items"]) == 1
        assert session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 1
        assert session.scalar(select(CanvasResult)).attachment_status == "detached"
        body = {**deepcopy(flow.body), "retry_operation_id": str(operation.id)}
        body["requests"][0].update(
            count=1, private_revision=operation.result_refs[0]["private_revision"]
        )
    retry_id, _claim = resume_media_tool(flow, body)
    with flow.factory() as session:
        retry = session.get(AgentOperation, retry_id)
        assert retry.status == "waiting_media", retry.result or retry.error
    retry_flow = SimpleNamespace(**{**vars(flow), "operation_id": retry_id})
    provider, worker = media_worker(retry_flow)
    drain_assistant_media(retry_flow, worker)
    assert len(provider.calls) == 1 and provider.calls[0]["parameters"]["count"] == 1
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).result == original
        assert session.get(AgentOperation, retry_id).status == "succeeded"
        assert session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 2
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["images"] == {
            "reserved": 0,
            "submitted": 3,
            "succeeded": 2,
        }


def test_assistant_canvas_media_denies_native_retry_outside_the_assistant_budget(task_workspace):
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationResult
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest
    from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
    from short_drama.service.canvas_generation_service import CanvasGenerationService
    from tests.integration.standard.assets.test_asset_image_generation import ImageProvider

    class FailedProvider(ImageProvider):
        def submit(self, *args, **kwargs):
            super().submit(*args, **kwargs)
            return GenerationResult("failed", "openai_images.v1")

    flow = canvas_media_flow(task_workspace, limit=1)
    _provider, worker = media_worker(flow, FailedProvider())
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        task_service(session, flow.settings)
        intent = session.scalar(select(AgentMediaRequest))
        payload = deepcopy(intent.frozen_request)
        payload["input"]["metadata"].update(
            client_operation_id="canvas-bypass-retry", retry_of=str(intent.generation_task_id)
        )
        generation_id = intent.generation_task_id
        session.rollback()
        native = CanvasGenerationService(session, flow.settings)
        assert native.detail(str(generation_id))["canRetry"] is False
        with pytest.raises(WorkflowError) as raised:
            native.create(CanvasRuntimeTaskCreate.model_validate(payload), "canvas-bypass-retry")
        assert raised.value.code == "canvas_task_retry_not_allowed"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1


def test_assistant_canvas_media_versions_allow_next_edit_without_expanding_read_scope(
    task_workspace,
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.authorization import digest
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.service.canvas_service import CanvasService

    flow = canvas_media_flow(task_workspace)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    content = "修改已选节点的提示词：新的生成要求"
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        original = session.get(AgentOperation, flow.operation_id)
        ref = original.result_refs[0]
        with pytest.raises(NotFound):
            execute_locked(
                session,
                session.get(AgentConversation, flow.conversation.id),
                session.get(AgentTask, flow.accepted.task.id),
                SimpleNamespace(
                    tool_name="read_task_results",
                    arguments={"operation_id": str(original.id), "ids": [ref["id"]]},
                ),
                settings=flow.settings,
            )
        session.rollback()
        service.control_task(flow.accepted.task.id, {"kind": "stop"}, "before-followup-edit")
        supplemented = service.send_task_message(
            flow.conversation.id,
            {"task_id": str(flow.accepted.task.id), "content": content},
            "canvas-media-followup-edit",
        )
        document = CanvasService(session).read(
            flow.reference["project_id"], flow.canvas_id, private=True
        )
        body = {
            "request_message_id": str(supplemented.message.id),
            "request_hash": digest({"role": "user", "content": content}),
            "patch": {
                "expected_revision": document["row_version"],
                "actions": [
                    {
                        "action": "update_node",
                        "node": ref["id"],
                        "row_version": ref["row_version"],
                        "private_revision": ref["private_revision"],
                        "patch": {"prompt": "新的生成要求"},
                    }
                ],
            },
        }
    edit_id, _claim = resume_media_tool(flow, body, tool_name="apply_canvas_patch")
    with flow.factory() as session:
        task_service(session, flow.settings)
        edited = session.get(AgentOperation, edit_id)
        assert edited.status == "succeeded", edited.result or edited.error
        assert edited.changes[0]["before"] is not None
        session.rollback()
        document = CanvasService(session).read(
            flow.reference["project_id"], flow.canvas_id, private=True
        )
        assert document["source_document"]["nodes"][0]["metadata"]["prompt"] == "新的生成要求"


def media_reference_body(flow, *, content, media_id, target_index=0):
    from copy import deepcopy

    from short_drama.agent.authorization import digest

    with flow.factory() as session:
        service = task_service(session, flow.settings)
        original = session.get(AgentOperation, flow.operation_id)
        ref = deepcopy(original.result_refs[target_index])
        session.rollback()
        service.control_task(flow.accepted.task.id, {"kind": "stop"}, "reference-followup-stop")
        supplemented = service.send_task_message(
            flow.conversation.id,
            {"task_id": str(flow.accepted.task.id), "content": content},
            "reference-followup-message",
        )
    body = deepcopy(flow.body)
    body.update(
        request_message_id=str(supplemented.message.id),
        request_hash=digest({"role": "user", "content": content}),
    )
    body["requests"] = [body["requests"][target_index]]
    body["requests"][0].update(row_version=ref["row_version"], reference_media_ids=[media_id])
    if "private_revision" in ref:
        body["requests"][0]["private_revision"] = ref["private_revision"]
    return body


@pytest.mark.parametrize("mode", ["standard", "canvas"])
def test_assistant_media_uses_only_real_current_task_generated_image_references(
    task_workspace, mode
):
    from short_drama.agent.media_runtime import collect_media_results

    flow = canvas_media_flow(task_workspace) if mode == "canvas" else media_tool_run(task_workspace)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        original = session.get(AgentOperation, flow.operation_id)
        media_id = original.result["items"][0]["result"]["items"][0]["media_id"]
    body = media_reference_body(
        flow,
        content="为已选图片节点生成图片：参考本任务先前生成图"
        if mode == "canvas"
        else "为已选素材生成图片：参考本任务先前生成图",
        media_id=media_id,
    )
    next_id, _claim = resume_media_tool(flow, body)
    with flow.factory() as session:
        operation = session.get(AgentOperation, next_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    next_flow = SimpleNamespace(**{**vars(flow), "operation_id": next_id})
    provider, worker = media_worker(next_flow)
    drain_assistant_media(next_flow, worker)
    assert list(map(str, provider.calls[0]["input"]["reference_media_ids"])) == [media_id]
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        assert session.get(AgentOperation, next_id).status == "succeeded"


def test_assistant_media_generated_reference_rechecks_origin_before_paid_send(task_workspace):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain.canvas import CanvasNode

    flow = canvas_media_flow(task_workspace, nodes=2)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        original = session.get(AgentOperation, flow.operation_id)
        media_id = original.result["items"][0]["result"]["items"][0]["media_id"]
    body = media_reference_body(
        flow,
        content="为节点 ID original-b生成图片：参考本任务前图",
        media_id=media_id,
        target_index=1,
    )
    next_id, _claim = resume_media_tool(flow, body)
    with flow.factory.begin() as session:
        operation = session.get(AgentOperation, next_id)
        assert operation.status == "waiting_media", operation.result or operation.error
        origin = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "original-a"))
        origin.archived_at = utcnow()
    next_flow = SimpleNamespace(**{**vars(flow), "operation_id": next_id})
    provider, worker = media_worker(next_flow)
    drain_assistant_media(next_flow, worker)
    assert not provider.calls
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, next_id)
        assert operation.status == "failed"
        assert operation.result["items"][0]["error"]["code"] == (
            "assistant_generation_reference_changed"
        )
        assert operation.result["items"][0]["reservation_state"] == "released"


@pytest.mark.parametrize("invalid", ["owner", "task", "unfinished", "output_index", "deleted"])
def test_assistant_media_generated_reference_does_not_expand_identity_or_scope(
    task_workspace, invalid
):
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain.canvas import CanvasNode
    from short_drama.service.assistant_media_reference_service import generated_image_references

    flow = canvas_media_flow(task_workspace)
    _provider, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory.begin() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        media_id = int(operation.result["items"][0]["result"]["items"][0]["media_id"])
        assert generated_image_references(session, 1, flow.accepted.task.id, {media_id})
        owner, task_id = 1, flow.accepted.task.id
        if invalid == "owner":
            owner = 2
        elif invalid == "task":
            task_id += 1
        elif invalid == "unfinished":
            operation.status = "waiting_media"
            operation.phase, operation.finished_at = "waiting_media", None
        elif invalid == "output_index":
            from short_drama.domain.agent_media import AgentMediaRequest

            intent = session.scalar(select(AgentMediaRequest))
            changed = deepcopy(intent.result)
            changed["items"][0]["output_index"] += 1
            intent.result = changed
        else:
            node = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "original-a"))
            node.archived_at = utcnow()
        session.flush()
        assert generated_image_references(session, owner, task_id, {media_id}) == {}


def voice_design_flow(workspace, **kwargs):
    factory, settings, _model = workspace
    settings.native_video_enabled = settings.audio_production_enabled = True
    reference = kwargs.pop("reference", None) or subject_assets(workspace)[0]
    return media_tool_run(
        workspace,
        media_kind="audio",
        content="为已选角色生成一个音色：沉稳自然的男声",
        references=[reference],
        targets=[reference],
        model_patch={
            "provider": "bailian",
            "model_key": "cosyvoice-v3.5-flash",
            "base_url": "https://dashscope.aliyuncs.com/api/v1",
        },
        item_patch={
            "target_kind": "character_voice",
            "media_kind": "audio",
            "voice_version": "0",
            "prompt": "沉稳自然的男声",
            "preview_text": "清晨的风吹过窗前，今天又是新的开始。",
            **kwargs.pop("item_patch", {}),
        },
        **kwargs,
    )


def voice_design_provider(tmp_path, *, before_validate=None, before_submit=None, fail=False):
    import base64
    import subprocess
    from copy import deepcopy

    from short_drama.ai import GenerationResult
    from short_drama.service.video_render import executable

    audio = tmp_path / "assistant-voice.wav"
    if not audio.exists():
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=500:sample_rate=24000:duration=3.2",
                str(audio),
            ],
            check=True,
            timeout=30,
        )

    class Provider:
        def __init__(self):
            self.calls = []

        def validate(self, _snapshot, _request, adapter):
            assert adapter == "dashscope_voice_design.v1"
            if before_validate:
                before_validate()
            return {}

        def submit(self, _snapshot, request, *_args, **_kwargs):
            self.calls.append(deepcopy(request))
            if before_submit:
                before_submit()
            return GenerationResult(
                "failed" if fail else "succeeded",
                "dashscope_voice_design.v1",
                provider_task_id="assistant-voice",
                voice={"voice_id": "assistant-voice-id", "target_model": "cosyvoice-v3.5-flash"},
                outputs=[] if fail else [{"base64": base64.b64encode(audio.read_bytes()).decode()}],
            )

    return Provider()


def test_assistant_voice_design_waits_for_archival_and_atomic_native_adoption(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, MediaFile
    from short_drama.domain.native_voice import CharacterVoice

    flow = voice_design_flow(task_workspace, limit=1)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-voice"
        )
    provider, worker = media_worker(flow, voice_design_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert len(provider.calls) == 1
    assert provider.calls[0]["input"] == {
        "voice_prompt": "沉稳自然的男声",
        "preview_text": "清晨的风吹过窗前，今天又是新的开始。",
    }
    assert "第一部的设定" not in str(provider.calls[0])
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result or operation.error
        assert operation.result_refs[0]["status"] == "adopted"
        assert operation.result_refs[0]["media_role"] == "character_voice"
        assert operation.result_refs[0]["voice_version"] == "1"
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.status == "stopped"
        assert task.media_usage["audio"] == {"reserved": 0, "submitted": 1, "succeeded": 1}
        binding = session.get(
            CharacterVoice, (int(flow.reference["project_id"]), int(flow.reference["id"]))
        )
        assert binding.row_version == 1
        media = session.get(MediaFile, binding.media_id)
        assert 3000 <= media.duration_ms <= 7500 and media.checksum_sha256
        assert session.get(AIGenerationRecord, binding.record_id).response_data["voice"]["voice_id"]


@pytest.mark.parametrize("change", ["edit", "delete", "flags"])
def test_assistant_voice_design_rechecks_source_before_first_external_send(
    task_workspace, tmp_path, change
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset

    flow = voice_design_flow(task_workspace)
    with flow.factory.begin() as session:
        asset = session.get(Asset, int(flow.reference["id"]))
        if change == "edit":
            asset.description = "后来的人工修改"
            asset.row_version += 1
        elif change == "delete":
            asset.deleted_at = utcnow()
        else:
            flow.settings.audio_production_enabled = False
    provider, worker = media_worker(flow, voice_design_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert provider.calls == []
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed", operation.result or operation.error
        assert operation.result["items"][0]["error"]["code"] == "assistant_voice_source_changed"
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["audio"]["reserved"] == 0


def adopted_assistant_voice(workspace, tmp_path):
    from short_drama.agent.media_runtime import collect_media_results

    flow = voice_design_flow(workspace)
    _provider, worker = media_worker(flow, voice_design_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).status == "succeeded"
    return flow


def test_assistant_voice_design_replacement_preserves_original_sample_history(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import MediaAsset, MediaFile
    from short_drama.domain.native_voice import CharacterVoice

    first = adopted_assistant_voice(task_workspace, tmp_path)
    with first.factory() as session:
        original = session.get(
            CharacterVoice, (int(first.reference["project_id"]), int(first.reference["id"]))
        )
        original_record, original_media = original.record_id, original.media_id
    second = voice_design_flow(
        task_workspace,
        reference=first.reference,
        identity_key="replace-voice",
        item_patch={"voice_version": "1"},
    )
    provider, worker = media_worker(second, voice_design_provider(tmp_path))
    drain_assistant_media(second, worker)
    assert collect_media_results(second.factory, second.settings) == 1
    assert len(provider.calls) == 1
    with second.factory() as session:
        binding = session.get(
            CharacterVoice, (int(second.reference["project_id"]), int(second.reference["id"]))
        )
        assert binding.row_version == 2 and binding.record_id != original_record
        assert binding.media_id != original_media
        assert session.get(MediaFile, original_media).published_at is not None
        assert session.scalar(select(MediaAsset.id).where(MediaAsset.record_id == original_record))
        operation = session.get(AgentOperation, second.operation_id)
        assert operation.changes == [
            {
                "kind": "character_voice",
                "id": second.reference["id"],
                "before": {
                    "row_version": "1",
                    "record_id": str(original_record),
                    "media_id": str(original_media),
                },
                "after": {
                    "row_version": "2",
                    "record_id": str(binding.record_id),
                    "media_id": str(binding.media_id),
                },
            }
        ]


@pytest.mark.parametrize("when", ["validate", "submit"])
def test_assistant_voice_design_guards_final_send_and_late_manual_voice_changes(
    task_workspace, tmp_path, when
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain.native_voice import CharacterVoice

    first = adopted_assistant_voice(task_workspace, tmp_path)
    second = voice_design_flow(
        task_workspace,
        reference=first.reference,
        identity_key="race-voice",
        item_patch={"voice_version": "1"},
    )

    def edit_binding():
        with second.factory.begin() as session:
            binding = session.get(
                CharacterVoice, (int(second.reference["project_id"]), int(second.reference["id"]))
            )
            binding.row_version = 2

    provider, worker = media_worker(
        second, voice_design_provider(tmp_path, **{f"before_{when}": edit_binding})
    )
    drain_assistant_media(second, worker)
    assert collect_media_results(second.factory, second.settings) == 1
    assert len(provider.calls) == (1 if when == "submit" else 0)
    with second.factory() as session:
        operation = session.get(AgentOperation, second.operation_id)
        assert operation.status == ("conflict" if when == "submit" else "failed")
        assert operation.changes == []
        if when == "submit":
            assert len(operation.result["items"][0]["result"]["items"]) == 1
            assert not operation.result["items"][0]["result"]["adopted"]
        binding = session.get(
            CharacterVoice, (int(second.reference["project_id"]), int(second.reference["id"]))
        )
        assert binding.row_version == 2


@pytest.mark.parametrize("case", ["guessed", "ceiling"])
def test_assistant_voice_design_cannot_guess_fresh_binding_or_overflow_its_version(
    task_workspace, tmp_path, case
):
    from short_drama.domain import AsyncTask
    from short_drama.domain.native_voice import CharacterVoice
    from short_drama.schemas.base import UINT64_MAX

    first = adopted_assistant_voice(task_workspace, tmp_path)

    def change_binding(*_args):
        with first.factory.begin() as session:
            binding = session.get(
                CharacterVoice, (int(first.reference["project_id"]), int(first.reference["id"]))
            )
            binding.row_version = 2 if case == "guessed" else UINT64_MAX

    if case == "ceiling":
        change_binding()
    second = voice_design_flow(
        task_workspace,
        reference=first.reference,
        identity_key="version-voice",
        item_patch={"voice_version": "2" if case == "guessed" else str(UINT64_MAX)},
        before_execute=change_binding if case == "guessed" else None,
    )
    with second.factory() as session:
        operation = session.get(AgentOperation, second.operation_id)
        assert operation.status == "failed", operation.result or operation.error
        assert operation.error["code"] == "conflict"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1


def test_assistant_voice_design_uses_audio_budget_before_admitting_native_request(task_workspace):
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest

    flow = voice_design_flow(task_workspace, limit=0)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.error["code"] == "assistant_media_budget_exhausted"
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_assistant_voice_design_unknown_holds_identity_reservation_and_pending_stop(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationError
    from short_drama.domain import AsyncTask

    flow = voice_design_flow(task_workspace)
    provider = voice_design_provider(tmp_path)

    def unknown(*_args, **_kwargs):
        provider.calls.append("unknown")
        raise GenerationError("timeout", accepted_unknown=True)

    provider.submit = unknown
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-unknown-voice"
        )
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        task = session.get(AgentTask, flow.accepted.task.id)
        assert operation.status == "reconciling" and operation.finished_at is None
        assert task.control_state == "stop_pending"
        assert task.media_usage["audio"] == {"reserved": 1, "submitted": 0, "succeeded": 0}
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
    assert provider.calls == ["unknown"]


def test_assistant_voice_design_explicit_resume_creates_only_the_missing_attempt(
    task_workspace, tmp_path
):
    from copy import deepcopy

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AsyncTask

    flow = voice_design_flow(task_workspace, limit=2)
    _provider, worker = media_worker(flow, voice_design_provider(tmp_path, fail=True))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        failed = deepcopy(session.get(AgentOperation, flow.operation_id).result)
        assert session.get(AgentTask, flow.accepted.task.id).status == "paused_error"
    body = {**flow.body, "retry_operation_id": str(flow.operation_id)}
    operation_id, claim = resume_media_tool(flow, body)
    retry_flow = SimpleNamespace(**{**vars(flow), "operation_id": operation_id, "claim": claim})
    provider, worker = media_worker(retry_flow, voice_design_provider(tmp_path))
    drain_assistant_media(retry_flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert len(provider.calls) == 1
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).result == failed
        assert session.get(AgentOperation, operation_id).status == "succeeded"
        assert session.get(AgentTask, flow.accepted.task.id).media_usage["audio"] == {
            "reserved": 0,
            "submitted": 2,
            "succeeded": 1,
        }
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 2


def test_assistant_voice_design_cannot_target_non_character_assets(task_workspace):
    from short_drama.domain import AsyncTask
    from short_drama.service.asset_service import AssetService

    reference = subject_assets(task_workspace)[0]
    with task_workspace[0]() as session:
        task_service(session, task_workspace[1])
        scene = AssetService(session).create(
            {"kind": "scene", "name": "雨夜街道", "description": "非角色目标"},
            project_id=int(reference["project_id"]),
        )
        reference = {**reference, "id": str(scene.id), "revision": str(scene.row_version)}
    flow = voice_design_flow(task_workspace, reference=reference)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed", operation.result or operation.error
        assert operation.error["code"] == "conflict"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def native_shot_video_flow(workspace, tmp_path, monkeypatch, *, selected_voice=True, **kwargs):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import Asset, ShotScript
    from short_drama.service.native_voice_service import NativeVoiceService
    from short_drama.service.shot_asset_service import ShotAssetService

    monkeypatch.setenv("NATIVE_VIDEO_ENABLED", "true")
    reference, groups = image_group_workspace(workspace, kind="shot")
    factory, settings, _model = workspace
    with factory() as session:
        task_service(session, settings)
        character = session.scalar(
            select(Asset).where(Asset.project_id == int(reference["project_id"]))
        )
        character_reference = {
            **reference,
            "kind": "asset",
            "id": str(character.id),
            "revision": str(character.row_version),
        }
    voice_flow = voice_design_flow(workspace, reference=character_reference)
    _provider, voice_worker = media_worker(voice_flow, voice_design_provider(tmp_path))
    drain_assistant_media(voice_flow, voice_worker)
    assert collect_media_results(factory, settings) == 1
    lines = kwargs.pop(
        "dialogue_lines", [{"character_id": character_reference["id"], "text": "车来了。"}]
    )
    with factory() as session:
        task_service(session, settings)
        voice = NativeVoiceService(session, settings)
        voice.set_mode(reference["project_id"], {"row_version": 0, "mode": "native"})
        ShotAssetService(session).create(
            {
                "project_id": reference["project_id"],
                "shot_id": reference["id"],
                "asset_id": character_reference["id"],
            }
        )
        dialogue = voice.save_dialogue(
            reference["project_id"],
            reference["id"],
            {
                "row_version": 0,
                "request_id": "assistant-native-dialogue",
                "document": {
                    "reviewed": True,
                    "lines": lines,
                },
            },
        )
    with factory.begin() as session:
        shot = session.get(ShotScript, int(reference["id"]))
        shot.script = "未授权发送的分镜原文"
        shot.video_prompt = "未授权发送的私人视频提示词"
        shot.video_settings = {"resolution": "720p", "duration_ms": 4000}
        reference["revision"] = str(shot.row_version)
    image = groups[0]["items"][0]
    references = [
        reference,
        {**reference, "kind": "image_slot", "id": image["id"], "revision": image["item_version"]},
        *([character_reference] if selected_voice else []),
    ]
    flow = media_tool_run(
        workspace,
        media_kind="video",
        identity_key="native-video",
        content=kwargs.pop("content", "为已选分镜生成一段有声视频：雨中的人物"),
        references=references,
        targets=[reference],
        item_patch={
            "target_kind": "shot_video",
            "media_kind": "video",
            "reference_media_ids": [image["media_id"]],
            "native_dialogue_version": str(dialogue["row_version"]),
            "native_voice_versions": [
                {
                    "character_id": character_reference["id"],
                    "row_version": character_reference["revision"],
                    "voice_version": kwargs.pop("voice_version", "1"),
                }
            ]
            if lines
            else [],
            **kwargs.pop("item_patch", {}),
        },
        **kwargs,
    )
    flow.character_reference = character_reference
    flow.voice_storage = voice_worker.storage
    return flow


def test_assistant_native_shot_video_requires_selected_voice_identity(
    task_workspace, tmp_path, monkeypatch
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, MediaFile, ShotVideo

    flow = native_shot_video_flow(task_workspace, tmp_path, monkeypatch)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media", operation.result or operation.error
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-native-video"
        )
    import time

    provider, worker = media_worker(
        flow, native_video_provider(tmp_path), storage=flow.voice_storage
    )
    submit = provider.submit

    def read_voice(snapshot, request, *args, **kwargs):
        content = kwargs["audio_reference_loader"](0, 10 * 1024**2, time.monotonic() + 30)
        assert content[:4] == b"RIFF"
        return submit(snapshot, request, *args, **kwargs)

    provider.submit = read_voice
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert len(provider.calls) == provider.polls == 1
    request = provider.calls[0]
    assert request["parameters"]["generate_audio"] is True
    assert "车来了。" in request["input"]["prompt"]
    assert request["input"]["prompt"].startswith("雨中的人物\n声音与对白要求")
    assert len(request["input"]["audio_reference_media_ids"]) == 1
    assert "不允许读取" not in str(request)
    assert "第一部的设定" not in str(request)
    assert "未授权发送" not in str(request)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result or operation.error
        assert operation.result_refs[0]["status"] == "adopted"
        assert session.get(AgentTask, flow.accepted.task.id).status == "stopped"
        video = session.scalar(select(ShotVideo))
        media = session.get(MediaFile, video.media_id)
        assert media.video_metadata["native_quality"]["technical_pass"] is True
        assert media.video_metadata["adopted_native_speech"]["lines"][0]["text"] == "车来了。"
        record = session.scalar(
            select(AIGenerationRecord).where(
                AIGenerationRecord.task_id == int(operation.result_refs[0]["generation_id"])
            )
        )
        assert record.request_data["source_snapshot"]["native_speech"]["lines"]


def native_video_provider(tmp_path, *, audible=True, **kwargs):
    import subprocess

    from short_drama.service.video_render import executable

    command = [
        executable("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=32x32:d=4",
    ]
    if audible:
        command += ["-f", "lavfi", "-i", "sine=frequency=500:sample_rate=24000:duration=4"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audible:
        command += ["-c:a", "aac", "-shortest"]
    subprocess.run(command + [str(tmp_path / "generated.mp4")], check=True, timeout=30)
    return shot_video_provider(tmp_path, **kwargs)


@pytest.mark.parametrize(
    "case", ["unselected", "dialogue_version", "voice_version", "guessed_new_voice", "ordinary"]
)
def test_assistant_native_shot_video_rejects_untrusted_speech_sources(
    task_workspace, tmp_path, monkeypatch, case
):
    from short_drama.domain import AsyncTask

    kwargs = {"selected_voice": False} if case == "unselected" else {}
    if case == "dialogue_version":
        kwargs["item_patch"] = {"native_dialogue_version": "999"}
    elif case == "voice_version":
        kwargs["voice_version"] = "999"
    elif case == "guessed_new_voice":
        from short_drama.domain.native_voice import CharacterVoice

        def changed(_accepted, _reference):
            with task_workspace[0].begin() as session:
                session.scalar(select(CharacterVoice)).row_version = 2

        kwargs.update(voice_version="2", before_execute=changed)
    elif case == "ordinary":
        kwargs["content"] = "为已选分镜生成一段视频"
    flow = native_shot_video_flow(task_workspace, tmp_path, monkeypatch, **kwargs)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed", operation.result or operation.error
        assert operation.error["code"] == (
            "assistant_generation_scope" if case == "ordinary" else "conflict"
        )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1


@pytest.mark.parametrize("when", ["claim", "validate", "poll"])
def test_assistant_native_shot_video_rechecks_voice_before_send_and_adoption(
    task_workspace, tmp_path, monkeypatch, when
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import MediaAsset, ShotVideo
    from short_drama.domain.native_voice import CharacterVoice

    flow = native_shot_video_flow(task_workspace, tmp_path, monkeypatch)

    def changed():
        with flow.factory.begin() as session:
            binding = session.get(
                CharacterVoice,
                (int(flow.reference["project_id"]), int(flow.character_reference["id"])),
            )
            binding.row_version += 1

    if when == "claim":
        changed()
    provider, worker = media_worker(
        flow,
        native_video_provider(tmp_path, **({f"before_{when}": changed} if when != "claim" else {})),
    )
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert len(provider.calls) == int(when == "poll")
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.result["items"][0]["status"] == (
            "conflict" if when == "poll" else "not_submitted"
        )
        assert session.scalar(select(ShotVideo)) is None
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 1 + int(
            when == "poll"
        )


def test_assistant_native_shot_video_keeps_silent_candidate_without_adopting(
    task_workspace, tmp_path, monkeypatch
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import MediaAsset, ShotVideo

    flow = native_shot_video_flow(task_workspace, tmp_path, monkeypatch)
    provider, worker = media_worker(flow, native_video_provider(tmp_path, audible=False))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and len(provider.calls) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "conflict", operation.result or operation.error
        assert operation.result["items"][0]["error"]["code"] == "native_audio_invalid"
        assert session.scalar(select(ShotVideo)) is None
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 2


def test_assistant_native_shot_video_rechecks_runtime_feature_flags(
    task_workspace, tmp_path, monkeypatch
):
    from short_drama.agent.media_runtime import collect_media_results

    flow = native_shot_video_flow(task_workspace, tmp_path, monkeypatch)
    flow.settings.audio_production_enabled = False
    provider, worker = media_worker(flow, native_video_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and not provider.calls
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        item = operation.result["items"][0]
        assert item["status"] == "not_submitted" and item["reservation_state"] == "released"
        assert item["error"]["code"] == "assistant_shot_video_source_changed"


def test_assistant_native_shot_video_reviewed_empty_dialogue_needs_no_voice_reference(
    task_workspace, tmp_path, monkeypatch
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import MediaFile, ShotVideo

    flow = native_shot_video_flow(
        task_workspace, tmp_path, monkeypatch, selected_voice=False, dialogue_lines=[]
    )
    provider, worker = media_worker(flow, native_video_provider(tmp_path, audible=False))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1 and len(provider.calls) == 1
    assert provider.calls[0]["input"]["audio_reference_media_ids"] == []
    assert "本镜头没有对白" in provider.calls[0]["input"]["prompt"]
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result or operation.error
        media = session.get(MediaFile, session.scalar(select(ShotVideo)).media_id)
        assert media.video_metadata["native_quality"]["dialogue_required"] is False


def dialogue_audio_workspace(workspace):
    from short_drama.domain.project_assembly import ProjectAssembly
    from short_drama.domain.project_sound import ProjectSound
    from short_drama.schemas.project_sound import SoundDocument
    from short_drama.utils.snowflake import next_id

    factory, settings, _model = workspace
    settings.audio_production_enabled = settings.native_video_enabled = True
    reference = subject_assets(workspace)[0]
    with factory() as session:
        task_service(session, settings)
        model = AIModelConfigService(session).create(
            {
                "service_type": "audio",
                "name": "Saved dubbing fixture",
                "provider": "openai",
                "model_key": "gpt-4o-mini-tts",
                "base_url": "https://api.openai.com/v1",
            }
        )
    with factory.begin() as session:
        assembly = ProjectAssembly(
            id=next_id(),
            project_id=int(reference["project_id"]),
            aspect="16:9",
            row_version=1,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(assembly)
        session.flush()
        document = SoundDocument.model_validate(
            {
                "schema_version": 2,
                "dialogue": [
                    {
                        "id": f"line_{index}",
                        "character": f"角色{index}",
                        "text": text,
                        "voice": "alloy",
                        "config_id": str(model.id),
                        "start_ms": index * 1000,
                    }
                    for index, text in enumerate(
                        ("清晨的风吹过窗前。", "今晚我们在桥头见面。", "未选台词正文")
                    )
                ],
                "subtitles": [{"start_ms": 0, "end_ms": 1000, "text": "未选字幕正文"}],
            }
        ).model_dump(mode="json")
        session.add(
            ProjectSound(
                assembly_id=assembly.id,
                row_version=1,
                document=document,
                reviewed_timeline_hash="a" * 64,
                updated_at=utcnow(),
            )
        )
    return (
        [
            {**reference, "kind": "sound_line", "id": f"line_{index}", "revision": "1"}
            for index in range(3)
        ],
        model,
        assembly.id,
    )


def dialogue_audio_flow(workspace, *, lines=1, **kwargs):
    references, model, assembly_id = dialogue_audio_workspace(workspace)
    flow = media_tool_run(
        workspace,
        media_kind="audio",
        content=kwargs.pop("content", "为已选台词生成配音"),
        references=references[:lines],
        targets=kwargs.pop("targets", references[:lines]),
        existing_model=model,
        item_patch={
            "target_kind": "dialogue_audio",
            "media_kind": "audio",
            **kwargs.pop("item_patch", {}),
        },
        **kwargs,
    )
    flow.assembly_id = assembly_id
    return flow


def dialogue_audio_provider(tmp_path, *, before_validate=None, before_submit=None):
    import base64
    from copy import deepcopy

    from short_drama.ai import GenerationResult
    from tests.integration.canvas.generation.test_canvas_generation_media_runtime import (
        playable_fixture,
    )

    saved = tmp_path / "generated.mp3"
    content = saved.read_bytes() if saved.exists() else playable_fixture(tmp_path, "audio")

    class Provider:
        def __init__(self):
            self.calls = []

        def validate(self, _snapshot, _request, adapter):
            assert adapter == "openai_speech.v1"
            if before_validate:
                before_validate()
            return {}

        def submit(self, _snapshot, request, *_args, **_kwargs):
            self.calls.append(deepcopy(request))
            if before_submit:
                before_submit()
            return GenerationResult(
                "succeeded",
                "openai_speech.v1",
                outputs=[{"base64": base64.b64encode(content).decode()}],
            )

    return Provider()


def test_assistant_dialogue_audio_only_reads_explicit_saved_lines(task_workspace):
    from short_drama.agent.assistant_tools import ReadSelectedSubjects
    from short_drama.service.assistant_subject_service import read_selected_snapshot

    refs, _model, _assembly = dialogue_audio_workspace(task_workspace)
    factory, settings, _ = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        directory = service.subject_directory(int(refs[0]["project_id"]), "sound_line", "", 0, 20)
        assert directory["total"] == 3 and directory["items"][0]["id"] == "line_0"
        assert "清晨的风" not in str(directory) and "未选台词正文" not in str(directory)
    _conversation, accepted, _store, claim = selected_task(task_workspace, [refs[0]])
    assert "清晨的风" not in str(claim.inputs)
    assert "未选台词正文" not in str(claim.inputs) and "未选字幕正文" not in str(claim.inputs)
    assert "sound_binding" not in str(accepted.task.selected_subjects)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        result = read_selected_snapshot(
            session,
            1,
            task.context_snapshot,
            ReadSelectedSubjects(ids=[task.context_snapshot["subjects"][0]["subject_key"]]),
        )
        assert "清晨的风" in str(result)
        assert "未选台词正文" not in str(result) and "未选字幕正文" not in str(result)
        with pytest.raises(WorkflowError, match="subject_key"):
            read_selected_snapshot(
                session, 1, task.context_snapshot, ReadSelectedSubjects(ids=["line_2"])
            )
    with factory() as session:
        with pytest.raises(NotFound):
            task_service(session, settings, 2).subject_directory(
                int(refs[0]["project_id"]), "sound_line", "", 0, 20
            )


@pytest.mark.parametrize("lines", [1, 2])
def test_assistant_dialogue_audio_waits_for_native_atomic_multitrack_adoption(
    task_workspace, tmp_path, lines
):
    from uuid import NAMESPACE_URL, uuid5

    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, MediaFile
    from short_drama.domain.project_assembly import ProjectAssembly
    from short_drama.domain.project_sound import ProjectSound, SoundMediaReference

    flow = dialogue_audio_flow(task_workspace, lines=lines, limit=lines)
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "waiting_media", operation.error or operation.result
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-dubbing"
        )
    provider, worker = media_worker(flow, dialogue_audio_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert len(provider.calls) == lines
    assert {call["input"]["text"] for call in provider.calls} == set(
        ("清晨的风吹过窗前。", "今晚我们在桥头见面。")[:lines]
    )
    assert all(call["parameters"] == {"voice": "alloy"} for call in provider.calls)
    assert "未选台词正文" not in str(provider.calls) and "未选字幕正文" not in str(provider.calls)
    assert collect_media_results(flow.factory, flow.settings) == 1
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result or operation.error
        assert len(operation.changes) == lines
        assert all(
            ref["kind"] == "sound_line" and ref["status"] == "adopted"
            for ref in operation.result_refs
        )
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.status == "stopped" and task.applied_control_seq == 1
        assert task.media_usage["audio"] == {"reserved": 0, "submitted": lines, "succeeded": lines}
        sound = session.get(ProjectSound, flow.assembly_id)
        assert sound.row_version == 1 + lines
        assert session.get(ProjectAssembly, flow.assembly_id).row_version == 1 + lines
        assert sound.reviewed_timeline_hash is None
        assert sound.document["dialogue"][2]["text"] == "未选台词正文"
        assert sound.document["subtitles"][0]["text"] == "未选字幕正文"
        assert len(sound.document["audio_clips"]) == lines
        for clip in sound.document["audio_clips"]:
            assert clip["id"] == str(
                uuid5(NAMESPACE_URL, f"assembly:{flow.assembly_id}:dialogue:{clip['dialogue_id']}")
            )
            media = session.get(MediaFile, int(clip["media_id"]))
            assert media.duration_ms > 0 and media.checksum_sha256
            assert session.get(SoundMediaReference, (flow.assembly_id, media.id))
        assert all(
            record.request_data["source_snapshot"]["line_hash"]
            for record in session.scalars(select(AIGenerationRecord))
        )


def change_dialogue_source(flow, change):
    from copy import deepcopy

    from short_drama.domain import Project
    from short_drama.domain.project_assembly import ProjectAssembly
    from short_drama.domain.project_sound import ProjectSound

    if change == "flags":
        flow.settings.audio_production_enabled = False
        return
    with flow.factory.begin() as session:
        sound = session.get(ProjectSound, flow.assembly_id)
        if change == "timeline":
            session.get(ProjectAssembly, flow.assembly_id).row_version += 1
        elif change == "archive":
            session.get(Project, int(flow.reference["project_id"])).archived_at = utcnow()
        else:
            doc = deepcopy(sound.document)
            if change == "delete":
                doc["dialogue"] = doc["dialogue"][1:]
            else:
                doc["dialogue"][0]["text"] = "人工后来的修改"
            sound.document, sound.row_version = doc, sound.row_version + 1


@pytest.mark.parametrize("change", ["edit", "delete", "timeline", "archive", "flags"])
@pytest.mark.parametrize("when", ["claim", "validate", "submit"])
def test_assistant_dialogue_audio_guards_claim_send_and_late_adoption(
    task_workspace, tmp_path, change, when
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain.project_sound import ProjectSound

    flow = dialogue_audio_flow(task_workspace)
    if when == "claim":
        change_dialogue_source(flow, change)
    provider, worker = media_worker(
        flow,
        dialogue_audio_provider(
            tmp_path,
            **(
                {f"before_{when}": lambda: change_dialogue_source(flow, change)}
                if when != "claim"
                else {}
            ),
        ),
    )
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == ("conflict" if when == "submit" else "failed"), operation.result
        assert operation.changes == []
        assert len(provider.calls) == int(when == "submit")
        assert all(
            not line["media_id"]
            for line in session.get(ProjectSound, flow.assembly_id).document["dialogue"]
        )
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.media_usage["audio"]["reserved"] == 0
        assert task.media_usage["audio"]["submitted"] == int(when == "submit")


@pytest.mark.parametrize("case", ["scope", "version", "timeline", "model", "budget", "discussion"])
def test_assistant_dialogue_audio_rejects_untrusted_input_before_native_admission(
    task_workspace, case
):
    from short_drama.domain import AsyncTask

    patch = (
        {"target_id": "line_2"}
        if case == "scope"
        else {"row_version": "2"}
        if case == "version"
        else {"timeline_hash": "a" * 64}
        if case == "timeline"
        else {"model_version": "2"}
        if case == "model"
        else {}
    )
    flow = dialogue_audio_flow(
        task_workspace,
        item_patch=patch,
        **(
            {"limit": 0}
            if case == "budget"
            else {"content": "讨论台词的配音"}
            if case == "discussion"
            else {}
        ),
    )
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed", operation.result
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert operation.error["code"] == (
            "assistant_generation_scope"
            if case == "scope"
            else "assistant_media_budget_exhausted"
            if case == "budget"
            else "assistant_generation_request_required"
            if case == "discussion"
            else "conflict"
        )


def test_assistant_dialogue_audio_adopts_out_of_order_completions_without_staling_other_lines(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain import AsyncTask
    from short_drama.domain.agent_media import AgentMediaRequest
    from short_drama.domain.project_sound import ProjectSound

    flow = dialogue_audio_flow(task_workspace, lines=3)
    provider, worker = media_worker(flow, dialogue_audio_provider(tmp_path))
    with flow.factory() as session:
        native_ids = session.scalars(
            select(AgentMediaRequest.generation_task_id)
            .where(AgentMediaRequest.operation_id == flow.operation_id)
            .order_by(AgentMediaRequest.item_index.desc())
        ).all()
    for index, native_id in enumerate(native_ids):
        for _ in range(6):
            with flow.factory.begin() as session:
                native = session.get(AsyncTask, native_id)
                if native.status in {"succeeded", "failed", "cancelled"}:
                    assert native.status == "succeeded", native.error
                    break
                version = native.message_version
                native.next_run_at = utcnow()
            worker.execute(native_id, version)
        else:
            pytest.fail("Dubbing native task did not finish")
        assert collect_media_results(flow.factory, flow.settings) == int(index == 2)
        with flow.factory() as session:
            assert (
                len(session.get(ProjectSound, flow.assembly_id).document["audio_clips"])
                == index + 1
            )
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.result
        assert len(operation.changes) == 3 and len(provider.calls) == 3
    execute_tools(flow.factory, flow.settings, flow.accepted.run.id)
    with flow.factory() as session:
        from short_drama.agent.media_tools import media_requests_locked

        run = session.get(AgentRun, flow.accepted.run.id)
        version = run.message_version
        requests = media_requests_locked(
            session,
            session.get(AgentConversation, flow.conversation.id),
            session.get(AgentTask, flow.accepted.task.id),
        )
        assert {target["row_version"] for target in requests[0]["targets"]} == {"4"}
    store = AgentRuntimeStore(flow.factory, flow.settings)
    claim = store.claim_execution(flow.accepted.run.id, version, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": "after-dubbing-adoption"})


def test_assistant_dialogue_audio_replacement_keeps_clip_identity_and_original_candidate(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.domain.project_sound import ProjectSound, SoundMediaReference
    from short_drama.service.project_sound_service import ProjectSoundService

    first = dialogue_audio_flow(task_workspace)
    provider, worker = media_worker(first, dialogue_audio_provider(tmp_path))
    drain_assistant_media(first, worker)
    assert collect_media_results(first.factory, first.settings) == 1
    with first.factory() as session:
        sound = session.get(ProjectSound, first.assembly_id)
        original_clip = sound.document["audio_clips"][0]
        reference = {**first.reference, "revision": str(sound.row_version)}
    old_request = first.body["requests"][0]
    second = media_tool_run(
        task_workspace,
        media_kind="audio",
        content="为已选台词生成配音",
        references=[reference],
        targets=[reference],
        identity_key="dubbing-replacement",
        existing_model=SimpleNamespace(id=int(old_request["model_config_id"]), row_version=1),
        item_patch={"target_kind": "dialogue_audio", "media_kind": "audio"},
    )
    _provider, worker = media_worker(
        second, dialogue_audio_provider(tmp_path), storage=worker.storage
    )
    drain_assistant_media(second, worker)
    assert collect_media_results(second.factory, second.settings) == 1
    with second.factory() as session:
        task_service(session, second.settings)
        sound = session.get(ProjectSound, first.assembly_id)
        current_clip = sound.document["audio_clips"][0]
        assert current_clip["id"] == original_clip["id"]
        assert current_clip["media_id"] != original_clip["media_id"]
        assert len(sound.document["audio_clips"]) == 1
        assert session.get(SoundMediaReference, (first.assembly_id, int(original_clip["media_id"])))
    with second.factory() as session:
        task_service(session, second.settings)
        candidates = ProjectSoundService(session, second.settings, worker.storage).candidates(
            reference["project_id"], reference["id"]
        )
        assert len(candidates) == 2
        assert {output["media_id"] for item in candidates for output in item["outputs"]} == {
            original_clip["media_id"],
            current_clip["media_id"],
        }


def test_assistant_dialogue_audio_unknown_preserves_original_request_and_pending_stop(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.ai import GenerationError
    from short_drama.domain import AIGenerationRecord

    flow = dialogue_audio_flow(task_workspace)
    provider = dialogue_audio_provider(tmp_path)

    def unknown(*_args, **_kwargs):
        provider.calls.append("unknown")
        raise GenerationError("timeout", accepted_unknown=True)

    provider.submit = unknown
    _provider, worker = media_worker(flow, provider)
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, "stop-unknown-dubbing"
        )
    assert collect_media_results(flow.factory, flow.settings) == 0
    assert collect_media_results(flow.factory, flow.settings) == 0
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "reconciling" and operation.finished_at is None
        task = session.get(AgentTask, flow.accepted.task.id)
        assert task.control_state == "stop_pending" and task.applied_control_seq == 0
        assert task.media_usage["audio"] == {"reserved": 1, "submitted": 0, "succeeded": 0}
        assert (
            session.scalar(select(AIGenerationRecord)).request_data["source"]["line_id"] == "line_0"
        )
    assert provider.calls == ["unknown"]


@pytest.mark.parametrize(
    "change", ["native_mode", "guessed_version", "timeline_content", "other_sound"]
)
def test_assistant_dialogue_audio_rechecks_frozen_sources_at_admission(task_workspace, change):
    from copy import deepcopy

    from short_drama.dao.assistant_context_dao import AssistantContextDAO
    from short_drama.domain import AsyncTask
    from short_drama.domain.native_voice import ProjectSoundMode

    factory, _settings, _model = task_workspace

    def changed(_accepted, reference):
        with factory.begin() as session:
            assembly, sound = AssistantContextDAO(session).sound_scope(
                int(reference["project_id"]), lock=True
            )
            if change == "native_mode":
                session.add(
                    ProjectSoundMode(
                        project_id=int(reference["project_id"]), mode="native", row_version=1
                    )
                )
            elif change == "timeline_content":
                assembly.aspect = "9:16"
            else:
                document = deepcopy(sound.document)
                if change == "guessed_version":
                    document["dialogue"][0]["text"] = "后来改过的台词"
                else:
                    document["subtitles"][0]["text"] = "后来改过的字幕"
                sound.document = document
                sound.row_version += 1

    flow = dialogue_audio_flow(
        task_workspace,
        before_execute=changed,
        item_patch={"row_version": "2"} if change == "guessed_version" else {},
    )
    with factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "failed" and operation.error["code"] == "conflict"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_assistant_dialogue_audio_finishes_sent_work_after_global_switch_closes(
    task_workspace, tmp_path
):
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.service.assistant_policy_service import AssistantPolicyService

    flow = dialogue_audio_flow(task_workspace)

    def disable():
        with flow.factory() as session:
            task_service(session, flow.settings)
            AssistantPolicyService(session).update_generation_policy(
                {"policy_version": "0", "media_generation_enabled": False}
            )

    provider, worker = media_worker(flow, dialogue_audio_provider(tmp_path, before_submit=disable))
    drain_assistant_media(flow, worker)
    assert collect_media_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded" and operation.result_refs[0]["status"] == "adopted"
        assert len(provider.calls) == 1


def sound_text_revision(workspace, project_id):
    from short_drama.service.assistant_dialogue_service import sound_document_binding

    factory, _settings, _model = workspace
    with factory.begin() as session:
        binding = sound_document_binding(session, project_id)
    return {key: binding[key] for key in ("row_version", "assembly_version", "timeline_hash")}


def test_assistant_sound_text_creates_only_requested_lines_and_preserves_the_multitrack_document(
    task_workspace,
):
    from copy import deepcopy

    from short_drama.domain import AsyncTask
    from short_drama.domain.project_assembly import ProjectAssembly
    from short_drama.domain.project_sound import ProjectSound

    refs, model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, _settings, _text_model = task_workspace
    with factory() as session:
        previous = deepcopy(session.get(ProjectSound, assembly_id).document)
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    patch = {
        "sound_revision": revision,
        "actions": [
            {
                "action": "create_sound_line",
                "key": "opening",
                "values": {
                    "text": "新建台词的正文",
                    "character": "旁白",
                    "voice": "alloy",
                    "config_id": str(model.id),
                },
            },
            {"action": "create_sound_line", "key": "reply", "values": {"text": "请等一下。"}},
            {"action": "update_sound_line", "line": "$opening", "patch": {"voice": "nova"}},
        ],
    }
    _conversation, accepted, claim, operations = standard_tool_run(
        task_workspace,
        "创建两条台词：雨夜敲门",
        [patch, patch],
        project_id=refs[0]["project_id"],
    )
    first, replay = operations
    assert first.status == replay.status == "succeeded", first.error
    assert replay.result["already_applied"] and replay.changes == []
    assert first.result["created_counts"] == {"sound_line": 2}
    assert first.result["sound_revision"]["row_version"] == "2"
    assert "未选台词正文" not in str(claim.inputs) and "未选字幕正文" not in str(claim.inputs)
    assert "新建台词的正文" not in str(first.result)
    assert len(first.result_refs) == 2 and len(first.changes) == 3
    assert all(item["kind"] == "sound_line" for item in first.result_refs)
    with factory() as session:
        sound = session.get(ProjectSound, assembly_id)
        saved = deepcopy(sound.document)
        assert sound.row_version == session.get(ProjectAssembly, assembly_id).row_version == 2
        assert saved["dialogue"][:3] == previous["dialogue"]
        assert saved["dialogue"][3]["voice"] == "nova"
        assert saved["dialogue"][3]["id"] == first.result["temporary_ids"]["opening"]
        assert {key: value for key, value in saved.items() if key != "dialogue"} == {
            key: value for key, value in previous.items() if key != "dialogue"
        }
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.get(AgentTask, accepted.task.id).status == "running"


@pytest.mark.parametrize("existing_sound", [True, False])
def test_assistant_sound_text_creates_with_a_frozen_zero_or_existing_sound_version(
    task_workspace,
    existing_sound,
):
    from short_drama.domain.project_sound import ProjectSound

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, _settings, _text_model = task_workspace
    if not existing_sound:
        with factory.begin() as session:
            session.delete(session.get(ProjectSound, assembly_id))
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    assert revision["row_version"] == ("1" if existing_sound else "0")
    *_rest, operations = standard_tool_run(
        task_workspace,
        "创建一条台词：新开场",
        [
            {
                "sound_revision": revision,
                "actions": [
                    {
                        "action": "create_sound_line",
                        "key": "opening",
                        "values": {"text": "你好。"},
                    }
                ],
            }
        ],
        project_id=refs[0]["project_id"],
    )
    assert operations[0].status == "succeeded", operations[0].error
    with factory() as session:
        assert session.get(ProjectSound, assembly_id).row_version == (2 if existing_sound else 1)


@pytest.mark.parametrize(
    "change",
    ["outside_scope", "field", "guessed_version", "timeline", "native", "disabled", "quota"],
)
def test_assistant_sound_text_rejects_forged_scope_fields_versions_and_ineligible_sources(
    task_workspace,
    change,
):
    from copy import deepcopy

    from short_drama.domain.native_voice import ProjectSoundMode
    from short_drama.domain.project_assembly import ProjectAssembly
    from short_drama.domain.project_sound import ProjectSound

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, settings, _text_model = task_workspace
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    content = "修改已选台词的正文：新的正文"
    action = {"action": "update_sound_line", "line": "line_0", "patch": {"text": "新的正文"}}
    if change == "outside_scope":
        action["line"] = "line_2"
    elif change == "field":
        action["patch"]["voice"] = "nova"
    elif change == "guessed_version":
        revision["row_version"] = "2"
    elif change == "quota":
        content = "创建一条台词"
        action = {"action": "create_sound_line", "key": "one", "values": {"text": "额外创建"}}
    expected = {}

    def change_source(_conversation, _accepted):
        with factory.begin() as session:
            sound = session.get(ProjectSound, assembly_id)
            if change == "guessed_version":
                document = deepcopy(sound.document)
                document["dialogue"][0]["text"] = "人工后来改过的正文"
                sound.document, sound.row_version = document, 2
            elif change == "timeline":
                session.get(ProjectAssembly, assembly_id).aspect = "9:16"
            elif change == "native":
                session.add(
                    ProjectSoundMode(
                        project_id=int(refs[0]["project_id"]), mode="native", row_version=1
                    )
                )
            elif change == "disabled":
                settings.audio_production_enabled = False
            expected["document"] = deepcopy(sound.document)

    *_rest, operations = standard_tool_run(
        task_workspace,
        content,
        [
            {
                "sound_revision": revision,
                "actions": [action, {**action, "key": "two"}] if change == "quota" else [action],
            }
        ],
        references=refs[:1],
        before_execute=change_source,
    )
    assert operations[0].status in {"failed", "conflict"}, operations[0].result
    assert operations[0].changes == [] and operations[0].result_refs == []
    with factory() as session:
        assert session.get(ProjectSound, assembly_id).document == expected["document"]


def test_assistant_sound_text_late_failure_rolls_back_other_created_objects(task_workspace):
    from short_drama.domain import Asset
    from short_drama.domain.project_sound import ProjectSound

    refs, _audio_model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, _settings, text_model = task_workspace
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    with factory() as session:
        original_count = session.scalar(select(func.count()).select_from(Asset))
    *_rest, operations = standard_tool_run(
        task_workspace,
        "创建一个角色和一条台词",
        [
            {
                "sound_revision": revision,
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {"kind": "character", "name": "应整批回滚"},
                    },
                    {
                        "action": "create_sound_line",
                        "key": "opening",
                        "values": {"text": "无效配音模型", "config_id": str(text_model)},
                    },
                ],
            }
        ],
        project_id=refs[0]["project_id"],
    )
    assert operations[0].status == "failed", operations[0].result
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == original_count
        assert session.get(ProjectSound, assembly_id).row_version == 1
        assert operations[0].changes == [] and operations[0].result_refs == []


def test_assistant_sound_text_reads_only_created_saved_lines_and_checks_final_send(task_workspace):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.domain.project_sound import ProjectSound

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, settings, _text_model = task_workspace
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一条台词：新开场",
        [
            {
                "sound_revision": revision,
                "actions": [
                    {
                        "action": "create_sound_line",
                        "key": "opening",
                        "values": {"text": "本任务新建的台词。" * 100},
                    }
                ],
            }
        ],
        project_id=refs[0]["project_id"],
    )
    saved = operations[0]
    with factory() as session:
        owner = session.get(AgentConversation, conversation.id)
        task = session.get(AgentTask, accepted.task.id)
        result = execute_locked(
            session,
            owner,
            task,
            SimpleNamespace(
                tool_name="read_task_results",
                arguments={"operation_id": str(saved.id), "character_limit": 6000},
            ),
        )
        assert result["results"][0]["kind"] == "sound_line"
        assert "本任务新建的台词" in result["results"][0]["content"]
        assert "未选台词正文" not in str(result) and "未选字幕正文" not in str(result)
        with pytest.raises(WorkflowError):
            execute_locked(
                session,
                owner,
                task,
                SimpleNamespace(
                    tool_name="read_task_results",
                    arguments={"operation_id": str(saved.id), "ids": ["line_2"]},
                ),
            )
        version = session.get(AgentRun, accepted.run.id).message_version
    store = AgentRuntimeStore(factory, settings)
    pending = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert pending is not None
    assert "read_task_results" in [item["name"] for item in pending.inputs["kwargs"]["tools"]]
    with factory.begin() as session:
        sound = session.get(ProjectSound, assembly_id)
        sound.document = {**sound.document, "dialogue": sound.document["dialogue"][:3]}
        sound.row_version += 1
    with pytest.raises(AgentGatewayError) as raised:
        store.before_send(pending, {"fixture": "deleted-sound-result"})
    assert raised.value.code == "assistant_subjects_superseded"


def sound_text_followup(flow, content, payload, tool_name="apply_standard_patch"):
    from short_drama.agent.authorization import digest

    key = digest(content)[:16]
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        service.control_task(flow.accepted.task.id, {"kind": "stop"}, f"sound-stop-{key}")
        supplemented = service.send_task_message(
            flow.conversation.id,
            {"task_id": str(flow.accepted.task.id), "content": content},
            f"sound-message-{key}",
        )
    return resume_media_tool(
        flow,
        {
            **payload,
            "request_message_id": str(supplemented.message.id),
            "request_hash": digest({"role": "user", "content": content}),
        },
        tool_name=tool_name,
        control_key=f"sound-resume-{key}",
    )


def test_assistant_sound_text_created_result_can_be_edited_then_dubbed_in_the_same_task(
    task_workspace,
    tmp_path,
):
    from short_drama.agent.assistant_tools import execute_locked
    from short_drama.agent.media_runtime import collect_media_results
    from short_drama.core.config import Settings
    from short_drama.domain import AsyncTask
    from short_drama.domain.project_sound import ProjectSound

    refs, model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, settings, _text_model = task_workspace
    runtime = Settings(_env_file=None)
    for key in (
        "generation_lease_seconds",
        "generation_poll_seconds",
        "generation_archive_budget_seconds",
        "minio_image_bucket",
        "minio_video_bucket",
        "minio_audio_bucket",
        "minio_presign_expiry",
        "render_ffmpeg_path",
        "render_ffprobe_path",
    ):
        setattr(settings, key, getattr(runtime, key))
    conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一条台词：新开场",
        [
            {
                "sound_revision": sound_text_revision(task_workspace, refs[0]["project_id"]),
                "actions": [
                    {
                        "action": "create_sound_line",
                        "key": "opening",
                        "values": {
                            "text": "新建时的台词",
                            "voice": "alloy",
                            "config_id": str(model.id),
                        },
                    }
                ],
            }
        ],
        project_id=refs[0]["project_id"],
    )
    created = operations[0]
    assert created.status == "succeeded", created.error
    line_id = created.result_refs[0]["id"]
    flow = SimpleNamespace(
        factory=factory,
        settings=settings,
        conversation=conversation,
        accepted=accepted,
        reference=refs[0],
        assembly_id=assembly_id,
    )
    edit_id, _claim = sound_text_followup(
        flow,
        "修改本任务台词的正文：更正后的台词",
        {
            "patch": {
                "sound_revision": sound_text_revision(task_workspace, refs[0]["project_id"]),
                "actions": [
                    {
                        "action": "update_sound_line",
                        "line": line_id,
                        "patch": {"text": "更正后的台词"},
                    }
                ],
            },
        },
    )
    with factory() as session:
        edit = session.get(AgentOperation, edit_id)
        assert edit.status == "succeeded", edit.error
        assert edit.result_refs[0]["row_version"] == "3"
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    media_id, claim = sound_text_followup(
        flow,
        "为本任务台词生成配音",
        {
            "requests": [
                {
                    "target_kind": "dialogue_audio",
                    "target_id": line_id,
                    **revision,
                    "model_config_id": str(model.id),
                    "model_version": "1",
                    "media_kind": "audio",
                }
            ]
        },
        tool_name="submit_generation_batch",
    )
    flow.operation_id = media_id
    assert "未选台词正文" not in str(claim.inputs)
    with factory() as session:
        assert session.get(AgentOperation, media_id).status == "waiting_media"
        task_service(session, settings)
        session.rollback()
        task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "sound-final-stop"
        )
    provider, worker = media_worker(flow, dialogue_audio_provider(tmp_path))
    drain_assistant_media(flow, worker)
    assert collect_media_results(factory, settings) == 1
    with factory() as session:
        media = session.get(AgentOperation, media_id)
        assert media.status == "succeeded" and media.result_refs[0]["status"] == "adopted"
        assert media.result_refs[0]["id"] == line_id
        assert session.get(AgentTask, accepted.task.id).status == "stopped"
        sound = session.get(ProjectSound, assembly_id)
        line = next(line for line in sound.document["dialogue"] if line["id"] == line_id)
        assert line["text"] == "更正后的台词" and line["media_id"] is not None
        assert sound.row_version == 4 and len(sound.document["audio_clips"]) == 1
        result = execute_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            SimpleNamespace(
                tool_name="read_task_results",
                arguments={"operation_id": str(created.id)},
            ),
        )
        assert "新建时的台词" in result["results"][0]["content"]
        assert "更正后的台词" not in result["results"][0]["content"]
        assert result["results"][0]["source_changed"]
    assert len(provider.calls) == 1
    assert provider.calls[0]["input"] == {"text": "更正后的台词"}
    with factory() as session:
        from copy import deepcopy

        original_clip = deepcopy(session.get(ProjectSound, assembly_id).document["audio_clips"][0])
    updated_id, _claim = sound_text_followup(
        flow,
        "修改本任务台词的正文：采用后再修改",
        {
            "patch": {
                "sound_revision": sound_text_revision(task_workspace, refs[0]["project_id"]),
                "actions": [
                    {
                        "action": "update_sound_line",
                        "line": line_id,
                        "patch": {"text": "采用后再修改"},
                    }
                ],
            },
        },
    )
    with factory() as session:
        from short_drama.service.project_sound_service import line_hash

        operation = session.get(AgentOperation, updated_id)
        assert operation.status == "succeeded", operation.error
        sound = session.get(ProjectSound, assembly_id)
        current = next(line for line in sound.document["dialogue"] if line["id"] == line_id)
        assert sound.document["audio_clips"] == [original_clip]
        assert current["media_id"] == original_clip["media_id"]
        assert current["adopted_hash"] != line_hash(current)
        assert sound.row_version == 5 and len(provider.calls) == 1


def test_assistant_sound_text_noop_keeps_versions_and_returns_the_real_line(task_workspace):
    from short_drama.domain.project_sound import ProjectSound

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    *_rest, operations = standard_tool_run(
        task_workspace,
        "修改已选台词的正文：保持原正文",
        [
            {
                "sound_revision": sound_text_revision(task_workspace, refs[0]["project_id"]),
                "actions": [
                    {
                        "action": "update_sound_line",
                        "line": "line_0",
                        "patch": {"text": "清晨的风吹过窗前。"},
                    }
                ],
            }
        ],
        references=refs[:1],
    )
    assert operations[0].status == "succeeded", operations[0].error
    assert operations[0].changes == [] and operations[0].result_refs[0]["status"] == "unchanged"
    with task_workspace[0]() as session:
        assert session.get(ProjectSound, assembly_id).row_version == 1


@pytest.mark.parametrize("change", ["guessed_creation_version", "legacy_input", "capacity", "stop"])
def test_assistant_sound_text_creation_rechecks_frozen_input_and_finishes_only_claimed_work(
    task_workspace,
    change,
):
    from copy import deepcopy

    from short_drama.domain.agent_task import AgentTaskInput
    from short_drama.domain.project_sound import ProjectSound

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, settings, _text_model = task_workspace
    if change == "capacity":
        with factory.begin() as session:
            sound = session.get(ProjectSound, assembly_id)
            line = sound.document["dialogue"][0]
            sound.document = {
                **sound.document,
                "dialogue": [{**line, "id": f"full_{index}"} for index in range(300)],
            }
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    if change == "guessed_creation_version":
        revision["row_version"] = "2"

    def change_source(conversation, accepted):
        if change == "stop":
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, "sound-started-stop"
                )
        else:
            with factory.begin() as session:
                if change == "legacy_input":
                    item = session.scalar(
                        select(AgentTaskInput).where(AgentTaskInput.task_id == accepted.task.id)
                    )
                    item.payload = {
                        key: value
                        for key, value in item.payload.items()
                        if key != "sound_write_binding"
                    }
                elif change == "guessed_creation_version":
                    sound = session.get(ProjectSound, assembly_id)
                    document = deepcopy(sound.document)
                    document["subtitles"][0]["text"] = "后来的人工字幕"
                    sound.document, sound.row_version = document, 2

    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一条台词",
        [
            {
                "sound_revision": revision,
                "actions": [
                    {
                        "action": "create_sound_line",
                        "key": "opening",
                        "values": {"text": "工具已领取的台词"},
                    }
                ],
            }
        ],
        project_id=refs[0]["project_id"],
        before_execute=change_source,
    )
    operation = operations[0]
    if change == "stop":
        assert operation.status == "succeeded", operation.error
        with factory() as session:
            assert session.get(AgentTask, accepted.task.id).status == "stopped"
            assert session.get(ProjectSound, assembly_id).row_version == 2
    else:
        assert operation.status in {"conflict", "failed"}, operation.result
        assert operation.changes == [] and operation.result_refs == []
        with factory() as session:
            sound = session.get(ProjectSound, assembly_id)
            assert len(sound.document["dialogue"]) == (300 if change == "capacity" else 3)
            assert sound.row_version == (2 if change == "guessed_creation_version" else 1)
