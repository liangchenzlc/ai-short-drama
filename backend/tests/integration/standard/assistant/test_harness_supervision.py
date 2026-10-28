"""Real MySQL checks for completion gates, replay and bounded task working state."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.harness.runtime import HarnessRuntime
from short_drama.agent.task.contract import Requirement
from short_drama.core.time import utcnow
from short_drama.domain.agent import AgentEvent, AgentMessage, AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.agent_task import AgentOperation, AgentTask
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    subject_assets,
    task_service,
)
from tests.integration.standard.assistant.test_harness_runtime import (
    advance,
    configured,
    context_frame,
    submit_review,
)
from tests.unit.standard.agent.test_agent_model_gateway import provider as provider

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def latest(factory, task_id, tool):
    with factory() as session:
        row = session.scalar(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task_id,
                AgentOperation.tool_name == tool,
            )
            .order_by(AgentOperation.id.desc())
        )
        assert row.status == "succeeded", row.error
        session.expunge(row)
        return row


def test_completion_missing_work_is_rejected_then_original_review_receipt_replays(
    task_workspace,
    provider,
    monkeypatch,
):
    state = configured(task_workspace, provider)
    factory, settings, model = task_workspace
    reference = subject_assets(task_workspace)[0]
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "先读取创作目标目录，再保存两个角色，回读后报告名称。",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
            },
            uuid4().hex,
        )
    runtime = HarnessRuntime(factory, settings)
    requirements = [
        {
            "key": "directory",
            "description": "读取创作目标目录",
            "kind": "read",
            "source_message_id": str(accepted.message.id),
            "source_quote": "读取创作目标目录",
        },
        {
            "key": "characters",
            "description": "保存两个角色",
            "kind": "write",
            "source_message_id": str(accepted.message.id),
            "source_quote": "保存两个角色",
            "minimum_count": 2,
            "artifact_kind": "asset",
            "asset_kind": "character",
        },
        {
            "key": "read",
            "description": "回读",
            "kind": "read",
            "source_message_id": str(accepted.message.id),
            "source_quote": "回读",
        },
    ]
    requirements = [
        Requirement.model_validate(item).model_dump(mode="json") for item in requirements
    ]
    advance(
        runtime,
        accepted,
        state,
        calls=[("requirements", "set_task_requirements", {"requirements": requirements})],
    )
    advance(runtime, accepted, state, text="已经保存并回读两个角色。", finish=False)
    invalid = {
        "intent": "execution",
        "requirements": [
            {**item, "satisfied": True, "evidence_operation_ids": ["999999"]}
            for item in requirements
        ],
    }
    submit_review(runtime, accepted, state, report=invalid)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status not in {"succeeded", "failed"}
        assert run.checkpoint["completion_feedback"]["missing"]
        assert not session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.task_id == accepted.task.id,
                AgentMessage.kind == "reply",
                AgentMessage.role == "assistant",
            )
        )
        assert not session.scalar(
            select(AgentEvent.id).where(
                AgentEvent.run_id == run.id, AgentEvent.event_type == "assistant.delta"
            )
        ), "Unreviewed candidate text must not reach the live event stream"
    advance(runtime, accepted, state, calls=[("directory", "describe_creation_target", {})])
    directory = latest(factory, accepted.task.id, "describe_creation_target")
    enable = advance(
        runtime,
        accepted,
        state,
        calls=[("enable", "enable_tool_group", {"group": "standard_write"})],
    )
    assert "apply_standard_patch" not in {item["function"]["name"] for item in enable["tools"]}
    wire = advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "save",
                "apply_standard_patch",
                {
                    "patch": {
                        "actions": [
                            {
                                "action": "create_asset",
                                "key": f"character-{i}",
                                "values": {"kind": "character", "name": name},
                            }
                            for i, name in enumerate(("林晚", "乘客"))
                        ],
                    }
                },
            )
        ],
    )
    assert "apply_standard_patch" in {item["function"]["name"] for item in wire["tools"]}
    saved = latest(factory, accepted.task.id, "apply_standard_patch")
    advance(
        runtime,
        accepted,
        state,
        calls=[("read", "read_task_results", {"operation_id": str(saved.id)})],
    )
    read = latest(factory, accepted.task.id, "read_task_results")
    advance(
        runtime, accepted, state, text="已保存林晚、乘客，并回读了两个角色的正文。", finish=False
    )
    evidence = {
        "directory": [str(directory.id)],
        "characters": [str(saved.id), str(read.id)],
        "read": [str(read.id)],
    }
    valid = {
        "intent": "execution",
        "requirements": [
            {
                **item,
                "satisfied": True,
                "evidence_operation_ids": evidence[item["key"]],
            }
            for item in requirements
        ],
    }
    original = runtime.store.save_result
    monkeypatch.setattr(
        runtime.store,
        "save_result",
        lambda *args: (_ for _ in ()).throw(RuntimeError("simulated review worker loss")),
    )
    with pytest.raises(RuntimeError, match="database recovery"):
        submit_review(runtime, accepted, state, report=valid)
    review_wire = state["calls"][-1][2]
    # The independent reviewer receives settled evidence, not the executor transcript.
    assert not [message for message in review_wire["messages"] if message["role"] == "tool"]
    evidence = context_frame(review_wire)["evidence"]["read"]
    assert any(
        item["operation_id"] == str(read.id) and "林晚" in item["excerpt"] for item in evidence
    )
    sent = len(state["calls"])
    with factory.begin() as session:
        session.get(AgentRun, accepted.run.id).lease_until = utcnow() - timedelta(seconds=1)
    assert recover(factory, settings) == 1
    monkeypatch.setattr(runtime.store, "save_result", original)
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)
    assert len(state["calls"]) == sent
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert session.get(AgentTask, accepted.task.id).effective_state["task_contract"]["verified"]
        assert not session.scalar(
            select(AgentToolCall.id).where(
                AgentToolCall.run_id == run.id,
                AgentToolCall.tool_name == "submit_completion_review",
            )
        )
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(bool(turn.response.get("internal")) for turn in turns) == 4
        assert run.usage["input_tokens"] == sum(turn.usage.get("input_tokens", 0) for turn in turns)
        assert all("tool_schema_bytes" in turn.usage["context_metrics"] for turn in turns)


def test_working_observation_survives_context_compaction_and_task_resume(
    task_workspace, provider, monkeypatch
):
    import short_drama.agent.context.transcript as transcript
    from tests.integration.standard.assistant.test_harness_runtime import (
        pause_with_truncated_response,
    )

    state = configured(task_workspace, provider)
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    monkeypatch.setattr(transcript, "RECENT_TOOL_TOKENS", 150)
    runtime = HarnessRuntime(factory, settings)
    advance(
        runtime,
        accepted,
        state,
        calls=[("read", "read_conversation_history", {"from_seq": 1, "to_seq": 1})],
    )
    read = latest(factory, accepted.task.id, "read_conversation_history")
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "bad-quote",
                "record_task_observation",
                {
                    "operation_id": str(read.id),
                    "source_quote": "讨论不存在的内容",
                    "note": "无效引文不能成为工作笔记。",
                },
            )
        ],
    )
    with factory() as session:
        failed = session.scalar(
            select(AgentOperation)
            .where(AgentOperation.task_id == accepted.task.id)
            .order_by(AgentOperation.id.desc())
        )
        assert failed.status == "failed"
        assert "讨论镜头节奏" in failed.error["details"]["source_excerpt"]
    wire = advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "note",
                "record_task_observation",
                {
                    "operation_id": str(read.id),
                    "source_quote": "讨论镜头节奏",
                    "note": "当前任务关注镜头节奏。",
                },
            )
        ],
    )
    noted = latest(factory, accepted.task.id, "record_task_observation")
    assert noted.result["recorded"]
    pause_with_truncated_response(runtime, accepted, state)
    with factory() as session:
        accepted = task_service(session, settings).send_task_message(
            conversation.id, {"task_id": accepted.task.id, "content": "继续"}, uuid4().hex
        )
    wire = advance(runtime, accepted, state, text="继续给出节奏建议。")
    frame = context_frame(wire)
    assert frame["working_set"]["items"][0]["operation_id"] == str(read.id)
    assert frame["working_set"]["items"][0]["source_quote"] == "讨论镜头节奏"
    assert "当前任务关注镜头节奏" in json.dumps(frame, ensure_ascii=False)


def test_skill_quota_pagination_ambiguity_release_and_archived_body_reuse(
    task_workspace,
    provider,
    monkeypatch,
):
    import short_drama.agent.skill.progressive as progressive
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill

    state = configured(task_workspace, provider)
    factory, settings, model = task_workspace
    storage = _Storage(factory)
    skills = [
        create_skill(
            (*task_workspace, storage),
            name="同名核对方法" if i < 2 else f"方法{i}",
            slug=f"stage-{i}",
            description="仅在需要核对人物动机和线索因果时加载。" * 10,
            resources=[],
            instructions=f"第{i}阶段核对线索。",
        )
        for i in range(9)
    ]
    monkeypatch.setattr(progressive, "INDEX_TOKENS", 900)
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "使用手选方法，并在需要时切换其他阶段方法。"
                + "阶段线索按顺序核对。" * 6,
                "model_config_id": model,
                "skills": [{"id": skills[0].id, "content_version": skills[0].content_version}],
            },
            uuid4().hex,
        )
    runtime = HarnessRuntime(factory, settings)
    offset, discovered, pages = 0, set(), 0
    while offset is not None:
        advance(
            runtime,
            accepted,
            state,
            calls=[(f"search-{pages}", "search_skills", {"offset": offset})],
        )
        page = latest(factory, accepted.task.id, "search_skills").result
        discovered.update(item["id"] for item in page["items"])
        assert page["items"] and page["estimated_tokens"] <= 900
        assert page["next_offset"] is None or page["next_offset"] > offset
        offset, pages = page["next_offset"], pages + 1
        assert pages <= len(skills)
    assert pages > 1 and {str(skill.id) for skill in skills} <= discovered

    def load(skill, name=None):
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    uuid4().hex,
                    "load_skill",
                    {
                        "name": name or str(skill.id),
                        "content_version": str(skill.content_version),
                        "purpose": "当前阶段需要核对规则",
                    },
                )
            ],
        )

    load(skills[0])
    manual = latest(factory, accepted.task.id, "load_skill")
    load(skills[0], skills[0].name)
    with factory() as session:
        ambiguous = session.scalar(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == accepted.task.id,
            )
            .order_by(AgentOperation.id.desc())
        )
        assert ambiguous.error["code"] == "assistant_skill_ambiguous"
    advance(
        runtime,
        accepted,
        state,
        calls=[
            (
                "manual-release",
                "release_skill",
                {
                    "activation_id": manual.result["activation_id"],
                },
            )
        ],
    )
    with factory() as session:
        assert (
            session.scalar(
                select(AgentOperation.status)
                .where(
                    AgentOperation.task_id == accepted.task.id,
                )
                .order_by(AgentOperation.id.desc())
            )
            == "failed"
        )
    load(skills[1])
    first = latest(factory, accepted.task.id, "load_skill")
    for index in range(34):
        current = latest(factory, accepted.task.id, "load_skill")
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"release-{index}",
                    "release_skill",
                    {
                        "activation_id": current.result["activation_id"],
                    },
                )
            ],
        )
        # Stage release is a real state transition; unique work evidence keeps
        # this capacity exercise independent of the repeated-call supervisor.
        advance(
            runtime,
            accepted,
            state,
            calls=[
                (
                    f"page-{index}",
                    "read_conversation_history",
                    {
                        "from_seq": 1,
                        "to_seq": 1,
                        "character_offset": index,
                        "character_limit": 1,
                    },
                )
            ],
        )
        load(skills[1])
        reloaded = latest(factory, accepted.task.id, "load_skill")
        assert reloaded.result["body_operation_id"] == str(first.id)
        assert "instructions" not in reloaded.result
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        loaded = session.scalars(
            select(AgentOperation.id).where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == "load_skill",
                AgentOperation.status == "succeeded",
            )
        ).all()
        assert len(loaded) == 36, "All 34 release/reactivation cycles must actually execute"
        assert len(task.effective_state["skill_activations"]) <= 24
        assert (
            len([item for item in task.effective_state["skill_activations"] if item["active"]]) == 2
        )
        bodies = session.scalars(
            select(AgentOperation.result["instructions"]).where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == "load_skill",
                AgentOperation.result["instructions"].is_not(None),
            )
        ).all()
        assert len(bodies) == 2
        from short_drama.dao.agent_transcript_dao import AgentTranscriptDAO

        run = session.get(AgentRun, accepted.run.id)
        dao = AgentTranscriptDAO(session)
        assert len(dao.settled_turns(run.id, task_id=task.id, limit=5)) == 5
        original_turn = session.get(AgentToolCall, first.tool_call_id).turn_id
        exact = dao.settled_turns(run.id, task_id=task.id, turn_ids=[original_turn])
        assert [row[0] for row in exact] == [original_turn]


def test_capacity_patch_preserves_omitted_value_and_can_clear_declaration(task_workspace):
    from short_drama.service.agent_model_service import AgentModelService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, settings, model = task_workspace
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AgentModelService(session, settings)
        first = service.patch_inputs(
            model,
            {"row_version": "1", "image": True, "audio": False, "context_window_tokens": 131072},
        )
        assert first.input_capabilities.context_window_tokens == 131072
        assert first.input_capabilities.context_window_evidence == "declared"
        second = service.patch_inputs(
            model, {"row_version": first.row_version, "image": True, "audio": False}
        )
        assert second.input_capabilities.context_window_tokens == 131072
        cleared = service.patch_inputs(
            model,
            {
                "row_version": second.row_version,
                "image": True,
                "audio": False,
                "context_window_tokens": None,
            },
        )
        assert cleared.input_capabilities.context_window_tokens == 65536
        assert cleared.input_capabilities.context_window_evidence == "platform_default"


def test_bounded_transcript_sorts_ids_without_json_predicates(task_workspace, provider):
    from sqlalchemy import event

    from short_drama.agent.memory.sources import message_sources
    from short_drama.dao.agent_transcript_dao import AgentTranscriptDAO

    state = configured(task_workspace, provider)
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    runtime = HarnessRuntime(factory, settings)
    advance(
        runtime,
        accepted,
        state,
        calls=[("read", "get_generation_policy", {})],
        text="已读取当前生成规则。",
    )
    with factory.begin() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id))
        turn.request_messages = [{"opaque_frozen_request": "x" * 600000}]
        turn.response = {**turn.response, "opaque_padding": "y" * 600000}
    with factory() as session:
        session.connection().exec_driver_sql("SET SESSION sort_buffer_size = 262144")
        statements = []
        event.listen(
            session, "do_orm_execute", lambda event: statements.append(str(event.statement))
        )
        rows = AgentTranscriptDAO(session).settled_turns(accepted.run.id, task_id=accepted.task.id)
        assert len(rows) == 1
        assert AgentTranscriptDAO(session).latest_turn(accepted.run.id).id == turn.id
        sources = message_sources(session, conversation.id, 1, 100)
        assert any(row["id"] == str(accepted.message.id) for row in sources)
        operation_queries = [query for query in statements if "FROM agent_operations" in query]
        assert operation_queries and all(
            "agent_operations.result" not in query and "agent_operations.changes" not in query
            for query in operation_queries
        ), "L1–L5 provenance must not load full operation bodies"
        sorted_queries = [query for query in statements if "ORDER BY" in query]
        assert sorted_queries and all(
            "agent_turns.response" not in query and "agent_turns.request_messages" not in query
            for query in sorted_queries
        )


def test_old_frozen_turn_replays_without_adding_review_or_external_request(
    task_workspace,
    provider,
    monkeypatch,
):
    import asyncio

    from short_drama.agent.context.assembly import prepare_decision
    from tests.unit.standard.agent.test_agent_model_gateway import sse_response

    state = configured(task_workspace, provider)
    factory, settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    runtime = HarnessRuntime(factory, settings)

    def prepare_old(session, conversation, run):
        values = prepare_decision(session, conversation, run)
        values.pop("supervision_version")
        values.pop("context_layout_version")
        return {
            **values,
            "instructions": "历史冻结讨论",
            "tools": [],
            "prompt_version": "assistant-harness-v3",
            "tool_version": 2,
        }

    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    claim = runtime.store.claim_execution(accepted.run.id, version, prepare_old)
    state["responses"] = [(200, sse_response("chat", text="历史回执答复"), "text/event-stream")]
    original = runtime.store.save_result

    def crash(*args):
        raise RuntimeError("simulated receipt loss")

    monkeypatch.setattr(runtime.store, "save_result", crash)
    with pytest.raises(RuntimeError, match="simulated receipt loss"):
        asyncio.run(runtime._decision(claim))
    with factory.begin() as session:
        session.get(AgentRun, accepted.run.id).lease_until = utcnow() - timedelta(seconds=1)
    assert recover(factory, settings) == 1
    monkeypatch.setattr(runtime.store, "save_result", original)
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)
    assert len(state["calls"]) == 1
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert len(turns) == 1 and "internal" not in turns[0].response
