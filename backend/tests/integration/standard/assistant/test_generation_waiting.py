"""Native media tasks wait through the same durable production graph."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.domain import Asset, AsyncTask
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from tests.integration.standard.assets.test_asset_image_generation import drain
from tests.integration.standard.assets.test_asset_image_generation import flow as flow
from tests.integration.standard.assistant.test_agent_runtime import expire, runtime_for
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    _workspace,
    harness_workspace,
    new_task,
    task_service,
)
from tests.support.assistant_runtime import RuntimeTransport, WorkerLost, calls, tool_result

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def seed_older_unanswered_waits(factory, run_id, count=100, *, leased=False):
    from copy import deepcopy
    from datetime import timedelta

    from short_drama.core.time import utcnow
    from short_drama.domain.assistant.execution.models import AgentTurn
    from short_drama.domain.assistant.execution.scope import AgentExecutionScope
    from short_drama.utils.snowflake import next_id

    def clone(session, row, **updates):
        values = {
            column.name: deepcopy(getattr(row, column.name))
            for column in row.__table__.columns
            if column.computed is None
        }
        values.update(id=next_id(), **updates)
        result = type(row)(**values)
        session.add(result)
        session.flush()
        return result

    with factory.begin() as session:
        run = session.get(AgentRun, run_id)
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run_id, AgentOperation.wait_state.is_not(None)
            )
        )
        scope = session.get(AgentExecutionScope, operation.scope_id)
        call = session.get(AgentToolCall, operation.tool_call_id)
        turn = session.get(AgentTurn, call.turn_id)
        from short_drama.domain.assistant.conversations.models import AgentMessage

        conversation = session.get(AgentConversation, run.conversation_id)
        task = session.get(AgentTask, run.task_id)
        message = session.get(AgentMessage, run.trigger_message_id)
        for _ in range(count):
            older_conversation = clone(session, conversation, create_key=uuid4().hex.ljust(64, "0"))
            older_task = clone(session, task, conversation_id=older_conversation.id)
            older_message = clone(
                session, message, conversation_id=older_conversation.id, task_id=older_task.id
            )
            older = clone(
                session,
                run,
                status="waiting_input",
                conversation_id=older_conversation.id,
                task_id=older_task.id,
                trigger_message_id=older_message.id,
                updated_at=run.created_at,
                lease_token=uuid4().hex if leased else None,
                lease_until=utcnow() + timedelta(minutes=10) if leased else None,
            )
            child_scope = clone(session, scope, run_id=older.id)
            child_turn = clone(session, turn, run_id=older.id, scope_id=child_scope.id)
            child_call = clone(
                session,
                call,
                run_id=older.id,
                scope_id=child_scope.id,
                turn_id=child_turn.id,
                idempotency_key=uuid4().hex.ljust(64, "0"),
            )
            clone(
                session,
                operation,
                run_id=older.id,
                scope_id=child_scope.id,
                task_id=older_task.id,
                tool_call_id=child_call.id,
                tool_name="runtime__ask_user",
                wait_state={
                    "kind": "user_input",
                    "ready": leased,
                    "consumed": False,
                    "interrupt_id": "already-ready-but-leased" if leased else None,
                },
            )


def test_wait_rejects_guessed_generation_identity_as_tool_error(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport(
        [
            calls(("wait", "runtime__wait_generation", {"task_ids": ["42"]})),
            {"text": "当前任务没有已授权的生成身份"},
        ]
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        operation = session.scalar(select(AgentOperation).where(AgentOperation.run_id == run.id))
        assert operation.status == "failed" and operation.error


def media_task(flow):
    factory, settings, model = _workspace(flow.session)
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    with factory() as session:
        session.info["actor"] = task_actor()
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "创建角色并生成图片，随后等待结果",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": str(flow.project.id)},
            },
            uuid4().hex,
        )
    return (factory, settings, model), accepted


def test_delete_generation_wait_preserves_admitted_native_task_through_purge(flow):
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.agent.coordinator.recovery import recover
    from short_drama.agent.session.purge import purge_one

    workspace, accepted = media_task(flow)
    factory, settings, _ = workspace
    identities = []

    def submit(body):
        reference = tool_result(body, "create")["result_refs"][0]
        with factory() as session:
            version = str(session.get(Asset, int(reference["id"])).row_version)
        return calls(
            (
                "submit",
                "workbench__submit_generation_batch",
                {
                    "project_id": str(flow.project.id),
                    "requests": [
                        {
                            "target_kind": "asset",
                            "target_id": reference["id"],
                            "row_version": version,
                            "model_config_id": str(flow.config.id),
                            "model_version": str(flow.config.row_version),
                            "prompt": "删除助手后继续生成",
                            "count": 1,
                        }
                    ],
                },
            )
        )

    def wait(body):
        identities.append(int(tool_result(body, "submit")["items"][0]["generation_id"]))
        return calls(
            ("wait", "runtime__wait_generation", {"task_ids": [str(identities[0])]}),
        )

    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    (
                        "create",
                        "workbench__apply_standard_patch",
                        {
                            "project_id": str(flow.project.id),
                            "patch": {
                                "actions": [
                                    {
                                        "action": "create_asset",
                                        "key": "hero",
                                        "values": {"kind": "character", "name": "主角"},
                                    }
                                ]
                            },
                        },
                    )
                ),
                submit,
                wait,
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_generation" and run.lease_token is None
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "runtime__wait_generation",
            )
        )
        assert operation.status == "waiting_media" and operation.wait_state["interrupt_id"]
        operation_id = operation.id
        conversation_id = run.conversation_id
    with factory() as session:
        service = task_service(session, settings)
        current = service.get_conversation(conversation_id)
        assert (
            service.delete_global_conversation(
                conversation_id, {"row_version": current.row_version}, uuid4().hex
            ).status
            == "deleted"
        )
    assert collect_media_results(factory, settings) == recover(factory, settings) == 0
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        operation = session.get(AgentOperation, operation_id)
        assert session.get(AgentConversation, conversation_id).status == "deleted"
        assert run.status == "cancelled" and run.lease_token is None
        assert run.message_status == "idle"
        assert session.get(AgentTask, accepted.task.id).status == "stopped"
        assert operation.status == "failed" and operation.error == {"code": "agent_cancelled"}
        assert operation.wait_state["interrupt_id"]
        assert session.get(AgentToolCall, operation.tool_call_id).status == "cancelled"
        assert len(session.scalars(select(AsyncTask)).all()) == 1
        assert session.get(AsyncTask, identities[0]).status == "queued"
    assert purge_one(factory, None)
    assert drain(flow, identities[0]) == "succeeded"
    assert collect_media_results(factory, settings) == recover(factory, settings) == 0
    with factory() as session:
        assert session.get(AsyncTask, identities[0]).status == "succeeded"
        assert session.get(AgentRun, accepted.run.id).status == "cancelled"
        assert session.get(AgentOperation, operation_id).wait_state is None
        assert len(session.scalars(select(AsyncTask)).all()) == 1
        assert len(session.scalars(select(AgentRun)).all()) == 1
    assert len(flow.provider.calls) == 1


@pytest.mark.parametrize(
    "boundary", ["already_done", "registered", "bound", "released", "leased", "checkpoint"]
)
def test_native_submit_returns_immediately_and_wait_survives_completion_windows(
    flow, monkeypatch, boundary
):
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.service.assistant.execution.waiting import GenerationWaitService

    workspace, accepted = media_task(flow)
    factory, settings, _ = workspace
    identities = []

    def submit(body):
        reference = tool_result(body, "create")["result_refs"][0]
        with factory() as session:
            asset = session.get(Asset, int(reference["id"]))
            version = str(asset.row_version)
        return calls(
            (
                "submit",
                "workbench__submit_generation_batch",
                {
                    "project_id": str(flow.project.id),
                    "requests": [
                        {
                            "target_kind": "asset",
                            "target_id": reference["id"],
                            "row_version": version,
                            "model_config_id": str(flow.config.id),
                            "model_version": str(flow.config.row_version),
                            "prompt": "受控图片",
                            "count": 1,
                        }
                    ],
                },
            )
        )

    def wait(body):
        identities.append(int(tool_result(body, "submit")["items"][0]["generation_id"]))
        if boundary == "already_done":
            assert drain(flow, identities[0]) == "succeeded"
        return calls(
            ("independent", "runtime__get_generation_policy", {}),
            ("wait-one", "runtime__wait_generation", {"task_ids": [str(identities[0])]}),
            ("wait-two", "runtime__wait_generation", {"task_ids": [str(identities[0])]}),
        )

    original = GenerationWaitService.inspect_and_register
    completed = []

    def inspect(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if boundary == "registered" and not completed:
            completed.append(True)
            assert drain(flow, identities[0]) == "succeeded"
            collect_media_results(factory, settings)
        return result

    monkeypatch.setattr(GenerationWaitService, "inspect_and_register", inspect)
    original_bind = GenerationWaitService.bind_snapshot
    crashes = []

    async def bind(self, snapshot, claim):
        if boundary == "checkpoint" and not crashes:
            crashes.append(True)
            raise WorkerLost("lost after checkpoint before snapshot binding")
        await original_bind(self, snapshot, claim)
        if boundary == "bound" and not completed:
            completed.append(True)
            assert drain(flow, identities[0]) == "succeeded"
            collect_media_results(factory, settings)

    monkeypatch.setattr(GenerationWaitService, "bind_snapshot", bind)
    transport = RuntimeTransport(
        [
            calls(
                (
                    "create",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(flow.project.id),
                        "patch": {
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": "hero",
                                    "values": {"kind": "character", "name": "主角"},
                                }
                            ]
                        },
                    },
                )
            ),
            submit,
            wait,
            *([{"text": "全部已经完成"}] if boundary == "already_done" else []),
        ]
    )
    if boundary == "checkpoint":
        with pytest.raises(WorkerLost):
            runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    else:
        runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        ).all()
        assert len(operations) == 5, [(item.tool_name, item.error) for item in operations]
        assert (
            next(
                item for item in operations if item.tool_name == "runtime__get_generation_policy"
            ).status
            == "succeeded"
        )
        assert len(session.scalars(select(AsyncTask)).all()) == 1
    if boundary != "already_done":
        if boundary in {"released", "leased", "checkpoint"}:
            assert drain(flow, identities[0]) == "succeeded"
            if boundary in {"released", "leased"}:
                seed_older_unanswered_waits(factory, accepted.run.id, leased=boundary == "leased")
            assert collect_media_results(factory, settings) == int(boundary != "checkpoint")
            assert collect_media_results(factory, settings) == 0
        if boundary == "checkpoint":
            expire(workspace, accepted)
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.message_status == "pending" and run.lease_token is None
            version = run.message_version
        resumed = RuntimeTransport([{"text": "已取回生成成果"}])
        runtime_for(factory, settings, resumed).execute(accepted.run.id, version)
        assert len(resumed.main_requests) == 1
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        waits = session.scalars(
            select(AgentOperation).where(AgentOperation.tool_name == "runtime__wait_generation")
        ).all()
        assert all(item.wait_state["consumed"] for item in waits)
        assert all(item.result["items"][0]["result_refs"] for item in waits)
        assert len(session.scalars(select(AsyncTask)).all()) == 1
        assert len(flow.provider.calls) == 1


@pytest.mark.parametrize("order", ["answer_first", "generation_first", "stop"])
def test_mixed_question_and_multiple_generation_dependencies_resume_only_ready_calls(
    flow, monkeypatch, order
):
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.agent.coordinator.recovery import recover
    from short_drama.ai import GenerationResult
    from tests.integration.standard.assistant.test_agent_questions import (
        QUESTION,
        answer,
        execute_next,
    )

    workspace, accepted = media_task(flow)
    factory, settings, _ = workspace
    identities = []

    def submit(body):
        references = tool_result(body, "create")["result_refs"]
        with factory() as session:
            requests = [
                {
                    "target_kind": "asset",
                    "target_id": reference["id"],
                    "row_version": str(session.get(Asset, int(reference["id"])).row_version),
                    "model_config_id": str(flow.config.id),
                    "model_version": str(flow.config.row_version),
                    "prompt": "受控图片",
                    "count": 1,
                }
                for reference in references
            ]
        return calls(
            (
                "submit",
                "workbench__submit_generation_batch",
                {
                    "project_id": str(flow.project.id),
                    "requests": requests,
                },
            )
        )

    def wait(body):
        identities.extend(
            int(item["generation_id"]) for item in tool_result(body, "submit")["items"]
        )
        return calls(
            ("question", "runtime__ask_user", QUESTION),
            ("wait", "runtime__wait_generation", {"task_ids": [str(item) for item in identities]}),
        )

    transport = RuntimeTransport(
        [
            calls(
                (
                    "create",
                    "workbench__apply_standard_patch",
                    {
                        "project_id": str(flow.project.id),
                        "patch": {
                            "actions": [
                                {
                                    "action": "create_asset",
                                    "key": key,
                                    "values": {"kind": "character", "name": key},
                                }
                                for key in ("hero", "friend")
                            ]
                        },
                    },
                )
            ),
            submit,
            wait,
        ]
    )
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        ).all()
        question = next(item for item in operations if item.tool_name == "runtime__ask_user")
        generation = next(
            item for item in operations if item.tool_name == "runtime__wait_generation"
        )
        assert question.wait_state["interrupt_id"] != generation.wait_state["interrupt_id"]
        assert session.get(AgentRun, accepted.run.id).status == "waiting_input"
        from short_drama.service.assistant.execution.run import read_run

        wait_summary = read_run(session, session.get(AgentRun, accepted.run.id)).wait_summary
        assert wait_summary.pending_questions == 1
        assert wait_summary.media_batches == 1 and wait_summary.generation_tasks == 2
        assert wait_summary.elapsed_seconds >= 0 and wait_summary.since is not None
        assert "待答" in wait_summary.message and "生成" in wait_summary.message
    assert len(identities) == 2
    assert drain(flow, identities[0]) == "succeeded"
    assert collect_media_results(factory, settings) == 0
    with factory() as session:
        assert not session.get(AgentOperation, generation.id).wait_state["ready"]
        assert session.get(AgentRun, accepted.run.id).message_status == "idle"

    if order == "answer_first":
        answer(factory, settings, question.id)
        execute_next(workspace, accepted, RuntimeTransport([]))
        with factory() as session:
            assert session.get(AgentOperation, question.id).wait_state["consumed"]
            assert not session.get(AgentOperation, generation.id).wait_state["consumed"]
            assert session.get(AgentRun, accepted.run.id).status == "waiting_generation"
    elif order == "stop":
        with factory() as session:
            task_service(session, settings).control_task(
                accepted.task.id, {"kind": "stop"}, uuid4().hex
            )

    def fail(_snapshot, request, *_args, **_kwargs):
        flow.provider.calls.append(request)
        return GenerationResult(
            status="failed", adapter="ark_images.v1", error={"code": "controlled_failure"}
        )

    monkeypatch.setattr(flow.provider, "submit", fail)
    assert drain(flow, identities[1]) == "failed"
    assert collect_media_results(factory, settings) == int(order != "stop")
    if order == "stop":
        assert collect_media_results(factory, settings) == recover(factory, settings) == 0
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert (
                run.status == "cancelled"
                and run.lease_token is None
                and run.message_status == "idle"
            )
            for identifier in (question.id, generation.id):
                operation = session.get(AgentOperation, identifier)
                assert operation.status == "failed" and operation.error["code"] == "agent_cancelled"
        return
    if order == "generation_first":
        execute_next(workspace, accepted, RuntimeTransport([]))
        with factory() as session:
            assert session.get(AgentOperation, generation.id).wait_state["consumed"]
            assert not session.get(AgentOperation, question.id).wait_state["consumed"]
            assert session.get(AgentRun, accepted.run.id).status == "waiting_input"
        answer(factory, settings, question.id)
    resumed = RuntimeTransport([{"text": "保留成功图片，并说明另一个生成失败"}])
    execute_next(workspace, accepted, resumed)
    result = tool_result(resumed.main_requests[0], "wait")
    assert result["settled"]
    items = {item["generation_id"]: item for item in result["items"]}
    assert (
        items[str(identities[0])]["status"] == "succeeded"
        and items[str(identities[0])]["result_refs"]
    )
    assert items[str(identities[1])]["status"] == "failed" and items[str(identities[1])]["error"]
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert len(session.scalars(select(AsyncTask)).all()) == 2
        assert len(session.scalars(select(AgentRun)).all()) == 1
    assert len(flow.provider.calls) == 2


@pytest.mark.parametrize("human_edit", [False, True])
def test_previously_admitted_media_settles_real_outputs_without_reviving_old_run(
    flow, monkeypatch, human_edit
):
    from copy import deepcopy

    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.domain import ImageGroup
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.domain.assistant.operations.media import AgentMediaRequest
    from short_drama.domain.assistant.tasks.task import AgentTask
    from short_drama.service.assistant.operations.image import image_adoption_state
    from short_drama.utils.snowflake import next_id

    workspace, accepted = media_task(flow)
    factory, settings, _ = workspace
    targets = []

    def submit(body):
        reference = tool_result(body, "create")["result_refs"][0]
        with factory() as session:
            asset = session.get(Asset, int(reference["id"]))
            targets.append(
                {
                    "kind": "asset",
                    "id": str(asset.id),
                    "project_id": str(asset.project_id),
                    "name": asset.name,
                    "row_version": str(asset.row_version),
                    "before": image_adoption_state(session, asset, "asset"),
                }
            )
        return calls(
            (
                "submit",
                "workbench__submit_generation_batch",
                {
                    "project_id": str(flow.project.id),
                    "requests": [
                        {
                            "target_kind": "asset",
                            "target_id": reference["id"],
                            "row_version": targets[0]["row_version"],
                            "model_config_id": str(flow.config.id),
                            "model_version": str(flow.config.row_version),
                            "prompt": "保留已受理媒体",
                            "count": 1,
                        }
                    ],
                },
            )
        )

    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    (
                        "create",
                        "workbench__apply_standard_patch",
                        {
                            "project_id": str(flow.project.id),
                            "patch": {
                                "actions": [
                                    {
                                        "action": "create_asset",
                                        "key": "hero",
                                        "values": {"kind": "character", "name": "主角"},
                                    }
                                ]
                            },
                        },
                    )
                ),
                submit,
                {"text": "已受理"},
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory.begin() as session:
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == accepted.run.id,
                AgentOperation.tool_name == "workbench__submit_generation_batch",
            )
        )
        native_id = int(operation.result["items"][0]["generation_id"])
        operation_id = operation.id
        # A saved pre-cutover intent is the fixture; no retired runtime creates new work.
        operation.status = operation.phase = "waiting_media"
        operation.finished_at = None
        session.get(AgentToolCall, operation.tool_call_id).status = "waiting_generation"
        task = session.get(AgentTask, accepted.task.id)
        usage = deepcopy(task.media_usage)
        usage["images"] = {"reserved": 1, "submitted": 0, "succeeded": 0}
        task.media_usage = usage
        session.add(
            AgentMediaRequest(
                id=next_id(),
                task_id=task.id,
                operation_id=operation.id,
                item_index=1,
                media_kind="image",
                requested_count=1,
                generation_task_id=native_id,
                frozen_target=targets[0],
                frozen_request={},
                reservation_state="reserved",
                status="accepted",
            )
        )
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"

    original = flow.provider.submit

    def generate_then_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        if human_edit:
            with factory.begin() as session:
                asset = session.get(Asset, int(targets[0]["id"]))
                asset.description = "生成中人工修改"
                asset.row_version += 1
        return result

    monkeypatch.setattr(flow.provider, "submit", generate_then_edit)
    assert drain(flow, native_id) == "succeeded"
    assert collect_media_results(factory, settings) == 1
    assert collect_media_results(factory, settings) == 0
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        assert operation.status == ("conflict" if human_edit else "succeeded")
        assert operation.result["settled"] and operation.finished_at
        assert operation.result_refs[0]["generated_count"] == 1
        assert operation.result_refs[0]["status"] == (
            "adoption_conflict" if human_edit else "adopted"
        )
        group = session.scalar(select(ImageGroup).where(ImageGroup.task_id == native_id))
        asset = session.get(Asset, int(targets[0]["id"]))
        if human_edit:
            assert asset.description == "生成中人工修改" and asset.media_id is None
            assert group.primary_index is None
        else:
            assert asset.media_id and group.primary_index == 1
        assert session.get(AgentTask, accepted.task.id).media_usage["images"] == {
            "reserved": 0,
            "submitted": 1,
            "succeeded": 1,
        }
        run = session.get(AgentRun, accepted.run.id)
        assert (
            run.status == "succeeded" and run.message_status == "idle" and run.lease_token is None
        )
        assert len(session.scalars(select(AsyncTask)).all()) == 1
    assert len(flow.provider.calls) == 1
