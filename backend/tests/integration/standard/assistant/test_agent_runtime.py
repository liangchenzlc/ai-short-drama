"""One production execute owns the complete graph; only provider HTTP is controlled."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.core.state import failure_message
from short_drama.agent.runtime import executor
from short_drama.core.time import utcnow
from short_drama.domain.assistant.conversations.models import AgentEvent, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    new_task,
    task_service,
)
from tests.support.assistant_runtime import RuntimeTransport, WorkerLost, calls

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def runtime_for(factory, settings, transport):
    implementation = getattr(executor, "AgentRuntime", None)
    assert implementation is not None, "Worker 缺少连续执行 AgentRuntime.execute 入口"
    return implementation(factory, settings, transport=transport)


def source_payload(body):
    payloads = []
    for message in body["messages"]:
        content = message.get("content", "")
        if message["role"] != "user" or not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except ValueError:
            continue
        if isinstance(payload, dict) and "task_user_sources" in payload:
            payloads.append(payload)
    assert len(payloads) == 1, "模型请求缺少唯一规范用户来源"
    return payloads[0]


def test_runtime_summarizes_against_bound_schema_budget_and_keeps_active_skill(
    task_workspace, monkeypatch
):
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.domain import AIModelConfig
    from short_drama.service.ai_config.management.assistant_models import AgentModelService
    from short_drama.service.projects.management.project import ProjectService
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, settings, model_id = task_workspace
    item = create_skill(
        (*task_workspace, _Storage(factory)),
        instructions="ACTIVE_SCHEMA_BUDGET_SKILL：核对实际策略，保留排演线索。",
        resources=[],
    )
    with factory() as session:
        session.info["actor"] = task_actor()
        version = session.get(AIModelConfig, model_id).row_version
        session.rollback()
        AgentModelService(session, settings).patch_inputs(
            model_id,
            {
                "row_version": str(version),
                "image": True,
                "audio": False,
                "context_window_tokens": 32768,
            },
        )
    with factory() as session:
        session.info["actor"] = task_actor()
        project = ProjectService(session).create_project({"name": "实际绑定工具预算"})
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "讨论镜头节奏",
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )
    rounds = 0

    def reply(body):
        nonlocal rounds
        if not body.get("tools"):
            return {"text": "历史排演已核对；保留已保存工具回执，继续下一阶段。"}
        rounds += 1
        if rounds == 1:
            return calls(
                (
                    "load",
                    "runtime__load_skill",
                    {
                        "name": item.name,
                        "content_version": str(item.content_version),
                        "purpose": "保留方法并核对真实策略",
                    },
                )
            )
        if rounds <= 5:
            return {
                **calls((f"policy-{rounds}", "runtime__get_generation_policy", {})),
                "text": "已完成的排演过程可压缩。" * 400,
            }
        return {"text": "已保留活动方法并完成实际策略核对"}

    names = [
        "runtime__read_resource",
        "runtime__read_selected_subjects",
        "runtime__describe_creation_target",
        "runtime__get_generation_policy",
        "workbench__read_current_script",
        "workbench__apply_standard_patch",
        "runtime__read_task_results",
        "runtime__read_conversation_history",
        "runtime__record_task_observation",
    ]
    transport = RuntimeTransport([reply] * 24, selections=[names] * 12)
    bodies, original = [], RunModelLedger.before_send

    def capture(ledger, request):
        if request["source"] == "assistant":
            bodies.append(request["body"])
        return original(ledger, request)

    monkeypatch.setattr(RunModelLedger, "before_send", capture)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(turn.usage["source"] == "summarization" for turn in turns) >= 2
        assert sum(bool((turn.response or {}).get("graph_compaction")) for turn in turns) >= 2
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        assert run.usage["output_tokens"] == 3 * len(turns)
        assert run.usage["reserved_output_tokens"] == 0
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert len(operations) == 5 and all(row.status == "succeeded" for row in operations)
    assert rounds == len(bodies) == 6
    assert "ACTIVE_SCHEMA_BUDGET_SKILL" in json.dumps(bodies[-1], ensure_ascii=False)
    assert all(
        "讨论镜头节奏" in json.dumps(body, ensure_ascii=False)
        for body in transport.selection_requests
    )


def test_runtime_large_l3_groups_compact_twice_and_read_original_cursor(
    task_workspace, monkeypatch
):
    from threading import local

    from sqlalchemy import event

    import short_drama.agent.skill.runtime as skill_runtime
    from short_drama.service.skills.catalog.skill import AgentSkillService
    from tests.integration.standard.assistant.test_agent_runtime_live import long_materials
    from tests.integration.standard.assistant.test_assistant_skills import create_skill
    from tests.support.agent_runtime_live import ControlledStorage
    from tests.support.assistant_runtime import tool_result

    factory, settings, model_id = task_workspace
    settings.minio_image_bucket = "controlled-large-summary"
    storage, reader, io_connections = ControlledStorage(), local(), []

    def read_bytes(configured, manifest):
        # 其它并行调用可持连接；当前读取线程必须先结束自己的短事务。
        assert getattr(reader, "connections", 0) == 0
        io_connections.append(factory.kw["bind"].pool.checkedout())
        return AgentSkillService(None, configured, storage)._resource_bytes(manifest)

    monkeypatch.setattr(skill_runtime, "read_resource_bytes", read_bytes)
    scenes = long_materials()
    skills = [
        create_skill(
            (*task_workspace, storage),
            name=f"大组排演资料{index}",
            slug=f"large-summary-{index}",
            instructions="真实读取资料，保留前幕ID和游标；原目标为核对排演证据。",
            resources=[
                {"path": f"references/scene-{number:02d}.md", "content": scene["content"]}
                for number, scene in enumerate(scenes)
                if number // 21 == index
            ],
        )
        for index in range(2)
    ]
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "原目标：核对两份排演资料，压缩后仍按真实游标读回。",
                "model_config_id": model_id,
            },
            uuid4().hex,
        )
    mains = []

    def reply(body):
        if not body.get("tools"):
            return {"text": "已核对排演证据；后续按方法清单和原游标重新读回。"}
        mains.append(body)
        number = len(mains)
        if number == 1:
            return calls(
                *[
                    (
                        f"load-{index}",
                        "runtime__load_skill",
                        {"name": skill.name, "content_version": "1", "purpose": "核对排演资料"},
                    )
                    for index, skill in enumerate(skills)
                ]
            )
        if number in {2, 3}:
            index = number - 2
            activation = tool_result(body, f"load-{index}")["activation_id"]
            if number == 3:
                with factory() as session:
                    task_service(session, settings).send_task_message(
                        conversation.id,
                        {
                            "task_id": accepted.task.id,
                            "content": "最新更正：最终重新核对第一份第0幕的100字符游标。",
                        },
                        uuid4().hex,
                    )
            return calls(
                *[
                    (
                        f"read-{index}-{item}",
                        "runtime__read_skill_resource",
                        {
                            "activation_id": activation,
                            "path": f"references/scene-{index * 21 + item:02d}.md",
                            "offset": 0,
                            "limit": 4000,
                        },
                    )
                    for item in range(16)
                ]
            )
        if number == 4:
            return calls(
                (
                    "read-again",
                    "runtime__read_skill_resource",
                    {
                        "activation_id": tool_result(body, "load-0")["activation_id"],
                        "path": "references/scene-00.md",
                        "offset": 100,
                        "limit": 200,
                    },
                )
            )
        assert tool_result(body, "read-again")["content"] == scenes[0]["content"][100:300]
        assert tool_result(body, "read-again")["next_offset"] == 300
        return {"text": "已按最新更正核对真实游标。"}

    def checked_out(_connection, _record, _proxy):
        reader.connections = getattr(reader, "connections", 0) + 1

    def checked_in(_connection, _record):
        reader.connections -= 1

    engine = factory.kw["bind"]
    event.listen(engine, "checkout", checked_out)
    event.listen(engine, "checkin", checked_in)
    transport = RuntimeTransport([reply] * 24)
    try:
        runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    finally:
        event.remove(engine, "checkout", checked_out)
        event.remove(engine, "checkin", checked_in)
    assert len(io_connections) == 33
    assert engine.pool.checkedout() == 0
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        compactions = [turn for turn in turns if (turn.response or {}).get("graph_compaction")]
        assert len(compactions) >= 2
        assert len({turn.id for turn in compactions}) == len(compactions)
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert len(operations) == 35 and all(row.status == "succeeded" for row in operations)
        for turn in compactions:
            messages = turn.response["graph_compaction"]
            assert any(
                item["data"]["additional_kwargs"].get("lc_source") == "summarization"
                for item in messages
            )
    assert len(mains) == 5
    assert "最新更正" in json.dumps(mains[-1], ensure_ascii=False)
    for body in mains[2:]:
        for index in range(2):
            assert tool_result(body, f"load-{index}")["instructions"] == skills[index].instructions


def test_summary_command_replays_after_durable_model_receipt(task_workspace, monkeypatch):
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original_middleware, original_result = (
        executor.assistant_middleware,
        AgentToolExecutionService.model_result,
    )
    original_stream, saved = executor.stream_run, []

    def compact(**kwargs):
        middleware = original_middleware(**kwargs)
        middleware[1] = SkillSummarizationMiddleware(
            model=kwargs["model"], trigger=("messages", 3), keep=("messages", 1)
        )
        return middleware

    async def capture(*args, **kwargs):
        result = await original_stream(*args, **kwargs)
        saved.append(result)
        return result

    def crash(service, signature, message):
        original_result(service, signature, message)
        if message.text == "摘要后正式交付":
            raise WorkerLost("lost after compacted model receipt before graph command")

    def route(body):
        if not body.get("tools"):
            return {"text": "唯一一次已持久化的摘要"}
        return (
            calls(("policy", "runtime__get_generation_policy", {}))
            if not any(message["role"] == "tool" for message in body["messages"])
            else {"text": "摘要后正式交付"}
        )

    transport = RuntimeTransport([route] * 8)
    runtime = runtime_for(factory, settings, transport)
    monkeypatch.setattr(executor, "assistant_middleware", compact)
    monkeypatch.setattr(executor, "stream_run", capture)
    monkeypatch.setattr(AgentToolExecutionService, "model_result", crash)
    with pytest.raises(WorkerLost):
        runtime.execute(accepted.run.id, 1)
    monkeypatch.setattr(AgentToolExecutionService, "model_result", original_result)
    version = expire(task_workspace, accepted)
    runtime.execute(accepted.run.id, version)
    assert any(
        message.additional_kwargs.get("lc_source") == "summarization"
        for message in saved[-1].values["messages"]
    )
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(turn.usage["source"] == "summarization" for turn in turns) == 1
        assert sum(turn.usage["source"] == "assistant" for turn in turns) == 2
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        assert (
            len(
                session.scalars(select(AgentOperation).where(AgentOperation.run_id == run.id)).all()
            )
            == 1
        )


def test_selector_invalid_name_retries_with_real_usage_before_main(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport(
        [calls(("policy", "runtime__get_generation_policy", {})), {"text": "实际核对完成"}],
        selections=[
            ["workbench__read_selected_subjects"],
            ["runtime__get_generation_policy"],
            [],
        ],
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(turn.usage["source"] == "tool_selection" for turn in turns) == 3
        assert sum(turn.usage["source"] == "assistant" for turn in turns) == 2
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests) == 5
        assert run.usage["output_tokens"] == 3 * len(turns)
        assert run.usage["reserved_output_tokens"] == 0
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert len(operations) == 1 and operations[0].status == "succeeded"
    assert all(
        "workbench__read_selected_subjects" not in json.dumps(body.get("tools", []))
        for body in transport.main_requests
    )


def test_selector_invalid_name_exhaustion_is_terminal_without_main(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport([], selections=[["workbench__read_selected_subjects"]] * 8)
    runtime = runtime_for(factory, settings, transport)
    runtime.execute(accepted.run.id, 1)
    runtime.execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "failed" and run.error["code"] == "invalid_agent_tool_selection"
        assert run.lease_token is None and run.message_status == "idle"
        assert 1 < run.usage["decision_calls"] <= 3
        assert run.usage["decision_calls"] == len(transport.requests)
        assert run.usage["output_tokens"] == 3 * len(transport.requests)
        assert run.usage["reserved_output_tokens"] == 0
        assert not session.scalar(select(AgentOperation.id).where(AgentOperation.run_id == run.id))
    assert not transport.main_requests


def test_selector_invalid_name_child_retry_keeps_usage_in_child_scope(task_workspace):
    from short_drama.domain.assistant.execution.scope import AgentExecutionScope
    from tests.integration.standard.assistant.test_agent_subtasks import delegation

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport(
        [
            calls(delegation("child")),
            calls(("policy", "runtime__get_generation_policy", {})),
            {"text": "子范围完成核验"},
            {"text": "父范围整合完成"},
        ],
        selections=[
            [],
            ["workbench__read_selected_subjects"],
            ["runtime__get_generation_policy"],
            [],
            [],
        ],
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == run.id)
        ).all()
        child = next(scope for scope in scopes if scope.parent_scope_id is not None)
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert (
            sum(
                turn.scope_id == child.id and turn.usage["source"] == "tool_selection"
                for turn in turns
            )
            == 3
        )
        assert (
            sum(turn.scope_id == child.id and turn.usage["source"] == "assistant" for turn in turns)
            == 2
        )
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests) == 9
        assert run.usage["output_tokens"] == 27 and run.usage["reserved_output_tokens"] == 0


def test_selector_retry_stops_after_first_settled_invalid_selection(task_workspace, monkeypatch):
    from short_drama.agent.runtime.model_ledger import RunModelLedger

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original, settled = RunModelLedger.settle_usage, []

    def stop(ledger, payload):
        original(ledger, payload)
        if not settled and payload["source"] == "tool_selection":
            settled.append(payload)
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, "stop-selector-retry"
                )

    monkeypatch.setattr(RunModelLedger, "settle_usage", stop)
    transport = RuntimeTransport([], selections=[["workbench__read_selected_subjects"]] * 8)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled" and run.lease_token is None
        assert run.usage["decision_calls"] == len(transport.requests) == 1
        assert run.usage["output_tokens"] == 3 and run.usage["reserved_output_tokens"] == 0
        assert not session.scalar(select(AgentOperation.id).where(AgentOperation.run_id == run.id))


@pytest.mark.parametrize(
    "after_summary,budget_variant",
    [
        (False, "actual"),
        (True, "actual"),
        (False, "invalid_values"),
        (False, "invalid_enums"),
        (False, "invalid_container"),
    ],
    ids=["fixed", "post_summary", "invalid_values", "invalid_enums", "invalid_container"],
)
def test_32k_context_rejection_persists_safe_facts_and_sent_usage(
    task_workspace, monkeypatch, after_summary, budget_variant
):
    from short_drama.domain import AIModelConfig
    from short_drama.service.ai_config.management.assistant_models import AgentModelService
    from short_drama.service.assistant.execution.run import AgentRunService
    from short_drama.service.media.files.storage import StorageService
    from short_drama.service.projects.management.project import ProjectService
    from tests.integration.standard.assistant.test_agent_trace import TraceStorage
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, settings, model_id = task_workspace
    settings.minio_image_bucket, settings.minio_video_bucket, settings.minio_audio_bucket = (
        "static-images",
        "static-videos",
        "static-audio",
    )
    adapter = executor.AgentEventAdapter
    storage = StorageService(TraceStorage(factory), settings)
    monkeypatch.setattr(
        executor, "AgentEventAdapter", lambda context: adapter(context, storage=storage)
    )
    item = create_skill(
        (*task_workspace, _Storage(factory)),
        instructions=(
            "预算回归方法：保留完整正文。" * 200 if after_summary else "长方法必须完整保存。" * 700
        ),
        resources=[],
    )
    with factory() as session:
        session.info["actor"] = task_actor()
        version = session.get(AIModelConfig, model_id).row_version
        session.rollback()
        AgentModelService(session, settings).patch_inputs(
            model_id,
            {
                "row_version": str(version),
                "image": True,
                "audio": False,
                "context_window_tokens": 32768,
            },
        )
    with factory() as session:
        session.info["actor"] = task_actor()
        project = ProjectService(session).create_project({"name": "不可压缩静态包络"})
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "讨论镜头节奏",
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )
    names = [
        "runtime__read_resource",
        "runtime__read_selected_subjects",
        "runtime__describe_creation_target",
        "runtime__get_generation_policy",
        "workbench__read_current_script",
        "workbench__apply_standard_patch",
        "runtime__read_task_results",
        "runtime__read_conversation_history",
        "runtime__record_task_observation",
    ]
    rounds = 0

    def reply(body):
        nonlocal rounds
        if not body.get("tools"):
            return {"text": "已核对历史并保留证据。" * 600}
        rounds += 1
        if rounds == 1:
            return calls(
                (
                    "load",
                    "runtime__load_skill",
                    {
                        "name": item.name,
                        "content_version": str(item.content_version),
                        "purpose": "完整保留方法",
                    },
                )
            )
        return {
            **calls((f"policy-{rounds}", "runtime__get_generation_policy", {})),
            "text": "已完成的排演过程可压缩。" * 400,
        }

    observed, original_finish = {}, AgentRunService.finish

    def finish(service, claim, **kwargs):
        error = kwargs.get("error")
        if error and error["code"] == "agent_context_budget_exhausted":
            observed["facts"] = deepcopy(error["context_budget"])
            if budget_variant == "invalid_values":
                error["context_budget"] = {
                    "phase": "helper",
                    "source": "summarization",
                    "fixed_kind": "helper_prompt_and_prior_summary",
                    "context_window_tokens": 0,
                    "covered_characters": 0,
                    "total_characters": 21,
                    "input_limit_tokens": False,
                    "reserved_output_tokens": -1,
                    "reserved_tool_tokens": "12",
                    "reserved_safety_tokens": {"text": "KEY_CANARY"},
                    "estimated_input_tokens": ["PRIVATE_BODY_CANARY"],
                    "fixed_estimated_input_tokens": None,
                    "helper_input_limit_tokens": 0.5,
                    "message_count": True,
                    "tool_call_count": -2,
                    "tool_result_count": "3",
                    "complete_tool_group_count": [],
                    "details": {"provider": "KEY_CANARY"},
                    "messages": "PRIVATE_BODY_CANARY",
                    "url": "https://private.invalid/KEY_CANARY",
                    "unknown": "KEY_CANARY",
                }
            elif budget_variant == "invalid_enums":
                error["context_budget"] = {
                    "phase": {"text": "KEY_CANARY"},
                    "source": "https://private.invalid",
                    "fixed_kind": ["PRIVATE_BODY_CANARY"],
                    "context_window_tokens": 32768,
                }
            elif budget_variant == "invalid_container":
                error["context_budget"] = ["KEY_CANARY", {"details": "PRIVATE_BODY_CANARY"}]
        return original_finish(service, claim, **kwargs)

    monkeypatch.setattr(AgentRunService, "finish", finish)
    transport = RuntimeTransport([reply] * 12, selections=[names] * 8)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "failed" and run.error["code"] == "agent_context_budget_exhausted"
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        helpers = [turn for turn in turns if turn.usage["source"] == "summarization"]
        assert bool(helpers) is after_summary, observed
        assert all(turn.status == "succeeded" for turn in turns)
        assert sum(turn.usage["source"] == "assistant" for turn in turns) == (
            2 if after_summary else 1
        )
        operation = session.scalar(
            select(AgentOperation)
            .where(AgentOperation.run_id == run.id)
            .order_by(AgentOperation.id)
        )
        assert operation.status == "succeeded" and item.instructions in json.dumps(
            operation.result, ensure_ascii=False
        )
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        assert run.usage["reserved_output_tokens"] == 0
        assert run.usage["output_tokens"] == sum(
            turn.usage["provider_usage"]["output_tokens"] for turn in turns
        )
        assert observed["facts"]["phase"] == ("post_summary" if after_summary else "fixed")
        assert observed["facts"]["source"] == "assistant"
        assert observed["facts"]["estimated_input_tokens"] > observed["facts"]["input_limit_tokens"]
        if after_summary:
            assert run.usage["last_context_budget"]["estimated_input_tokens"] <= 18432
        else:
            assert len(turns) == 3
        expected = {
            "actual": observed["facts"],
            "invalid_values": {
                "phase": "helper",
                "source": "summarization",
                "fixed_kind": "helper_prompt_and_prior_summary",
                "context_window_tokens": 0,
                "covered_characters": 0,
                "total_characters": 21,
            },
            "invalid_enums": {"context_window_tokens": 32768},
            "invalid_container": None,
        }[budget_variant]
        assert run.error.get("context_budget") == expected
        finished = session.scalars(
            select(AgentEvent).where(
                AgentEvent.run_id == run.id, AgentEvent.event_type == "run.finished"
            )
        ).one()
        assert finished.payload["error"] == run.error
        saved_error = deepcopy(run.error)
        serialized = json.dumps(finished.payload, ensure_ascii=False)
        assert all(marker not in serialized for marker in ["CANARY", "private.invalid"])
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).error == saved_error
    with factory() as session:
        session.info["actor"] = task_actor()
        assert AgentRunService(session, settings).get_run(accepted.run.id).error == {
            "code": "agent_context_budget_exhausted",
            "message": failure_message("agent_context_budget_exhausted"),
        }


@pytest.mark.parametrize("late_request", [False, True])
def test_completed_run_rejects_a_new_stop_after_the_completion_boundary(
    task_workspace, monkeypatch, late_request
):
    from short_drama.core.exceptions import WorkflowError
    from short_drama.domain.assistant.tasks.task import AgentTaskControl
    from short_drama.service.assistant.tasks.controls import TaskControlOperations

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    entered, completed = Event(), Event()
    original = TaskControlOperations._task

    def wait_for_completion(service, identifier):
        entered.set()
        assert completed.wait(20)
        return original(service, identifier)

    def stop():
        with factory() as session, pytest.raises(WorkflowError) as rejected:
            task_service(session, settings).control_task(
                accepted.task.id, {"kind": "stop"}, "completed-new-stop"
            )
        assert rejected.value.code == "assistant_task_finished"
        assert rejected.value.status_code == 409

    runtime = runtime_for(factory, settings, RuntimeTransport([{"text": "正式完成"}]))
    if late_request:
        monkeypatch.setattr(TaskControlOperations, "_task", wait_for_completion)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(stop)
            try:
                assert entered.wait(10)
                runtime.execute(accepted.run.id, 1)
            finally:
                completed.set()
            future.result(timeout=10)
    else:
        runtime.execute(accepted.run.id, 1)
        stop()
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        run = session.get(AgentRun, accepted.run.id)
        assert task.status == run.status == "succeeded"
        assert task.control_seq == 0 and task.control_state == "none"
        assert not session.scalar(
            select(AgentTaskControl.id).where(AgentTaskControl.task_id == task.id)
        )


def test_accepted_stop_replays_after_a_later_run_succeeds(task_workspace):
    from short_drama.domain.assistant.tasks.task import AgentTaskControl

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "original-stop")
        continued = service.send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "继续核对后完成"},
            "continue-before-stop-replay",
        )
    runtime_for(factory, settings, RuntimeTransport([{"text": "后续执行已完成"}])).execute(
        continued.run.id, 1
    )
    with factory() as session:
        replay = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "original-stop"
        )
        assert replay.control_id == stopped.control_id
        assert replay.task.status == replay.run.status == "succeeded"
        assert replay.task.control_seq == 1
        assert (
            len(
                session.scalars(
                    select(AgentTaskControl).where(AgentTaskControl.task_id == accepted.task.id)
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize("kind", ["resume", "redirect"])
def test_retired_controls_cannot_recreate_runs_when_engine_marker_is_absent(task_workspace, kind):
    from pydantic import ValidationError

    _, accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        stopped = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "stop-old-controls"
        )
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        task.effective_state = {
            key: value for key, value in task.effective_state.items() if key != "engine"
        }
    with factory() as session:
        with pytest.raises(ValidationError) as rejected:
            task_service(session, settings).control_task(
                accepted.task.id,
                {
                    "kind": kind,
                    **(
                        {"expected_control_seq": str(stopped.task.control_seq)}
                        if kind == "resume"
                        else {"content": "这条旧控制不能创建执行"}
                    ),
                },
                "retired-control",
            )
        assert rejected.value.errors()[0]["type"] == "literal_error"
    with factory() as session:
        assert len(session.scalars(select(AgentRun)).all()) == 1
        assert len(session.scalars(select(AgentMessage)).all()) == 1
        assert session.get(AgentTask, accepted.task.id).status == "stopped"


@pytest.mark.parametrize("crash", [False, True])
def test_acceptance_runner_uses_separate_worker_process_and_memory_only_key(
    task_workspace, tmp_path, monkeypatch, crash
):
    from types import SimpleNamespace

    from opensandbox.models.sandboxes import NetworkPolicy
    from pydantic import SecretStr
    from sqlalchemy import func

    from short_drama.core.config import Settings
    from short_drama.domain import AIModelConfig, Asset
    from short_drama.service.ai_config.management.models import AIModelConfigService
    from tests.support.agent_runtime_live import LiveCase

    factory, _settings, model_id = task_workspace
    settings = Settings(_env_file=None, auth_enabled=True, agent_enabled=True)
    with factory() as session:
        task_service(session, settings)
        AIModelConfigService(session).update(
            model_id,
            {
                "row_version": "1",
                "model_key": "gpt-6.1-terra",
                "base_url": "https://t8api.com/v1",
                "runtime_profile": {
                    "version": 1,
                    "api_format": "openai",
                    "protocol": "chat-completion",
                },
            },
        )
    case = LiveCase(
        factory=factory,
        settings=settings,
        model_id=model_id,
        root="https://t8api.com",
        model="gpt-6.1-terra",
        storage=None,
        objects=SimpleNamespace(prefix="controlled-runner/"),
        directory=tmp_path,
        scene_id="controlled-worker",
        observer=None,
        text_secret=SecretStr("controlled-text-key-not-persisted"),
        network_policy=NetworkPolicy(default_action="deny", egress=[]),
    )
    _, accepted, project_id = case.create("实际创建一个角色后简述结果")
    first = case.execute(
        accepted,
        fault="business_committed" if crash else None,
        controlled_replies=[
            calls(
                (
                    "create-role",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(project_id),
                        "patch": {
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "hero",
                                    "values": {"kind": "character", "name": "雨夜来客"},
                                }
                            ]
                        },
                    },
                )
            ),
            {"text": "已保存角色"},
        ],
    )
    if crash:
        assert first.status == "running" and case.facts["worker_deliveries"][0]["crash_injected"]
        expire(task_workspace, accepted)
    second = case.execute(accepted, controlled_replies=[{"text": "已核对保存角色"}])
    assert second.status == "succeeded", second.error
    deliveries = case.facts["worker_deliveries"]
    assert len({item["pid"] for item in deliveries}) == 2
    assert deliveries[1]["controlled_requests"] == (1 if crash else 0)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.get(AIModelConfig, model_id).apikey is None
        assert len(session.scalars(select(AgentRun)).all()) == 1
    monkeypatch.setenv("AGENT_LIVE_API_KEY", "controlled-text-key-not-persisted")
    case.report()
    report_text = (tmp_path / "controlled-worker.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    diagnostics = report["model_diagnostics"]
    selectors = [item for item in diagnostics if item["source"] == "tool_selection"]
    assert selectors and all("实际创建一个角色" in item["query"]["preview"] for item in selectors)
    assert all(item["selection_parsed"] and item["selected_tool_names"] == [] for item in selectors)
    mains = [item for item in diagnostics if item["source"] == "assistant"]
    assert all("runtime__load_skill" in item["bound_tool_names"] for item in mains)
    assert any(item.get("final_reply", {}).get("preview") for item in mains)
    assert all(
        item["scope_id"] in {scope["id"] for scope in report["scopes"]} for item in diagnostics
    )
    assert "body_b64" not in report_text and '"headers"' not in report_text
    assert "controlled-text-key-not-persisted" not in report_text


@pytest.mark.parametrize("recover_after_model", [False, True], ids=["normal", "recovery"])
@pytest.mark.parametrize(
    ("content", "narration"),
    [
        pytest.param([], None, id="empty-blocks"),
        pytest.param("", None, id="empty-text"),
        pytest.param("    ", None, id="spaces"),
        pytest.param(" \t\r\n ", None, id="whitespace"),
        pytest.param("  正在核对实际生成策略  ", "  正在核对实际生成策略  ", id="narration"),
    ],
)
def test_execute_finishes_two_model_rounds_without_broker_reschedule(
    task_workspace, monkeypatch, content, narration, recover_after_model
):
    from short_drama.domain.assistant.conversations.models import AgentEvent

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport(
        [
            {**calls(("policy", "runtime__get_generation_policy", {})), "text": content},
            {"text": "已根据实际生成策略完成讨论"},
        ]
    )
    runtime = runtime_for(factory, settings, transport)
    version = 1
    if recover_after_model:
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService

        original = AgentToolExecutionService.model_result

        def crash(service, signature, message):
            original(service, signature, message)
            raise WorkerLost("worker lost after model receipt before graph checkpoint")

        monkeypatch.setattr(AgentToolExecutionService, "model_result", crash)
        with pytest.raises(WorkerLost):
            runtime.execute(accepted.run.id, version)
        with factory() as session:
            prepared = session.scalar(
                select(AgentToolCall).where(AgentToolCall.run_id == accepted.run.id)
            )
            assert prepared is not None and prepared.status == "prepared"
            assert session.get(AgentTurn, prepared.turn_id).response["applied"] is True
            assert not session.scalar(
                select(AgentOperation.id).where(AgentOperation.run_id == accepted.run.id)
            )
        monkeypatch.setattr(AgentToolExecutionService, "model_result", original)
        version = expire(task_workspace, accepted)
    runtime.execute(accepted.run.id, version)
    runtime.execute(accepted.run.id, version)
    assert len(transport.selection_requests) == 2
    assert all(
        "讨论镜头节奏" in json.dumps(body["messages"], ensure_ascii=False)
        for body in transport.selection_requests
    )
    assert all(
        any(
            item["role"] == "assistant"
            and isinstance(item.get("content"), str)
            and item["content"].startswith('{"task_working_notes"')
            for item in body["messages"]
        )
        for body in transport.main_requests
    )
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded" and run.message_status == "idle"
        assert run.message_version == version + 1 and run.lease_token is None
        assert session.get(AgentTask, accepted.task.id).status == "succeeded"
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert len(operations) == 1 and operations[0].status == "succeeded"
        assert operations[0].result["settings_version"] == "0"
        assert session.get(AgentToolCall, operations[0].tool_call_id).status == "succeeded"
        assert run.usage["tool_calls"] == 1
        messages = session.scalars(
            select(AgentMessage)
            .where(AgentMessage.task_id == accepted.task.id, AgentMessage.role == "assistant")
            .order_by(AgentMessage.seq)
        ).all()
        assert [(message.kind, message.content) for message in messages] == [
            *([("narration", narration)] if narration is not None else []),
            ("reply", "已根据实际生成策略完成讨论"),
        ]
        events = session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id)).all()
        assert "task_working_notes" not in json.dumps([event.payload for event in events])
        assert len(transport.main_requests) == 2
        assert run.usage["decision_calls"] == len(transport.requests)
        assert run.usage["output_tokens"] == 3 * len(transport.requests)
        assert run.usage["reserved_output_tokens"] == 0
        assert len(
            session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        ) == len(transport.requests)
    continuation = transport.main_requests[1]["messages"]
    assert any(item["role"] == "tool" and item["tool_call_id"] == "policy" for item in continuation)


def expire(workspace, accepted):
    factory, settings, _ = workspace
    with factory.begin() as session:
        session.get(AgentRun, accepted.run.id).lease_until = utcnow() - timedelta(seconds=1)
    assert recover(factory, settings) == 1
    with factory() as session:
        return session.get(AgentRun, accepted.run.id).message_version


@pytest.mark.parametrize("question", [False, True], ids=["read", "ask_user"])
def test_first_tool_creates_one_operation_and_preserves_turn_identity(task_workspace, question):
    from tests.integration.standard.assistant.test_agent_questions import QUESTION

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    name = "runtime__ask_user" if question else "runtime__describe_creation_target"
    transport = RuntimeTransport(
        [calls(("first-tool", name, QUESTION if question else {})), {"text": "已核对目标"}],
        selections=[[name], []],
    )
    runtime = runtime_for(factory, settings, transport)
    runtime.execute(accepted.run.id, 1)
    runtime.execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == ("waiting_input" if question else "succeeded"), run.error
        operation = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).one()
        call = session.get(AgentToolCall, operation.tool_call_id)
        assert operation.tool_name == call.tool_name == name
        with factory() as reader:
            detail = task_service(reader, settings).operation_detail(operation.id)
            assert detail.model_dump(mode="json")["turn_id"] == str(call.turn_id)
            assert detail.action and detail.title and detail.target is not None
        assert "summary" not in operation.result["_display"]
        started = session.scalars(
            select(AgentEvent).where(
                AgentEvent.run_id == run.id, AgentEvent.event_type == "step.started"
            )
        ).one()
        assert started.payload["turn_id"] == str(call.turn_id)
        assert run.usage["decision_calls"] == len(transport.requests)
        if question:
            assert operation.wait_state["kind"] == "user_input"
            assert operation.wait_state["consumed"] is False
            assert run.lease_token is None
        else:
            assert operation.status == call.status == "succeeded"


@pytest.mark.parametrize(
    "failure", ["orm_type_error", "removed_tool", "runtime_error", "mysql_schema_error"]
)
def test_deterministic_graph_failure_is_terminal_and_recovery_does_not_schedule(
    task_workspace, monkeypatch, failure
):
    from short_drama.service.assistant.execution import tools

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    if failure == "removed_tool":
        from short_drama.agent.tools import registry

        monkeypatch.delitem(registry._CATALOG, "runtime__describe_creation_target")
    elif failure == "orm_type_error":
        constructor = tools.AgentOperation.__init__

        def invalid_operation(operation, **kwargs):
            constructor(operation, **kwargs, invalid_private_argument="PRIVATE_ERROR_CANARY")

        monkeypatch.setattr(tools.AgentOperation, "__init__", invalid_operation)
    else:
        original = tools.AgentToolExecutionService.start_tool

        def broken_tool(service, *args):
            original(service, *args)
            if failure == "mysql_schema_error":
                from pymysql.err import OperationalError as DriverOperationalError
                from sqlalchemy.exc import OperationalError

                raise OperationalError("private sql", {}, DriverOperationalError(1054, "CANARY"))
            raise RuntimeError("PRIVATE_ERROR_CANARY")

        monkeypatch.setattr(tools.AgentToolExecutionService, "start_tool", broken_tool)
    transport = RuntimeTransport([calls(("first-tool", "runtime__describe_creation_target", {}))])
    runtime = runtime_for(factory, settings, transport)
    runtime.execute(accepted.run.id, 1)
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "failed" and run.error == {"code": "agent_execution_error"}
        task = session.get(AgentTask, accepted.task.id)
        assert task.status == "paused_error" and task.phase == "terminal"
        assert task.error == run.error
        assert run.lease_token is run.lease_until is None
        assert run.message_status == "idle" and run.next_run_at is None
        version, usage, request_count = (
            run.message_version,
            deepcopy(run.usage),
            len(transport.requests),
        )
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert all(row.status == "failed" for row in operations)
        assert "CANARY" not in json.dumps([run.error, *[row.error for row in operations]])
        events = session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id)).all()
        assert "CANARY" not in json.dumps([event.payload for event in events])
    later = utcnow() + timedelta(seconds=settings.agent_lease_seconds + 1)
    monkeypatch.setattr("short_drama.agent.coordinator.recovery.utcnow", lambda: later)
    assert recover(factory, settings) == 0
    runtime.execute(accepted.run.id, version)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "failed" and run.message_version == version and run.usage == usage
    assert len(transport.requests) == request_count


@pytest.mark.parametrize("failure", ["disconnect", "lock_timeout", "pool_timeout"])
def test_database_disconnect_resumes_prepared_first_tool_without_repeating_model(
    task_workspace, monkeypatch, failure
):
    from pymysql.err import OperationalError as DriverOperationalError
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from short_drama.service.assistant.execution.tools import AgentToolExecutionService

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original = AgentToolExecutionService.start_tool

    def disconnected(*args):
        if failure == "pool_timeout":
            raise PoolTimeoutError("controlled exhausted pool")
        code = 1205 if failure == "lock_timeout" else 2013
        raise OperationalError(
            "controlled storage boundary", {}, DriverOperationalError(code, "lost")
        )

    monkeypatch.setattr(AgentToolExecutionService, "start_tool", disconnected)
    transport = RuntimeTransport(
        [calls(("first-tool", "runtime__describe_creation_target", {})), {"text": "已恢复核对"}]
    )
    with pytest.raises(RuntimeError, match="durable recovery"):
        runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "running"
        call = session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id)).one()
        assert call.status == "prepared"
        turn = session.get(AgentTurn, call.turn_id)
        assert turn.status == "succeeded" and turn.response["graph_message"]
        settled_turn_ids = set(
            session.scalars(select(AgentTurn.id).where(AgentTurn.run_id == run.id))
        )
        assert not session.scalar(select(AgentOperation.id).where(AgentOperation.run_id == run.id))
    monkeypatch.setattr(AgentToolExecutionService, "start_tool", original)
    version = expire(task_workspace, accepted)
    runtime_for(factory, settings, transport).execute(accepted.run.id, version)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        operation = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).one()
        assert operation.status == "succeeded" and operation.tool_call_id == call.id
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert settled_turn_ids < {turn.id for turn in turns}
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests) == 4
        assert run.usage["output_tokens"] == 3 * len(turns)
        assert run.usage["reserved_output_tokens"] == 0
    assert len(transport.main_requests) == 2


def test_duplicate_delivery_cannot_claim_the_active_graph(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace

    def duplicate(_body, _number):
        runtime_for(factory, settings, RuntimeTransport([])).execute(accepted.run.id, 1)

    transport = RuntimeTransport([{"text": "唯一答复"}], before_reply=duplicate)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert len(transport.main_requests) == 1
        assert (
            len(
                session.scalars(
                    select(AgentMessage).where(
                        AgentMessage.task_id == accepted.task.id, AgentMessage.role == "assistant"
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize("boundary", ["stop", "lease"])
def test_stop_and_lost_lease_block_new_tool_writes(task_workspace, boundary):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace

    def interrupt(_body, _number):
        with factory() as session:
            if boundary == "stop":
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, uuid4().hex
                )
            else:
                with session.begin():
                    session.get(AgentRun, accepted.run.id).lease_token = "b" * 32

    transport = RuntimeTransport(
        [calls(("blocked", "runtime__record_task_observation", {"note": "不应写入"}))],
        before_reply=interrupt,
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert not session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert run.usage["decision_calls"] == len(transport.requests)
        if boundary == "stop":
            assert (
                run.status == "cancelled"
                and session.get(AgentTask, accepted.task.id).status == "stopped"
            )
        else:
            assert run.status == "running" and run.lease_token == "b" * 32


def test_active_skill_tools_union_survives_summaries_and_release_removes_only_its_dependencies(
    task_workspace, monkeypatch
):
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
    from tests.support.assistant_runtime import tool_result

    factory, settings, model = task_workspace
    workspace = factory, settings, model, _Storage(factory)
    required = ["runtime__get_generation_policy", "runtime__memory_catalog"]
    skills = [
        create_skill(
            workspace,
            name=f"依赖方法{index}",
            slug=f"dependency-{index}",
            instructions=f"ACTIVE_SKILL_BODY_{index}",
            required_tools=[name],
            resources=[],
        )
        for index, name in enumerate(required)
    ]
    _, accepted = new_task(task_workspace)
    original = executor.assistant_middleware

    def small_context(**kwargs):
        middleware = original(**kwargs)
        middleware[1] = SkillSummarizationMiddleware(
            model=kwargs["model"],
            trigger=("messages", 4),
            keep=("messages", 2),
            trim_tokens_to_summarize=None,
        )
        return middleware

    monkeypatch.setattr(executor, "assistant_middleware", small_context)
    mains = []

    def route(body):
        if not body.get("tools"):
            return {"text": "受控摘要不复述目标或方法正文"}
        mains.append(body)
        number = len(mains)
        if number == 1:
            return calls(
                *[
                    (
                        f"load-{index}",
                        "runtime__load_skill",
                        {"name": skill.name, "content_version": "1", "purpose": "核对依赖并集"},
                    )
                    for index, skill in enumerate(skills)
                ]
            )
        if number == 4:
            return calls(
                (
                    "release-first",
                    "runtime__release_skill",
                    {"activation_id": tool_result(body, "load-0")["activation_id"]},
                )
            )
        return (
            calls((f"read-{number}", required[1], {}))
            if number < 6
            else {"text": "已按仍活动的方法完成核对"}
        )

    transport = RuntimeTransport([route] * 25)
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    assert len(mains) == len(transport.selection_requests) == 6
    for number, body in enumerate(mains, 1):
        bound = {item["function"]["name"] for item in body["tools"]}
        assert (required[0] in bound) is (2 <= number <= 4)
        assert (required[1] in bound) is (number >= 2)
        content = json.dumps(body["messages"], ensure_ascii=False)
        assert ("ACTIVE_SKILL_BODY_0" in content) is (2 <= number <= 4)
        assert ("ACTIVE_SKILL_BODY_1" in content) is (number >= 2)
    for body in transport.selection_requests:
        query = json.dumps(body["messages"], ensure_ascii=False)
        assert "讨论镜头节奏" in query and "task_working_notes" not in query
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(turn.usage.get("source") == "summarization" for turn in turns) >= 2
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        assert all(item.status == "succeeded" for item in session.scalars(select(AgentOperation)))


def test_worker_rebuild_restores_successful_skill_command_before_checkpoint(
    task_workspace, monkeypatch
):
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill

    factory, settings, model = task_workspace
    skill = create_skill(
        (factory, settings, model, _Storage(factory)),
        instructions="先核对人物动机，再给出建议。",
        resources=[],
    )
    _, accepted = new_task(task_workspace)
    original = AgentToolExecutionService.finish_tool

    def crash(self, operation_id, output):
        original(self, operation_id, output)
        raise WorkerLost("worker disappeared after operation commit before graph write")

    monkeypatch.setattr(AgentToolExecutionService, "finish_tool", crash)
    first = RuntimeTransport(
        [
            calls(
                (
                    "load",
                    "runtime__load_skill",
                    {
                        "name": skill.name,
                        "content_version": str(skill.content_version),
                        "purpose": "核对人物动机",
                    },
                )
            )
        ]
    )
    with pytest.raises(WorkerLost):
        runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        assert operation.status == "succeeded"
        activation = operation.result["activation_id"]
        operation_id = operation.id
    monkeypatch.setattr(AgentToolExecutionService, "finish_tool", original)
    version = expire(task_workspace, accepted)
    second = RuntimeTransport([{"text": "已按加载方法完成讨论"}])
    runtime_for(factory, settings, second).execute(accepted.run.id, version)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert len(operations) == 1 and operations[0].id == operation_id
        assert operations[0].result["activation_id"] == activation
        assert "skill_activations" not in session.get(AgentTask, accepted.task.id).effective_state
    results = [
        json.loads(item["content"])
        for item in second.main_requests[0]["messages"]
        if item["role"] == "tool"
    ]
    assert len(results) == 1 and results[0]["activation_id"] == activation
    assert skill.instructions in results[0]["instructions"]


def test_input_arriving_during_model_is_consumed_once_by_next_round(task_workspace):
    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace

    def supplement(_body, number):
        if number == 1:
            with factory() as session:
                task_service(session, settings).send_task_message(
                    conversation.id,
                    {"task_id": accepted.task.id, "content": "新增要求：保留雨声"},
                    uuid4().hex,
                )

    transport = RuntimeTransport(
        [calls(("policy", "runtime__get_generation_policy", {})), {"text": "已保留雨声"}],
        before_reply=supplement,
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert task.applied_input_revision == task.input_revision == 2
    for requests in (transport.selection_requests, transport.main_requests):
        for body in requests:
            assert source_payload(body)["current_run"] == {
                "run_id": str(accepted.run.id),
                "trigger_message_id": str(accepted.message.id),
                "trigger_seq": accepted.message.seq,
            }
        sources = source_payload(requests[-1])["task_user_sources"]["items"]
        assert [row["content"] for row in sources] == ["讨论镜头节奏", "新增要求：保留雨声"]
    messages = transport.main_requests[1]["messages"]
    assert sum(item.get("content") == "新增要求：保留雨声" for item in messages) == 1
    assert sum(item.get("content") == "讨论镜头节奏" for item in messages) == 1
    assert "新增要求：保留雨声" not in json.dumps(
        transport.selection_requests[0]["messages"], ensure_ascii=False
    )
    query = json.dumps(transport.selection_requests[1]["messages"], ensure_ascii=False)
    assert "讨论镜头节奏" in query and "新增要求：保留雨声" in query
    for body in transport.main_requests:
        assert (
            sum(
                isinstance(item.get("content"), str)
                and item["content"].startswith('{"task_user_sources"')
                for item in body["messages"]
            )
            == 1
        )


def test_final_input_handoff_preserves_the_framework_call_budget(task_workspace, monkeypatch):
    from short_drama.service.assistant.execution.run import AgentRunService

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.config_snapshot = {
            **run.config_snapshot,
            "assistant_settings": {
                **run.config_snapshot["assistant_settings"],
                "model_call_limit": 1,
            },
        }
    original_finish = AgentRunService.finish
    injected = []

    def before_finish(service, claim, **kwargs):
        if not injected:
            with factory() as session:
                task_service(session, settings).send_task_message(
                    conversation.id,
                    {"task_id": accepted.task.id, "content": "补充输入不能重置模型额度"},
                    uuid4().hex,
                )
            injected.append(True)
        return original_finish(service, claim, **kwargs)

    monkeypatch.setattr(AgentRunService, "finish", before_finish)
    transport = RuntimeTransport([{"text": "原最终答复"}])
    runtime = runtime_for(factory, settings, transport)
    runtime.execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.message_status == "pending"
        version, charged = run.message_version, run.usage
    runtime.execute(accepted.run.id, version)
    with factory() as session:
        run, task = session.get(AgentRun, accepted.run.id), session.get(AgentTask, accepted.task.id)
        assert run.status == "failed" and run.error["code"] == "agent_budget_exhausted"
        assert task.status == "paused_budget" and task.applied_input_revision == 1
        assert task.input_revision == 2
        for field in ("decision_calls", "input_tokens", "output_tokens", "reserved_output_tokens"):
            assert run.usage[field] == charged[field]
        assert run.usage["decision_calls"] == len(transport.requests)
        assert not session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.task_id == task.id,
                AgentMessage.role == "assistant",
                AgentMessage.kind == "reply",
                AgentMessage.content == "原最终答复",
            )
        )
        interrupted = session.scalars(
            select(AgentMessage).where(
                AgentMessage.task_id == task.id,
                AgentMessage.content == "原最终答复",
            )
        ).one()
        assert interrupted.kind == "narration"
        assert interrupted.references[0]["type"] == "interrupted_response"
    assert len(transport.main_requests) == 1


@pytest.mark.parametrize("boundary", ["model", "finish"])
def test_final_reply_hands_off_every_accepted_input(task_workspace, monkeypatch, boundary):
    from short_drama.service.assistant.execution.run import AgentRunService

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    injected = []
    content = "最终答复交接时补充：保留雨声"

    def supplement():
        if not injected:
            with factory() as session:
                response = task_service(session, settings).send_task_message(
                    conversation.id, {"task_id": accepted.task.id, "content": content}, uuid4().hex
                )
            injected.append(response.message.id)

    def before_reply(_body, number):
        if boundary == "model" and number == 1:
            supplement()

    original_finish = AgentRunService.finish

    def before_finish(service, claim, **kwargs):
        if boundary == "finish":
            supplement()
        return original_finish(service, claim, **kwargs)

    monkeypatch.setattr(AgentRunService, "finish", before_finish)
    transport = RuntimeTransport(
        [{"text": "原最终答复"}, {"text": "补充的雨声要求已处理"}], before_reply=before_reply
    )
    runtime = runtime_for(factory, settings, transport)
    runtime.execute(accepted.run.id, 1)
    if boundary == "finish":
        with factory() as session:
            run, task = (
                session.get(AgentRun, accepted.run.id),
                session.get(AgentTask, accepted.task.id),
            )
            assert task.input_revision == 2 and task.applied_input_revision == 1
            assert run.status == "running" and run.message_status == "pending"
            assert run.lease_token is None and run.next_run_at is not None
            version, snapshot, budget = run.message_version, run.config_snapshot, run.budget
        runtime.execute(accepted.run.id, version)
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.config_snapshot == snapshot and run.budget == budget
    with factory() as session:
        run, task = session.get(AgentRun, accepted.run.id), session.get(AgentTask, accepted.task.id)
        assert run.status == task.status == "succeeded"
        assert run.message_status == "idle" and run.lease_token is None
        assert task.applied_input_revision == task.input_revision == 2
        assert run.message_version == (2 if boundary == "model" else 3)
        assert [
            row.content
            for row in session.scalars(
                select(AgentMessage).where(
                    AgentMessage.task_id == task.id, AgentMessage.role == "assistant"
                )
            )
        ] == ["补充的雨声要求已处理"]
        assert len(session.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all()) == 1
        assert run.usage["decision_calls"] == len(transport.requests)
    assert len(transport.main_requests) == 2 and len(injected) == 1
    messages = transport.main_requests[1]["messages"]
    assert sum(item.get("content") == content for item in messages) == 1
    assert sum(item.get("content") == "讨论镜头节奏" for item in messages) == 1


@pytest.mark.parametrize("limit", ["ledger", "framework"])
def test_persistent_budget_pauses_without_a_reviewer_retry(task_workspace, limit):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        if limit == "ledger":
            run.budget = {**run.budget, "decision_calls": 3}
        else:
            run.config_snapshot = {
                **run.config_snapshot,
                "assistant_settings": {
                    **run.config_snapshot["assistant_settings"],
                    "model_call_limit": 1,
                },
            }
    transport = RuntimeTransport(
        [calls(("policy", "runtime__get_generation_policy", {})), {"text": "不能超额发送"}]
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "failed" and run.error["code"] == "agent_budget_exhausted"
        assert session.get(AgentTask, accepted.task.id).status == "paused_budget"
        assert (
            run.usage["decision_calls"]
            == len(transport.requests)
            == (3 if limit == "ledger" else 2)
        )
        assert len(transport.main_requests) == 1
        assert run.usage["output_tokens"] == 3 * len(transport.requests)
        assert run.usage["reserved_output_tokens"] == 0
        assert len(
            session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        ) == len(transport.requests)


def test_execute_runs_fifty_tool_rounds_in_one_delivery(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport(
        [
            *[
                calls((f"policy-{index}", "runtime__get_generation_policy", {}))
                for index in range(50)
            ],
            {"text": "连续五十轮已完成"},
        ]
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert run.status == "succeeded" and run.message_version == 2
        assert len(transport.main_requests) == 51
        assert len(operations) == 50 and all(item.status == "succeeded" for item in operations)
        assert run.usage["decision_calls"] == len(transport.requests)


def test_new_run_reads_settled_creation_after_stop_without_recreating_it(task_workspace):
    from short_drama.domain import Asset, Project
    from short_drama.service.projects.management.project import ProjectService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, settings, model = task_workspace
    with factory() as session:
        session.info["actor"] = task_actor()
        project = ProjectService(session).create_project({"name": "连续创作"})
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "保存乘客角色并核对",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )

    def stop_after_save(_body, number):
        if number == 2:
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, uuid4().hex
                )

    first = RuntimeTransport(
        [
            calls(
                (
                    "save",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(project.id),
                        "patch": {
                            "storyboard_version": "1",
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "passenger",
                                    "values": {"kind": "character", "name": "雨夜乘客"},
                                }
                            ],
                        },
                    },
                )
            ),
            {"text": "停止前已保存"},
        ],
        before_reply=stop_after_save,
    )
    runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "cancelled", (
            session.get(AgentRun, accepted.run.id).error,
            [
                (item.tool_name, item.status, item.error)
                for item in session.scalars(
                    select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
                )
            ],
        )
        asset = session.scalar(select(Asset).where(Asset.project_id == int(project.id)))
        assert asset is not None
        asset_id = str(asset.id)
        original_operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        assert original_operation.status == "succeeded"
    with factory() as session:
        resumed = task_service(session, settings).send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "继续核对已保存的乘客，不要重复创建"},
            uuid4().hex,
        )
    second = RuntimeTransport(
        [
            calls(("read", "runtime__read_selected_subjects", {"ids": [asset_id]})),
            {"text": "已核对原乘客"},
        ]
    )
    runtime_for(factory, settings, second).execute(resumed.run.id, 1)
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == resumed.run.id)
        )
        assert operation is not None, session.get(AgentRun, resumed.run.id).error
        assert operation.status == "succeeded", (
            operation.error,
            session.get(AgentRun, resumed.run.id).error,
        )
        assert session.get(AgentRun, resumed.run.id).status == "succeeded", session.get(
            AgentRun, resumed.run.id
        ).error
        assert "雨夜乘客" in json.dumps(operation.result, ensure_ascii=False)
        assert (
            len(session.scalars(select(Asset).where(Asset.project_id == int(project.id))).all())
            == 1
        )
        assert len(session.scalars(select(Project).where(Project.name == "连续创作")).all()) == 1
    metadata = next(
        json.loads(item["content"])
        for item in second.main_requests[0]["messages"]
        if item.get("content", "").startswith('{"task_id"')
    )
    assert any(item["id"] == asset_id for item in metadata["subjects"]["subjects"])


@pytest.mark.parametrize("bounded_omission", [False, True])
def test_stopped_question_new_run_exposes_current_trigger_to_selector_and_main(
    task_workspace, monkeypatch, bounded_omission
):
    from short_drama.core.exceptions import BusinessError
    from tests.integration.standard.assistant.test_agent_questions import QUESTION, answer
    from tests.integration.standard.assistant.test_graph_answer_sources import observe_admissions

    factory, settings, model_id = task_workspace
    goal = "先问我结尾是否团圆，必须等我的答案后才能保存一幕结尾。"
    followup = "停止后请核对这项任务已经结算的实际成果并简述；不创建、不修改，也不生成新媒体。"
    with factory() as session:
        manager = task_service(session, settings)
        conversation = manager.create_global_conversation({}, "current-run-conversation")
        accepted = manager.send_task_message(
            conversation.id, {"content": goal, "model_config_id": model_id}, "current-run-goal"
        )
    initial = RuntimeTransport([calls(("question", "runtime__ask_user", QUESTION))])
    runtime_for(factory, settings, initial).execute(accepted.run.id, 1)
    with factory() as session:
        old_run = session.get(AgentRun, accepted.run.id)
        assert old_run.status == "waiting_input", old_run.error
        question = session.scalar(select(AgentOperation).where(AgentOperation.run_id == old_run.id))
        question_id = question.id
    with factory() as session:
        stopped = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "stop-current-run-question"
        )
        assert stopped.run.status == "cancelled"
    with pytest.raises(BusinessError) as raised:
        answer(factory, settings, question_id)
    assert raised.value.status_code == 409
    with factory() as session:
        newer = task_service(session, settings).send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": followup},
            "current-run-read-only",
        )
        run = session.get(AgentRun, newer.run.id)
        trigger = session.get(AgentMessage, run.trigger_message_id)
        expected = {
            "run_id": str(run.id),
            "trigger_message_id": str(trigger.id),
            "trigger_seq": trigger.seq,
        }
        assert run.id != accepted.run.id and trigger.id == newer.message.id
        assert trigger.id != accepted.message.id
    observed = observe_admissions(monkeypatch)
    supplements = []

    def supplement(_body, number):
        if bounded_omission and number == 1:
            for index in range(32):
                with factory() as session:
                    item = task_service(session, settings).send_task_message(
                        conversation.id,
                        {
                            "task_id": accepted.task.id,
                            "content": f"后补{index}：仅核对真实成果。",
                        },
                        f"read-only-supplement-{index}",
                    )
                    assert item.run.id == newer.run.id
                    supplements.append(item.message)

    resumed = RuntimeTransport(
        [
            calls(("read", "runtime__read_conversation_history", {"from_seq": 1})),
            {"text": "原问题已停止，未保存结尾；已只读核对。"},
        ],
        before_reply=supplement,
    )
    runtime_for(factory, settings, resumed).execute(newer.run.id, 1)
    assert len(resumed.selection_requests) == len(resumed.main_requests) == 2
    for requests in (resumed.selection_requests, resumed.main_requests):
        for index, body in enumerate(requests):
            payload = source_payload(body)
            assert payload["current_run"] == expected
            assert list(payload)[0] == "task_user_sources"
            sources = payload["task_user_sources"]
            assert sources["estimated_tokens"] <= sources["quota_tokens"] == 4000
            if bounded_omission and index == 1:
                assert sources["omitted_messages"] > 0
                assert str(newer.message.id) not in {row["id"] for row in sources["items"]}
                assert sources["items"][0]["content"] == goal
                assert sources["items"][-1]["id"] == str(supplements[-1].id)
            else:
                assert [(row["id"], row["content"]) for row in sources["items"]] == [
                    (str(accepted.message.id), goal),
                    (str(newer.message.id), followup),
                ]
    assert observed and all(
        len(row["pinned_source_ids"]) == 1 and str(newer.message.id) in row["pinned_source_ids"][0]
        for row in observed
    )
    with factory() as session:
        run = session.get(AgentRun, newer.run.id)
        assert run.status == "succeeded", run.error
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
        assert [(row.tool_name, row.status) for row in operations] == [
            ("runtime__read_conversation_history", "succeeded")
        ]
        assert goal in json.dumps(operations[0].result, ensure_ascii=False)
        assert not session.scalars(
            select(AgentMessage).where(AgentMessage.kind == "tool_answer")
        ).all()
        assert len(session.scalars(select(AgentRun)).all()) == 2
        assert len(session.scalars(select(AgentTask)).all()) == 1


@pytest.mark.parametrize("model_timeout", [120, 300])
def test_heartbeat_cancels_inflight_model_and_records_unknown_acceptance(
    task_workspace, monkeypatch, model_timeout
):
    from short_drama.agent.runtime.model_ledger import RunModelLedger

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    settings.agent_lease_seconds = 3
    settings.agent_model_timeout_seconds = model_timeout
    entered, release, cancelled = Event(), Event(), Event()
    original = RunModelLedger.save_error
    original_callbacks = RunModelLedger.callbacks
    seen_timeouts = []

    def callbacks(self, **kwargs):
        result = original_callbacks(self, **kwargs)
        seen_timeouts.append(result.timeout_seconds)
        return result

    def record(self, payload):
        original(self, payload)
        if payload.get("code") == "agent_model_cancelled":
            cancelled.set()

    monkeypatch.setattr(RunModelLedger, "save_error", record)
    monkeypatch.setattr(RunModelLedger, "callbacks", callbacks)

    def block(_body, _number):
        entered.set()
        assert release.wait(10)

    transport = RuntimeTransport(
        [calls(("late", "runtime__get_generation_policy", {}))], before_reply=block
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime_for(factory, settings, transport).execute, accepted.run.id, 1)
        try:
            assert entered.wait(10)
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, uuid4().hex
                )
            assert cancelled.wait(5), "租约监视器必须取消在途图，并保存模型受理不明事实"
        finally:
            release.set()
        future.result(timeout=10)
    assert seen_timeouts and set(seen_timeouts) == {model_timeout}
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled" and run.lease_token is None
        turn = session.scalar(
            select(AgentTurn).where(
                AgentTurn.run_id == run.id, AgentTurn.usage["source"].as_string() == "assistant"
            )
        )
        assert turn.status == "unknown" and turn.error["accepted_unknown"] is True
        assert turn.error["requests"] == 1 and turn.error["not_accepted"] is False
        assert not (turn.response or {}).get("raw") and not turn.usage.get("settled", False)
        assert run.usage["reserved_output_tokens"] == turn.usage["reserved_output_tokens"] == 8192
        version, saved_usage = run.message_version, dict(run.usage)
        turn_ids = set(session.scalars(select(AgentTurn.id).where(AgentTurn.run_id == run.id)))
        assert not session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == run.id)
        ).all()
    repeated = RuntimeTransport([])
    runtime_for(factory, settings, repeated).execute(accepted.run.id, version)
    assert repeated.requests == []
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.usage == saved_usage
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert {turn.id for turn in turns} == turn_ids
        unknown = next(turn for turn in turns if turn.usage["source"] == "assistant")
        assert unknown.status == "unknown" and not unknown.usage.get("settled", False)
        assert unknown.usage["reserved_output_tokens"] == 8192


def test_renewal_outage_expires_into_recovery_without_user_cancellation(
    task_workspace, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    from short_drama.agent.gateway.contracts import AgentGatewayError
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.service.assistant.execution.run import AgentRunService

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    settings.agent_lease_seconds = 1
    entered, release, cancelled = Event(), Event(), Event()
    observed_ledgers = []
    original = RunModelLedger.save_error

    def record(self, payload):
        original(self, payload)
        observed_ledgers.append(self)
        if payload.get("code") == "agent_model_cancelled":
            cancelled.set()

    def unavailable(self, claim):
        raise OperationalError("controlled renew", {}, OSError(2006, "controlled outage"))

    def block(_body, _number):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(RunModelLedger, "save_error", record)
    monkeypatch.setattr(AgentRunService, "renew", unavailable)
    transport = RuntimeTransport([{"text": "late answer"}], before_reply=block)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime_for(factory, settings, transport).execute, accepted.run.id, 1)
        try:
            assert entered.wait(10)
            assert cancelled.wait(4)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="durable recovery required"):
            future.result(timeout=10)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "running" and run.error is None and run.cancel_requested == 0
        assert run.lease_token and run.lease_until <= utcnow()
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == run.id))
        request = turn.request_messages[0]
        assert len(transport.main_requests) == 1
    with pytest.raises(AgentGatewayError) as denied:
        observed_ledgers[-1].before_send(request)
    assert denied.value.code == "agent_admission_denied"


def test_unready_interrupt_is_not_resumed_and_stop_finishes_wait_immediately(
    task_workspace, monkeypatch
):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from short_drama.agent.runtime.context import AssistantState, RuntimeContext

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    entered = []

    def builder(**kwargs):
        def wait_node(state):
            entered.append(True)
            interrupt({"kind": "controlled-persistent-wait"})
            return {"messages": []}

        graph = StateGraph(AssistantState, context_schema=RuntimeContext)
        graph.add_node("wait", wait_node)
        graph.add_edge(START, "wait")
        graph.add_edge("wait", END)
        return graph.compile(checkpointer=kwargs["checkpointer"])

    monkeypatch.setattr(executor, "build_agent", builder)
    runtime_for(factory, settings, RuntimeTransport([])).execute(accepted.run.id, 1)
    assert len(entered) == 1
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_generation" and run.lease_token is None
        run.message_status, run.next_run_at = "pending", utcnow()
        version = run.message_version
    runtime_for(factory, settings, RuntimeTransport([])).execute(accepted.run.id, version)
    assert len(entered) == 1
    with factory() as session:
        stopped = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, uuid4().hex
        )
        assert stopped.run.status == "cancelled" and stopped.task.status == "stopped"
    assert recover(factory, settings) == 0


@pytest.mark.parametrize("boundary", ["stop", "access"])
def test_wait_settlement_cannot_overwrite_stop_or_access_revocation(
    task_workspace, monkeypatch, boundary
):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from short_drama.agent.runtime.context import AssistantState, RuntimeContext
    from short_drama.domain.identity.accounts.user import User
    from short_drama.service.assistant.execution.run import AgentRunService

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace

    def builder(**kwargs):
        def wait_node(state):
            interrupt({"kind": "controlled-persistent-wait"})
            return {"messages": []}

        graph = StateGraph(AssistantState, context_schema=RuntimeContext)
        graph.add_node("wait", wait_node)
        graph.add_edge(START, "wait")
        graph.add_edge("wait", END)
        return graph.compile(checkpointer=kwargs["checkpointer"])

    original_wait = AgentRunService.wait

    def revoke_before_wait(service, claim):
        if boundary == "stop":
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, uuid4().hex
                )
        else:
            with factory.begin() as session:
                session.get(User, claim.owner_id).status = "disabled"
        return original_wait(service, claim)

    monkeypatch.setattr(executor, "build_agent", builder)
    monkeypatch.setattr(AgentRunService, "wait", revoke_before_wait)
    transport = RuntimeTransport([])
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run, task = session.get(AgentRun, accepted.run.id), session.get(AgentTask, accepted.task.id)
        assert run.status == "cancelled" and task.status == "stopped"
        assert run.error["code"] == ("agent_stopped" if boundary == "stop" else "access_revoked")
        assert run.lease_token is None and run.lease_until is None
        assert run.message_status == "idle" and run.next_run_at is None
        assert task.control_state == "none"
    assert recover(factory, settings) == 0 and not transport.requests


def test_completed_checkpoint_recovers_without_another_model_request(task_workspace, monkeypatch):
    from short_drama.service.assistant.execution.run import AgentRunService

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original = AgentRunService.finish

    def crash(*args, **kwargs):
        raise WorkerLost("worker lost after final checkpoint before terminal settlement")

    monkeypatch.setattr(AgentRunService, "finish", crash)
    first = RuntimeTransport([{"text": "检查点里的完整答复"}])
    with pytest.raises(WorkerLost):
        runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    monkeypatch.setattr(AgentRunService, "finish", original)
    version = expire(task_workspace, accepted)
    second = RuntimeTransport([])
    runtime_for(factory, settings, second).execute(accepted.run.id, version)
    assert second.requests == []
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert [
            row.content
            for row in session.scalars(
                select(AgentMessage).where(
                    AgentMessage.task_id == accepted.task.id, AgentMessage.role == "assistant"
                )
            )
        ] == ["检查点里的完整答复"]


@pytest.mark.parametrize("model_timeout", [120, 300])
def test_recovery_records_sent_request_as_unknown_instead_of_resending(
    task_workspace, model_timeout
):
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime

    _, accepted = new_task(task_workspace)
    _saver, context = runtime(task_workspace, accepted)
    context.settings.agent_model_timeout_seconds = model_timeout
    ledger = RunModelLedger(context)
    assert ledger.callbacks().timeout_seconds == model_timeout
    ledger.before_send(
        {"model_call_id": "crashed-inflight", "source": "assistant", "body": {"max_tokens": 20}}
    )
    expire(task_workspace, accepted)
    with context.factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == run.id))
        assert run.status == "failed" and run.error["code"] == "agent_acceptance_unknown"
        assert turn.status == "unknown" and turn.error["accepted_unknown"] is True
        assert not (turn.response or {}).get("raw") and not turn.usage.get("settled", False)
        assert run.usage["reserved_output_tokens"] == turn.usage["reserved_output_tokens"] == 20
        version, saved_usage, turn_id = run.message_version, dict(run.usage), turn.id
    repeated = RuntimeTransport([])
    runtime_for(context.factory, context.settings, repeated).execute(accepted.run.id, version)
    assert repeated.requests == []
    with context.factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert run.usage == saved_usage and [turn.id for turn in turns] == [turn_id]
        assert turns[0].status == "unknown" and not turns[0].usage.get("settled", False)
        assert turns[0].usage["reserved_output_tokens"] == 20


@pytest.mark.parametrize("response_status", ["succeeded", "truncated"])
@pytest.mark.parametrize("compact", [False, True])
def test_image_directory_read_hydrates_only_the_next_model_input(
    task_workspace, monkeypatch, response_status, compact
):
    import hashlib

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from short_drama.agent.resources.contracts import assert_reference_payload
    from short_drama.agent.resources.resolver import ResourceResolver
    from short_drama.agent.runtime.checkpointer import _validate_state
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from short_drama.domain import MediaFile
    from short_drama.domain.assistant.execution.graph import AgentGraphCheckpoint, AgentGraphWrite
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        image_group_workspace,
    )
    from tests.support.assistant_runtime import tool_result
    from tests.unit.standard.agent.test_harness_resources import ObjectStore, png

    factory, settings, model = task_workspace
    if compact:
        original = executor.assistant_middleware

        def summarize_before_pixels(**kwargs):
            middleware = original(**kwargs)
            middleware[1] = SkillSummarizationMiddleware(
                model=kwargs["model"],
                trigger=("messages", 6),
                keep=("messages", 2),
                trim_tokens_to_summarize=None,
            )
            return middleware

        monkeypatch.setattr(executor, "assistant_middleware", summarize_before_pixels)
    reference, groups = image_group_workspace(task_workspace)
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
                "content": "查看冻结图位，再讨论画面",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
            },
            uuid4().hex,
        )

    def read_image(body):
        arguments = json.loads(tool_result(body, "catalog")["content"])["groups"][0]["items"][0][
            "read_content_args"
        ]
        return calls(
            ("pixels", "workbench__read_image_content", {**arguments, "purpose": "查看画面"})
        )

    def note_pixels(_body):
        if response_status == "truncated":
            return {"text": "未完成的图片响应", "finish_reason": "length"}
        with factory() as session:
            source_id = session.scalar(
                select(AgentOperation.id).where(
                    AgentOperation.run_id == accepted.run.id,
                    AgentOperation.tool_name == "workbench__read_image_content",
                )
            )
        return calls(
            (
                "visual-note",
                "runtime__record_task_observation",
                {"operation_id": str(source_id), "note": "已检查此图位像素", "kind": "observation"},
            )
        )

    transport = RuntimeTransport(
        [
            calls(
                (
                    "catalog",
                    "workbench__read_image_group",
                    {
                        "project_id": reference["project_id"],
                        "target_kind": reference["kind"],
                        "target_id": reference["id"],
                        "view": "current",
                    },
                )
            ),
            read_image,
            *([{"text": "摘要保留目录，下一步按原图核对"}] if compact else []),
            note_pixels,
            {"text": "已依据真实图位完成讨论"},
        ]
    )
    executor.AgentRuntime(
        factory, settings, transport=transport, resolver_factory=lambda: ResourceResolver(storage)
    ).execute(accepted.run.id, 1)
    main_requests = [body for body in transport.main_requests if body.get("tools")]
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        if response_status == "truncated":
            assert run.status == "failed" and run.error["code"] == "agent_response_truncated"
            read = session.scalar(
                select(AgentOperation).where(
                    AgentOperation.run_id == run.id,
                    AgentOperation.tool_name == "workbench__read_image_content",
                )
            )
            call = session.get(AgentToolCall, read.tool_call_id)
            assert not call.result.get("resource_deliveries")
            assert len(main_requests) == 3
            assert "data:image/jpeg;base64," in json.dumps(main_requests[2])
            return
        assert run.status == "succeeded", (
            run.error,
            [
                (op.tool_name, op.status, op.error)
                for op in session.scalars(
                    select(AgentOperation).where(AgentOperation.run_id == run.id)
                )
            ],
        )
        assert len(main_requests) == 4
        assert "data:image/jpeg;base64," in json.dumps(main_requests[2])
        assert "data:image/" not in json.dumps(main_requests[3])
        assert tool_result(main_requests[3], "visual-note")["recorded"] is True
        read = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "workbench__read_image_content",
            )
        )
        note = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "runtime__record_task_observation",
            )
        )
        assert note.arguments["operation_id"] == str(read.id)
        deliveries = session.get(AgentToolCall, read.tool_call_id).result["resource_deliveries"]
        assert len(deliveries) == 1 and deliveries[0]["resource"]["kind"] == "image"
        assert "已检查此图位像素" in json.dumps(main_requests[3], ensure_ascii=False)
        task = session.get(AgentTask, accepted.task.id)
        assert "media_evidence" not in task.effective_state
        for row in session.scalars(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)):
            assert_reference_payload(row.request_messages)
        for row in session.scalars(select(AgentGraphCheckpoint)):
            _validate_state(
                JsonPlusSerializer(pickle_fallback=False).loads_typed((row.codec, row.payload))
            )
        for row in session.scalars(select(AgentGraphWrite)):
            _validate_state(
                JsonPlusSerializer(pickle_fallback=False).loads_typed((row.codec, row.payload))
            )


@pytest.mark.parametrize("response_status", ["succeeded", "truncated"])
def test_skill_image_hydrates_after_summary_and_audits_only_successful_pixel_delivery(
    task_workspace, monkeypatch, response_status
):
    import base64
    import hashlib
    import io

    from PIL import Image

    from short_drama.agent.resources.contracts import assert_reference_payload
    from short_drama.agent.resources.resolver import ResourceResolver
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill
    from tests.support.assistant_runtime import tool_result
    from tests.unit.standard.agent.test_harness_resources import ObjectStore, png

    factory, settings, model = task_workspace
    settings.minio_image_bucket = "fixture-skill-pixels"
    packages = _Storage(factory)
    skill = create_skill(
        (factory, settings, model, packages),
        instructions="按[分镜参考图](assets/frame.png)核对画面。",
        resources=[
            {
                "path": "assets/frame.png",
                "kind": "image",
                "encoding": "base64",
                "content": base64.b64encode(png()).decode(),
            }
        ],
    )
    storage = ObjectStore()
    storage.objects.update(
        {f"minio://{bucket}/{key}": data for (bucket, key), data in packages.objects.items()}
    )
    _, accepted = new_task(task_workspace)
    original = executor.assistant_middleware

    def summarize_before_pixels(**kwargs):
        middleware = original(**kwargs)
        middleware[1] = SkillSummarizationMiddleware(
            model=kwargs["model"],
            trigger=("messages", 4),
            keep=("messages", 2),
            trim_tokens_to_summarize=None,
        )
        return middleware

    monkeypatch.setattr(executor, "assistant_middleware", summarize_before_pixels)
    mains, activations = [], []

    def route(body):
        if not body.get("tools"):
            return {"text": "摘要仅保留待核对状态"}
        mains.append(body)
        if len(mains) == 1:
            return calls(
                (
                    "load-image-skill",
                    "runtime__load_skill",
                    {"name": skill.name, "content_version": "1", "purpose": "核对方法参考图"},
                )
            )
        if len(mains) == 2:
            loaded = tool_result(body, "load-image-skill")
            assert loaded["content_version"] == str(skill.content_version)
            activations.append(loaded["activation_id"])
            return calls(
                (
                    "skill-pixels",
                    "runtime__read_skill_resource",
                    {
                        "activation_id": activations[0],
                        "path": "assets/frame.png",
                        "purpose": "核对分镜画面",
                    },
                )
            )
        if len(mains) == 3:
            return (
                {"text": "图片响应截断", "finish_reason": "length"}
                if response_status == "truncated"
                else calls(
                    (
                        "release-image-skill",
                        "runtime__release_skill",
                        {"activation_id": activations[0]},
                    )
                )
            )
        if len(mains) == 4:
            return calls(
                (
                    "released-skill-pixels",
                    "runtime__read_skill_resource",
                    {"activation_id": activations[0], "path": "assets/frame.png"},
                )
            )
        return {"text": "已核对方法中的真实参考图"}

    transport = RuntimeTransport([route] * 12)
    executor.AgentRuntime(
        factory, settings, transport=transport, resolver_factory=lambda: ResourceResolver(storage)
    ).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == ("failed" if response_status == "truncated" else "succeeded"), (
            run.error
        )
        reads = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.tool_name == "runtime__read_skill_resource")
            .order_by(AgentOperation.created_at, AgentOperation.id)
        ).all()
        read = reads[0]
        assert read.status == "succeeded", read.error
        assert "data:image/jpeg;base64," in json.dumps(mains[2])
        pairs = []
        for call_id in ("load-image-skill", "skill-pixels"):
            call_index = next(
                index
                for index, message in enumerate(mains[2]["messages"])
                if any(call["id"] == call_id for call in message.get("tool_calls", []))
            )
            result_index = next(
                index
                for index, message in enumerate(mains[2]["messages"])
                if message.get("tool_call_id") == call_id
            )
            assert result_index == call_index + 1
            pairs.append((call_index, result_index))
        assert pairs[0][1] < pairs[1][0]
        pixel_block = next(
            block
            for message in mains[2]["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "image_url"
        )
        pixel_bytes = base64.b64decode(pixel_block["image_url"]["url"].split(",", 1)[1])
        with Image.open(io.BytesIO(pixel_bytes)) as image:
            assert image.size == (64, 40) and image.getpixel((0, 0))[0] > 200
        reference = read.result["hydration_intents"][0]["resource"]
        assert read.result["image_ref"]["source"] == "skill_resource"
        assert read.result["image_ref"]["checksum"] == hashlib.sha256(png()).hexdigest()
        assert reference["source"] == "hydrated_image"
        assert reference["checksum"] == hashlib.sha256(pixel_bytes).hexdigest()
        deliveries = session.get(AgentToolCall, read.tool_call_id).result.get(
            "resource_deliveries", []
        )
        assert bool(deliveries) is (response_status == "succeeded")
        if response_status == "succeeded":
            assert len(mains) == 5 and "data:image/" not in json.dumps(mains[3:])
            assert reads[1].status == "failed" and reads[1].error["code"] == "not_found"
            assert len(deliveries) == 1 and deliveries[0]["resource"] == reference
        else:
            assert run.error["code"] == "agent_response_truncated"
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert any(turn.usage.get("source") == "summarization" for turn in turns)
        for turn in turns:
            assert_reference_payload(turn.request_messages)


def test_history_revision_uses_original_position_and_has_no_tools(task_workspace):
    conversation, accepted = new_task(task_workspace)
    factory, settings, model = task_workspace
    runtime_for(factory, settings, RuntimeTransport([{"text": "原讨论答复"}])).execute(
        accepted.run.id, 1
    )
    with factory() as session:
        later = task_service(session, settings).send_task_message(
            conversation.id,
            {"content": "以后才知道的私有事实", "model_config_id": model},
            uuid4().hex,
        )
    runtime_for(factory, settings, RuntimeTransport([{"text": "未来答复"}])).execute(
        later.run.id, 1
    )
    with factory() as session:
        revised = task_service(session, settings).revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "edit", "row_version": "1", "content": "只讨论三秒开场"},
            uuid4().hex,
        )
    transport = RuntimeTransport([{"text": "修订后的讨论"}])
    runtime_for(factory, settings, transport).execute(revised.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, revised.run.id)
        assert run.status == "succeeded", run.error
        reply = session.scalar(
            select(AgentMessage).where(
                AgentMessage.task_id == accepted.task.id, AgentMessage.role == "assistant"
            )
        )
        assert reply.content == "修订后的讨论" and reply.row_version == 2
        assert not session.scalar(select(AgentOperation.id).where(AgentOperation.run_id == run.id))
    wire = transport.main_requests[0]
    assert not wire.get("tools")
    assert "以后才知道" not in json.dumps(wire, ensure_ascii=False)
    assert "未来答复" not in json.dumps(wire, ensure_ascii=False)


@pytest.mark.parametrize("stop", [False, True])
def test_committed_business_receipt_survives_worker_loss_before_tool_settlement(
    task_workspace, monkeypatch, stop
):
    from short_drama.domain import Asset
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService
    from short_drama.service.projects.management.project import ProjectService

    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        project = ProjectService(session).create_project({"name": "收据恢复"})
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "保存乘客角色",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )
    original = AgentToolExecutionService.finish_tool

    def crash(*args, **kwargs):
        raise WorkerLost("worker disappeared after business receipt before tool settlement")

    monkeypatch.setattr(AgentToolExecutionService, "finish_tool", crash)
    first = RuntimeTransport(
        [
            calls(
                (
                    "save",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(project.id),
                        "patch": {
                            "storyboard_version": "1",
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "passenger",
                                    "values": {"kind": "character", "name": "同一乘客"},
                                }
                            ],
                        },
                    },
                )
            )
        ]
    )
    with pytest.raises(WorkerLost):
        runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        assert operation.status == "running" and operation.business_invocation_id is not None
        operation_id = operation.id
    monkeypatch.setattr(AgentToolExecutionService, "finish_tool", original)
    if stop:
        with factory() as session:
            response = task_service(session, settings).control_task(
                accepted.task.id, {"kind": "stop"}, uuid4().hex
            )
            assert response.task.control_state == "stop_pending"
        with factory() as session:
            assert session.get(AgentRun, accepted.run.id).cancel_requested == 1
    version = expire(task_workspace, accepted)
    second = RuntimeTransport([{"text": "已核对同一收据"}])
    runtime_for(factory, settings, second).execute(accepted.run.id, version)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == ("cancelled" if stop else "succeeded"), run.error
        assert session.get(AgentOperation, operation_id).status == "succeeded"
        assert (
            len(session.scalars(select(Asset).where(Asset.project_id == int(project.id))).all())
            == 1
        )
        assert (
            len(
                session.scalars(select(AgentOperation).where(AgentOperation.run_id == run.id)).all()
            )
            == 1
        )
        assert len(second.main_requests) == (0 if stop else 1)


def test_canvas_write_rejects_stale_version_inside_the_same_execute(task_workspace):
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        canvas_patch_workspace,
    )
    from tests.support.assistant_runtime import tool_result

    factory, settings, model = task_workspace
    project_id, _canvas_id, saved = canvas_patch_workspace(task_workspace)
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "写入雨夜开场",
                "model_config_id": model,
                "execution_target": {
                    "mode": "infinite_canvas",
                    "project_id": str(project_id),
                    "canvas_id": saved["source_key"],
                },
            },
            uuid4().hex,
        )
    patch = {}

    def create(body):
        patch.update(
            expected_revision=tool_result(body, "directory")["canvas_revision"],
            actions=[
                {
                    "action": "create_node",
                    "key": "opening",
                    "values": {
                        "type": "text",
                        "title": "雨夜开场",
                        "content": "铜钥匙落在站台边。",
                    },
                }
            ],
        )
        return calls(
            (
                "create",
                "workbench__apply_canvas_patch",
                {
                    "project_id": str(project_id),
                    "canvas_id": saved["source_key"],
                    "patch": patch,
                },
            )
        )

    transport = RuntimeTransport(
        [
            calls(("directory", "runtime__describe_creation_target", {})),
            create,
            lambda body: calls(
                (
                    "stale",
                    "workbench__apply_canvas_patch",
                    {
                        "project_id": str(project_id),
                        "canvas_id": saved["source_key"],
                        "patch": patch,
                    },
                )
            ),
            {"text": "已保存开场，旧版本写入已拒绝"},
        ]
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        operations = session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "workbench__apply_canvas_patch",
            )
            .order_by(AgentOperation.id)
        ).all()
        assert len(operations) == 2 and operations[0].status == "succeeded"
        assert (
            operations[1].status == "failed"
            and operations[1].error["code"] == "canvas_revision_conflict"
        )


def test_forgotten_memory_is_removed_before_the_next_model_round(task_workspace):
    from short_drama.service.assistant.memory.memory import AgentMemoryService

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    with factory() as session:
        task_service(session, settings)
        memory = AgentMemoryService(session).create_item(
            {
                "kind": "preference",
                "memory_key": "runtime-style",
                "title": "旧风格",
                "content": "forgotten-private-style-marker",
            },
            uuid4().hex,
        )

    def forget(_body, number):
        if number == 2:
            with factory() as session:
                task_service(session, settings)
                AgentMemoryService(session).forget_item(
                    memory.id, {"content_version": memory.content_version}
                )

    transport = RuntimeTransport(
        [
            calls(("memory", "runtime__read_memory", {"ids": [str(memory.id)]})),
            calls(("policy", "runtime__get_generation_policy", {})),
            {"text": "只使用仍可用的输入继续讨论"},
        ],
        before_reply=forget,
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
    assert "forgotten-private-style-marker" in json.dumps(transport.main_requests[1])
    assert "forgotten-private-style-marker" not in json.dumps(transport.main_requests[2])


def test_stopped_run_saved_business_objects_are_trusted_by_new_run(task_workspace):
    from short_drama.domain import Asset
    from short_drama.service.projects.management.project import ProjectService
    from tests.support.assistant_runtime import tool_result

    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        project = ProjectService(session).create_project({"name": "停止后继续"})
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "创建乘客，稍后继续完善",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )

    def stop_after_save(_body, number):
        if number == 2:
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, uuid4().hex
                )

    first = RuntimeTransport(
        [
            calls(
                (
                    "save-before-stop",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(project.id),
                        "patch": {
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "passenger",
                                    "values": {"kind": "character", "name": "原乘客"},
                                }
                            ]
                        },
                    },
                )
            ),
            {"text": "停止"},
        ],
        before_reply=stop_after_save,
    )
    runtime_for(factory, settings, first).execute(accepted.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "cancelled"
        saved = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        assert saved.status == "succeeded"
        asset = session.scalar(select(Asset).where(Asset.project_id == project.id))
        asset_id, version, operation_id = str(asset.id), str(asset.row_version), saved.id
        session.rollback()
        continued = task_service(session, settings).send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "继续完善刚保存的乘客，不要新建"},
            uuid4().hex,
        )
    assert continued.run.id != accepted.run.id and continued.task.id == accepted.task.id
    second = RuntimeTransport(
        [
            calls(
                ("read-saved", "runtime__read_task_results", {"operation_id": str(operation_id)})
            ),
            calls(
                (
                    "update-saved",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(project.id),
                        "patch": {
                            "actions": [
                                {
                                    "action": "update_asset",
                                    "asset": asset_id,
                                    "patch": {"name": "继续后的乘客", "row_version": version},
                                }
                            ]
                        },
                    },
                )
            ),
            {"text": "已完善同一乘客"},
        ]
    )
    runtime_for(factory, settings, second).execute(continued.run.id, 1)
    assert "error" not in tool_result(second.main_requests[1], "read-saved")
    assert "error" not in tool_result(second.main_requests[2], "update-saved")
    with factory() as session:
        assets = session.scalars(select(Asset).where(Asset.project_id == project.id)).all()
        assert [(str(row.id), row.name) for row in assets] == [(asset_id, "继续后的乘客")]
