"""Durable user answers exercise the production graph, tools and MySQL."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import BusinessError
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.service.assistant.operations.questions import QuestionService
from tests.integration.standard.assistant.test_agent_runtime import expire, runtime_for
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    task_actor,
    task_service,
)
from tests.support.assistant_runtime import RuntimeTransport, WorkerLost, calls, tool_result

pytestmark = pytest.mark.integration
task_workspace = harness_workspace

QUESTION = {
    "title": "选择结尾",
    "question": "你希望哪种结尾？",
    "fields": [
        {
            "key": "ending",
            "label": "结尾",
            "type": "single_select",
            "required": True,
            "options": [
                {"value": "开放结尾", "label": "开放结尾", "recommended": True},
                {"value": "雨夜团圆", "label": "雨夜团圆"},
            ],
        }
    ],
    "allow_skip": True,
}


def answer(factory, settings, identifier, *, owner=1, key="answer", **changes):
    with factory() as session:
        session.info["actor"] = task_actor(owner)
        return QuestionService(session, settings).answer(
            identifier,
            {
                "question_version": "1",
                "outcome": "answered",
                "values": {"ending": "开放结尾"},
                **changes,
            },
            idempotency_key=key,
        )


def wait_question(workspace, request=None):
    conversation, accepted = new_task(workspace)
    factory, settings, _ = workspace
    runtime_for(
        factory,
        settings,
        RuntimeTransport([calls(("question", "runtime__ask_user", request or QUESTION))]),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
        )
        return conversation, accepted, operation.id


def execute_next(workspace, accepted, transport):
    factory, settings, _ = workspace
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime_for(factory, settings, transport).execute(accepted.run.id, version)


def test_queued_new_task_explains_blocking_question_and_stop_target(task_workspace):
    conversation, accepted, _ = wait_question(task_workspace)
    factory, settings, model = task_workspace
    with factory() as session:
        queued = task_service(session, settings).send_task_message(
            conversation.id,
            {"content": "开始另一个任务", "model_config_id": model, "new_task": True},
            "queued-behind-question",
        )
    assert queued.run.queue_position == 1
    assert queued.run.blocking_run_id == accepted.run.id
    assert queued.run.blocking_task_id == accepted.task.id
    assert accepted.task.goal in queued.run.waiting_reason
    assert "待答" in queued.run.waiting_reason and "停止" in queued.run.waiting_reason
    assert queued.run.wait_summary.pending_questions == 1


def test_multi_question_answers_resume_with_choices_and_other_without_a_resolved_form(
    task_workspace,
):
    request = {
        **QUESTION,
        "fields": [
            *QUESTION["fields"],
            {
                "key": "mood",
                "label": "画面氛围",
                "type": "multi_select",
                "required": True,
                "options": [
                    {"value": "rain", "label": "雨夜", "recommended": True},
                    {"value": "shadow", "label": "暗影", "recommended": True},
                ],
            },
        ],
    }
    conversation, accepted, identifier = wait_question(task_workspace, request)
    factory, settings, _ = task_workspace
    with factory() as session:
        snapshot = task_service(session, settings).task_runtime_state(conversation.id)
        assert len(snapshot.pending_questions[0].request.fields) == 2
    values = {"ending": "保留开放式悬念", "mood": ["rain", "shadow", "铜钥匙"]}
    receipt = answer(factory, settings, identifier, values=values)
    with factory() as session:
        snapshot = task_service(session, settings).task_runtime_state(conversation.id)
        assert snapshot.pending_questions[0].status == "answered"
        assert snapshot.pending_questions[0].request is None
        message = session.get(AgentMessage, receipt.answer_message_id)
        assert message.content == "结尾：保留开放式悬念\n画面氛围：雨夜、暗影、铜钥匙"
    resumed = RuntimeTransport([{"text": "按所选方向继续"}])
    execute_next(task_workspace, accepted, resumed)
    result = next(item for item in resumed.main_requests[0]["messages"] if item["role"] == "tool")
    assert json.loads(result["content"])["values"] == values


def test_question_interrupt_is_durable_and_worker_rebuild_consumes_answer_once(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    transport = RuntimeTransport([calls(("question", "runtime__ask_user", QUESTION))])
    runtime_for(factory, settings, transport).execute(accepted.run.id, 1)
    assert {"runtime__ask_user", "runtime__wait_generation"}.issubset(
        item["function"]["name"] for item in transport.main_requests[0]["tools"]
    )
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input", run.error
        operation = session.scalar(select(AgentOperation).where(AgentOperation.run_id == run.id))
        assert operation.wait_state["interrupt_id"]
        assert operation.wait_state["consumed"] is False
        operation_id = operation.id
    receipt = answer(factory, settings, operation_id)
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    resumed = RuntimeTransport([{"text": "将采用开放结尾"}])
    runtime_for(factory, settings, resumed).execute(accepted.run.id, version)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert session.get(AgentOperation, operation_id).wait_state["consumed"] is True
    result = next(item for item in resumed.main_requests[0]["messages"] if item["role"] == "tool")
    assert json.loads(result["content"])["answer_message_id"] == str(receipt.answer_message_id)
    assert answer(factory, settings, operation_id) == receipt
    assert answer(factory, settings, operation_id, key="another-key") == receipt
    with factory() as session:
        assert (
            len(
                session.scalars(
                    select(AgentMessage).where(AgentMessage.kind == "tool_answer")
                ).all()
            )
            == 1
        )
        assert len(session.scalars(select(AgentTaskInput)).all()) == 1


@pytest.mark.parametrize("outcome", ["answered", "skipped"])
def test_question_resume_keeps_frozen_selector_and_saves_requested_scene(
    task_workspace, monkeypatch, tmp_path, outcome
):
    from dataclasses import replace

    from langchain_core.tools import BaseTool

    from short_drama.agent.tools import registry as catalog
    from short_drama.ai.prompts import registry
    from short_drama.domain import ProjectScript, ShotScript
    from short_drama.service.projects.management.project import ProjectService
    from tests.support.agent_runtime_live import acceptance_api, model_diagnostic

    factory, settings, model_id = task_workspace
    descriptions = {
        name: catalog.get_spec(name).description
        for name in (
            "task",
            "runtime__ask_user",
            "runtime__describe_creation_target",
            "workbench__read_current_script",
            "workbench__apply_standard_patch",
        )
    }
    goal = "请先向我确认结尾，取得回答后实际保存一幕结尾；若跳过，保存待确认的开放结尾。"
    with factory() as session:
        service = task_service(session, settings)
        project = ProjectService(session).create_project({"name": "问答后实际保存"})
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": goal,
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )
        frozen = (
            session.get(AgentRun, accepted.run.id)
            .config_snapshot["assistant_settings"]
            .get("tool_selector_prompt")
        )
    with factory() as session:
        service = task_service(session, settings)
        fresh_conversation = service.create_global_conversation({}, uuid4().hex)
        fresh = service.send_task_message(
            fresh_conversation.id,
            {
                "content": "讨论结尾的写作取向",
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": project.id},
            },
            uuid4().hex,
        )
    assert frozen, "选择提示词必须随同一Run冻结，跨Worker恢复不得读取部署后的新词"
    initial = RuntimeTransport(
        [calls(("question", "runtime__ask_user", QUESTION))],
        selections=[["workbench__read_current_script", "workbench__apply_standard_patch"]],
    )
    runtime_for(factory, settings, initial).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input", run.error
        question = session.scalar(select(AgentOperation).where(AgentOperation.run_id == run.id))
    with acceptance_api(factory, settings) as client:
        receipt = client.post(
            f"/assistant/operations/{question.id}/answer",
            json={
                "question_version": "1",
                "outcome": outcome,
                "values": {"ending": "雨夜团圆"} if outcome == "answered" else {},
            },
            headers={"idempotency-key": "answer"},
        )
        assert receipt.status_code == 200, receipt.text
    # 模拟部署后模板变化；恢复仍必须使用数据库中的原选择提示词。
    original = registry.load_prompt
    monkeypatch.setattr(
        registry,
        "load_prompt",
        lambda path: (
            "CHANGED_DEPLOYMENT_SELECTOR"
            if path == "assistant/tool_selection.md"
            else original(path)
        ),
    )
    deployed = {}
    for name in descriptions:
        spec = catalog.get_spec(name)
        description = "CHANGED_DEPLOYMENT_DESCRIPTION_" + name
        handler = (
            spec.handler.model_copy(update={"description": description})
            if isinstance(spec.handler, BaseTool)
            else spec.handler
        )
        deployed[name] = replace(spec, description=description, handler=handler)
        monkeypatch.setitem(catalog._CATALOG, name, deployed[name])
    monkeypatch.setattr(
        catalog, "TOOL_SPECS", tuple(deployed.get(spec.name, spec) for spec in catalog.TOOL_SPECS)
    )
    ending = "雨夜团圆" if outcome == "answered" else "待确认"

    def after_unreferenced_read(body):
        rejected = tool_result(body, "read-unreferenced-manuscript")
        assert rejected["error"]["code"] == "assistant_script_scope"
        return calls(("describe-target", "runtime__describe_creation_target", {}))

    def save_ending(body):
        target = tool_result(body, "describe-target")
        assert target["write_authorized"] is True
        return calls(
            (
                "save-ending",
                "workbench__apply_standard_patch",
                {
                    "project_id": str(project.id),
                    "patch": {
                        "storyboard_version": target["storyboard_version"],
                        "actions": [
                            {
                                "action": "create_shot",
                                "key": "ending",
                                "values": {
                                    "title": ending,
                                    "script": ending + "，父亲平安归来。",
                                },
                            }
                        ],
                    },
                },
            )
        )

    resumed = RuntimeTransport(
        [
            calls(
                (
                    "read-unreferenced-manuscript",
                    "workbench__read_current_script",
                    {
                        "project_id": str(project.id),
                        "script_id": None,
                        "content_version": str(project.content_version),
                    },
                )
            ),
            after_unreferenced_read,
            save_ending,
            calls(
                (
                    "explain-ending",
                    "task",
                    {
                        "subagent_type": "workbench-task",
                        "description": "简短说明一幕结尾的写作取向，无需读取既有对象。",
                    },
                )
            ),
            {"text": "结尾让人物的选择形成呼应。"},
            {"text": "已实际保存一幕结尾"},
        ],
        selections=[
            [
                "workbench__read_current_script",
                "runtime__describe_creation_target",
                "workbench__apply_standard_patch",
            ]
        ]
        * 6,
    )
    execute_next(task_workspace, accepted, resumed)
    evidence = {
        "expected": descriptions,
        "deployed": {name: spec.description for name, spec in deployed.items()},
        "requests": [],
    }
    for phase, transport in (("initial", initial), ("resumed", resumed)):
        for request in transport.selection_requests:
            items = request["tools"][0]["function"]["parameters"]["properties"]["tools"]["items"]
            candidates = {item["const"]: item["description"] for item in items["anyOf"]}
            evidence["requests"].append(
                {
                    "phase": phase,
                    "kind": "selector",
                    "descriptions": {
                        name: value for name, value in candidates.items() if name in descriptions
                    },
                }
            )
        for request in transport.main_requests:
            bound = {
                tool["function"]["name"]: tool["function"]["description"]
                for tool in request["tools"]
            }
            evidence["requests"].append(
                {
                    "phase": phase,
                    "kind": "root" if "task" in bound else "child",
                    "descriptions": {
                        name: value for name, value in bound.items() if name in descriptions
                    },
                }
            )
    evidence_path = tmp_path / "tool-description-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    for request in [*initial.selection_requests, *resumed.selection_requests]:
        assert request["messages"][0]["content"].startswith(frozen)
        assert (
            goal in request["messages"][-1]["content"]
            or "简短说明一幕结尾的写作取向" in request["messages"][-1]["content"]
        )
        assert "workbench__apply_standard_patch" in json.dumps(request["tools"])
    for request in evidence["requests"]:
        for name, actual in request["descriptions"].items():
            assert actual == descriptions[name], (
                request["phase"],
                request["kind"],
                name,
                evidence_path,
            )
    assert any(
        "task" not in {tool["function"]["name"] for tool in request["tools"]}
        for request in resumed.main_requests
    )
    assert (
        catalog.get_spec("runtime__ask_user").handler.description
        == deployed["runtime__ask_user"].description
    )
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        saved = session.scalars(select(ShotScript).where(ShotScript.project_id == project.id)).all()
        assert len(saved) == 1 and saved[0].title == ending and ending in saved[0].script
        assert not session.scalars(
            select(ProjectScript).where(ProjectScript.project_id == project.id)
        ).all()
        assert (
            session.get(AgentTask, accepted.task.id).effective_state.get("script_reference") is None
        )
        assert session.get(AgentOperation, question.id).wait_state["consumed"] is True
        assert (
            len(
                session.scalars(
                    select(AgentTaskInput).where(AgentTaskInput.task_id == accepted.task.id)
                ).all()
            )
            == 1
        )
        from short_drama.domain.assistant.execution.models import AgentTurn

        turn = session.scalar(
            select(AgentTurn).where(AgentTurn.usage["source"].as_string() == "tool_selection")
        )
        diagnostic = model_diagnostic(turn, "fixture-secret")
        assert "workbench__apply_standard_patch" in diagnostic["candidate_tool_names"]
    current = RuntimeTransport(
        [{"text": "已说明结尾的写作取向。"}],
        selections=[["workbench__apply_standard_patch"]],
    )
    runtime_for(factory, settings, current).execute(fresh.run.id, 1)
    assert current.selection_requests[0]["messages"][0]["content"].startswith(frozen)
    evidence["new_run"] = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in current.main_requests[0]["tools"]
        if tool["function"]["name"] in deployed
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    for tool in current.main_requests[0]["tools"]:
        function = tool["function"]
        if function["name"] in deployed:
            assert function["description"] == deployed[function["name"]].description


@pytest.mark.parametrize(
    "changes,owner,code",
    [
        ({"question_version": "2"}, 1, 409),
        ({"values": {}}, 1, 422),
        ({"values": {"unknown": "x"}}, 1, 422),
        ({}, 2, 404),
    ],
)
def test_invalid_or_foreign_answers_do_not_create_messages(task_workspace, changes, owner, code):
    _, _, operation_id = wait_question(task_workspace)
    factory, settings, _ = task_workspace
    with pytest.raises(BusinessError) as raised:
        answer(factory, settings, operation_id, owner=owner, **changes)
    assert raised.value.status_code == code
    with factory() as session:
        assert not session.scalars(
            select(AgentMessage).where(AgentMessage.kind == "tool_answer")
        ).all()


@pytest.mark.parametrize("allow_skip", [True, False])
def test_skip_is_explicit_and_never_fills_required_fields(task_workspace, allow_skip):
    _, accepted, operation_id = wait_question(
        task_workspace, {**QUESTION, "allow_skip": allow_skip}
    )
    factory, settings, _ = task_workspace
    if not allow_skip:
        with pytest.raises(BusinessError) as raised:
            answer(factory, settings, operation_id, outcome="skipped", values={})
        assert raised.value.status_code == 422
        return
    answer(factory, settings, operation_id, outcome="skipped", values={})
    resumed = RuntimeTransport([{"text": "用户选择跳过"}])
    execute_next(task_workspace, accepted, resumed)
    content = next(
        item["content"] for item in resumed.main_requests[0]["messages"] if item["role"] == "tool"
    )
    assert json.loads(content)["outcome"] == "skipped" and json.loads(content)["values"] == {}


def test_snapshot_stop_and_late_answer_keep_same_run(task_workspace):
    conversation, accepted, operation_id = wait_question(task_workspace)
    factory, settings, _ = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        snapshot = service.task_runtime_state(conversation.id)
        assert snapshot.pending_questions[0].operation_id == operation_id
        assert snapshot.pending_questions[0].can_answer
        supplement = service.send_task_message(
            conversation.id, {"task_id": accepted.task.id, "content": "新增要求"}, uuid4().hex
        )
        assert supplement.run.id == accepted.run.id
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, uuid4().hex)
        assert stopped.run.status == "cancelled"
        snapshot = service.task_runtime_state(conversation.id)
        assert snapshot.pending_questions[0].status == "cancelled"
        assert not snapshot.pending_questions[0].can_answer
    with pytest.raises(BusinessError) as raised:
        answer(factory, settings, operation_id)
    assert raised.value.status_code == 409
    from short_drama.agent.coordinator.recovery import recover

    assert recover(factory, settings) == 0


def test_delete_settles_unanswered_question_after_worker_releases_lease(task_workspace):
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.agent.coordinator.recovery import recover

    conversation, accepted, operation_id = wait_question(task_workspace)
    factory, settings, _ = task_workspace
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        operation = session.get(AgentOperation, operation_id)
        assert run.status == "waiting_input" and run.lease_token is None
        assert operation.status == "waiting_input" and operation.wait_state["interrupt_id"]
        binding = operation.wait_state
    with factory() as session:
        service = task_service(session, settings)
        current = service.get_conversation(conversation.id)
        deleted = service.delete_global_conversation(
            conversation.id, {"row_version": current.row_version}, uuid4().hex
        )
        assert deleted.status == "deleted"
    assert collect_media_results(factory, settings) == recover(factory, settings) == 0
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        task = session.get(AgentTask, accepted.task.id)
        operation = session.get(AgentOperation, operation_id)
        assert session.get(AgentConversation, conversation.id).status == "deleted"
        assert run.status == "cancelled" and run.lease_token is None
        assert run.message_status == "idle" and task.status == "stopped"
        assert operation.status == "failed" and operation.error == {"code": "agent_cancelled"}
        assert operation.wait_state == binding
        assert session.get(AgentToolCall, operation.tool_call_id).status == "cancelled"
        assert len(session.scalars(select(AgentRun)).all()) == 1


def test_delete_keeps_persistent_wait_bound_until_live_worker_exits(task_workspace, monkeypatch):
    from short_drama.agent.session.purge import purge_one
    from short_drama.service.assistant.execution.waiting import GenerationWaitService

    conversation, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    original = GenerationWaitService.bind_snapshot
    deleting = []

    async def delete_after_bind(self, snapshot, claim):
        await original(self, snapshot, claim)
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.status == "running" and run.lease_token == claim.token
        with factory() as session:
            service = task_service(session, settings)
            current = service.get_conversation(conversation.id)
            deleting.append(
                service.delete_global_conversation(
                    conversation.id, {"row_version": current.row_version}, uuid4().hex
                ).status
            )
        assert not purge_one(factory, None)
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            operation = session.scalar(
                select(AgentOperation).where(AgentOperation.run_id == run.id)
            )
            assert run.status == "running" and run.lease_token == claim.token
            assert operation.status == "waiting_input" and operation.wait_state["interrupt_id"]

    monkeypatch.setattr(GenerationWaitService, "bind_snapshot", delete_after_bind)
    runtime_for(
        factory,
        settings,
        RuntimeTransport([calls(("question", "runtime__ask_user", QUESTION))]),
    ).execute(accepted.run.id, 1)
    assert deleting == ["deleting"]
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled" and run.lease_token is None
        assert session.get(AgentConversation, conversation.id).status == "deleted"


def test_purge_removes_answer_and_question_body_from_durable_wait(task_workspace):
    from short_drama.agent.session.purge import purge_one

    secrets = ("private-question-" + uuid4().hex, "private-value-" + uuid4().hex, uuid4().hex)
    conversation, accepted, operation_id = wait_question(
        task_workspace, {**QUESTION, "question": secrets[0]}
    )
    factory, settings, _ = task_workspace
    answer(
        factory,
        settings,
        operation_id,
        values={"ending": secrets[1]},
        additional_text=secrets[2],
    )
    execute_next(task_workspace, accepted, RuntimeTransport([{"text": "完成"}]))
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        assert operation.wait_state["consumed"]
        assert operation.wait_state["answer"]["values"] == {"ending": secrets[1]}
        assert operation.wait_state["answer"]["additional_text"] == secrets[2]
        call_id = operation.tool_call_id
    with factory() as session:
        service = task_service(session, settings)
        current = service.get_conversation(conversation.id)
        assert (
            service.delete_global_conversation(
                conversation.id, {"row_version": current.row_version}, uuid4().hex
            ).status
            == "deleted"
        )
    assert purge_one(factory, None)
    assert not purge_one(factory, None)
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        assert operation.wait_state is None
        assert operation.arguments == {} and operation.result is None and operation.changes == []
        assert operation.status == "succeeded" and operation.tool_call_id == call_id
        call = session.get(AgentToolCall, call_id)
        assert call.arguments == {} and call.result is None and call.status == "succeeded"
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded" and run.task_id == accepted.task.id
        messages = session.scalars(
            select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
        ).all()
        assert messages and all(item.content == "[已删除]" for item in messages)
        retained = json.dumps(
            {
                "wait_state": operation.wait_state,
                "arguments": operation.arguments,
                "result": operation.result,
                "messages": [item.content for item in messages],
            }
        )
        assert all(secret not in retained for secret in secrets)


def test_answered_retry_after_stop_preserves_receipt_and_rejects_changed_answer(task_workspace):
    _, accepted, operation_id = wait_question(task_workspace)
    factory, settings, _ = task_workspace
    receipt = answer(factory, settings, operation_id)
    with factory() as session:
        task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, uuid4().hex
        )
    assert answer(factory, settings, operation_id) == receipt
    for key in ("answer", "changed-key"):
        with pytest.raises(BusinessError) as raised:
            answer(factory, settings, operation_id, key=key, values={"ending": "悲剧"})
        assert raised.value.status_code == 409


def test_two_parallel_questions_resume_only_the_answered_tool(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    ("first", "runtime__ask_user", QUESTION),
                    ("second", "runtime__ask_user", {**QUESTION, "title": "第二个问题"}),
                )
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == accepted.run.id)
            .order_by(AgentOperation.id)
        ).all()
        assert len({item.wait_state["interrupt_id"] for item in operations}) == 2
    answer(factory, settings, operations[0].id)
    execute_next(task_workspace, accepted, RuntimeTransport([]))
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "waiting_input"
        assert session.get(AgentOperation, operations[0].id).wait_state["consumed"]
        assert not session.get(AgentOperation, operations[1].id).wait_state["consumed"]
    answer(factory, settings, operations[1].id)
    resumed = RuntimeTransport([{"text": "两个回答都已收到"}])
    execute_next(task_workspace, accepted, resumed)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        assert len(session.scalars(select(AgentRun)).all()) == 1
    assert sum(item["role"] == "tool" for item in resumed.main_requests[0]["messages"]) == 2


@pytest.mark.parametrize("boundary", ["before_write", "after_write", "after_bind"])
def test_answer_before_worker_releases_lease_is_scheduled_once(
    task_workspace, monkeypatch, boundary
):
    from short_drama.service.assistant.execution.graph import AgentGraphService
    from short_drama.service.assistant.execution.waiting import GenerationWaitService

    _, accepted = new_task(task_workspace)
    factory, settings, _ = task_workspace
    answered = []

    def ready():
        if answered:
            return
        with factory() as session:
            operation = session.scalar(
                select(AgentOperation).where(AgentOperation.run_id == accepted.run.id)
            )
            identifier = operation.id
            before = session.get(AgentRun, accepted.run.id).message_version
        answered.append(answer(factory, settings, identifier))
        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            assert run.lease_token and run.message_version == before and run.status == "running"

    original_writes = AgentGraphService.put_writes

    def writes(self, **kwargs):
        if kwargs["interrupts"] and boundary == "before_write":
            ready()
        result = original_writes(self, **kwargs)
        if kwargs["interrupts"] and boundary == "after_write":
            ready()
        return result

    monkeypatch.setattr(AgentGraphService, "put_writes", writes)
    original_bind = GenerationWaitService.bind_snapshot

    async def bind(self, snapshot, claim):
        await original_bind(self, snapshot, claim)
        if boundary == "after_bind":
            ready()

    monkeypatch.setattr(GenerationWaitService, "bind_snapshot", bind)
    runtime_for(
        factory, settings, RuntimeTransport([calls(("question", "runtime__ask_user", QUESTION))])
    ).execute(accepted.run.id, 1)
    assert len(answered) == 1
    execute_next(task_workspace, accepted, RuntimeTransport([{"text": "收到先到的答案"}]))
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"


def test_crash_after_answer_read_before_graph_write_remains_resumable(task_workspace, monkeypatch):
    _, accepted, operation_id = wait_question(task_workspace)
    factory, settings, _ = task_workspace
    answer(factory, settings, operation_id)
    original = QuestionService.read_answer

    def crash(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise WorkerLost("lost after DB answer read")

    monkeypatch.setattr(QuestionService, "read_answer", crash)
    with pytest.raises(WorkerLost):
        execute_next(task_workspace, accepted, RuntimeTransport([]))
    with factory() as session:
        assert not session.get(AgentOperation, operation_id).wait_state["consumed"]
    monkeypatch.setattr(QuestionService, "read_answer", original)
    version = expire(task_workspace, accepted)
    runtime_for(factory, settings, RuntimeTransport([{"text": "恢复成功"}])).execute(
        accepted.run.id, version
    )
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"


def test_active_skill_restores_its_call_group_without_forgotten_answer_body(task_workspace):
    from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
    from tests.integration.standard.assistant.test_assistant_memory import (
        create_sourced_memory,
        memory,
        service,
    )
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill

    factory, settings, model = task_workspace
    skill = create_skill(
        (factory, settings, model, _Storage(factory)),
        instructions="ACTIVE_SKILL_BODY",
        resources=[],
    )
    conversation, accepted = new_task(task_workspace)
    runtime_for(
        factory,
        settings,
        RuntimeTransport(
            [
                calls(
                    (
                        "load",
                        "runtime__load_skill",
                        {
                            "name": skill.name,
                            "content_version": str(skill.content_version),
                            "purpose": "创作",
                        },
                    ),
                    ("question", "runtime__ask_user", QUESTION),
                )
            ]
        ),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        identifier = session.scalar(
            select(AgentOperation.id).where(
                AgentOperation.run_id == accepted.run.id,
                AgentOperation.tool_name == "runtime__ask_user",
            )
        )
    receipt = answer(factory, settings, identifier, values={"ending": "FORGOTTEN_ANSWER_SECRET"})

    def forget(_body, number):
        if number == 1:
            with factory() as session:
                manager = service(session)
                item = create_sourced_memory(
                    manager,
                    memory(
                        source_conversation_id=conversation.id,
                        source_message_ids=[receipt.answer_message_id],
                    ),
                )
                manager.forget_item(item.id, {"content_version": "1"})

    transport = RuntimeTransport(
        [
            calls(("policy", "runtime__get_generation_policy", {})),
            {"text": "按活动方法继续"},
        ],
        before_reply=forget,
    )
    execute_next(task_workspace, accepted, transport)
    latest = transport.main_requests[-1]["messages"]
    assert "FORGOTTEN_ANSWER_SECRET" not in json.dumps(latest)
    assert "ACTIVE_SKILL_BODY" in json.dumps(latest)
    answer_result = next(
        json.loads(item["content"])
        for item in latest
        if item["role"] == "tool" and item["tool_call_id"] == "question"
    )
    assert answer_result["content_withheld"] and answer_result["answer_message_id"] == str(
        receipt.answer_message_id
    )
    with factory() as session:
        assert (
            "FORGOTTEN_ANSWER_SECRET"
            in session.get(AgentMessage, receipt.answer_message_id).content
        )
        sources = AgentTranscriptDAO(session).input_messages(
            accepted.task.id, 1, [receipt.answer_message_id]
        )
        assert len(sources) == 1 and sources[0].id == receipt.answer_message_id


def start_question(context, accepted, scope_id, provider_call_id):
    from short_drama.agent.runtime.context import execution_scope
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService
    from tests.integration.standard.assistant.test_execution_scopes import calls_for

    identifiers = calls_for(context.factory, accepted, scope_id, tool_name="runtime__ask_user")
    with context.factory.begin() as session:
        call = session.get(AgentToolCall, identifiers[0])
        call.provider_call_id, call.arguments = provider_call_id, {"parsed": QUESTION}
    token = execution_scope.set(scope_id)
    try:
        identifier, replay = AgentToolExecutionService(context).start_tool(
            {"id": provider_call_id, "name": "runtime__ask_user", "args": QUESTION},
            {"permitted_tool_names": ["runtime__ask_user"], "applied_input_revision": 1},
        )
        assert replay is None
        return identifier
    finally:
        execution_scope.reset(token)


def test_two_real_compiled_child_scopes_bind_actual_namespaces_and_resume_by_id(task_workspace):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command

    from short_drama.agent.runtime.context import AssistantState, RuntimeContext, execution_scope
    from short_drama.agent.runtime.waiting import RuntimeQuestions
    from short_drama.agent.tools.ask_user.ask_user import runtime__ask_user
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService
    from short_drama.service.assistant.execution.waiting import GenerationWaitService
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime
    from tests.integration.standard.assistant.test_execution_scopes import calls_for

    _, accepted = new_task(task_workspace)
    saver, context = runtime(task_workspace, accepted)
    factory, settings, _ = task_workspace
    parents = calls_for(factory, accepted, context.scope_id)
    children = [context.scopes.child(context.scope_id, parent) for parent in parents[:2]]
    operations, child_calls = [], []
    for scope in children:
        identifier = start_question(context, accepted, scope.id, f"question-{scope.id}")
        operations.append(identifier)
        with factory() as session:
            operation = session.get(AgentOperation, identifier)
            child_calls.append(session.get(AgentToolCall, operation.tool_call_id).provider_call_id)

    def build():
        builder = StateGraph(AssistantState, context_schema=RuntimeContext)
        for index, scope in enumerate(children):
            child_context = replace(context, questions=RuntimeQuestions(context))

            async def settle(request, handler, bound=child_context, identifier=operations[index]):
                output = await handler(request)
                return await asyncio.to_thread(
                    AgentToolExecutionService(bound).finish_tool, identifier, output
                )

            child_builder = StateGraph(AssistantState, context_schema=RuntimeContext)
            child_builder.add_node("tools", ToolNode([runtime__ask_user], awrap_tool_call=settle))
            child_builder.add_edge(START, "tools")
            child_builder.add_edge("tools", END)
            child = child_builder.compile()

            async def branch(
                state,
                child=child,
                bound=child_context,
                call_id=child_calls[index],
                scope_id=scope.id,
            ):
                token = execution_scope.set(scope_id)
                try:
                    result = await child.ainvoke(
                        {
                            "messages": [
                                AIMessage(
                                    content="",
                                    tool_calls=[
                                        {
                                            "id": call_id,
                                            "name": "runtime__ask_user",
                                            "args": QUESTION,
                                        }
                                    ],
                                )
                            ]
                        },
                        context=bound,
                    )
                finally:
                    execution_scope.reset(token)
                return {"messages": [result["messages"][-1]]}

            name = f"child_{index}"
            builder.add_node(name, branch)
            builder.add_edge(START, name)
            builder.add_edge(name, END)
        return builder.compile(checkpointer=saver)

    config = saver._config()
    claim = SimpleNamespace(run_id=context.run_id, fence=context.fence)
    waits = GenerationWaitService(context)

    async def invoke():
        graph = build()
        await graph.ainvoke({"messages": []}, config, context=context)
        snapshot = await graph.aget_state(config, subgraphs=True)
        await waits.bind_snapshot(snapshot, claim)
        with factory() as session:
            states = [
                session.get(AgentOperation, identifier).wait_state for identifier in operations
            ]
            assert len({state["namespace"] for state in states}) == 2
            assert all(state["namespace"] and state["checkpoint_id"] for state in states)
        answer(factory, settings, operations[0])
        resumes = waits.resume_values(claim, snapshot)
        assert set(resumes) == {states[0]["interrupt_id"]}
        graph = build()
        await graph.ainvoke(Command(resume=resumes), config, context=context)
        with factory() as session:
            assert session.get(AgentOperation, operations[0]).wait_state["consumed"]
            assert not session.get(AgentOperation, operations[1]).wait_state["consumed"]
        answer(factory, settings, operations[1])
        snapshot = await graph.aget_state(config, subgraphs=True)
        await build().ainvoke(
            Command(resume=waits.resume_values(claim, snapshot)), config, context=context
        )
        with factory() as session:
            assert all(
                session.get(AgentOperation, identifier).wait_state["consumed"]
                for identifier in operations
            )

    asyncio.run(invoke())


def test_durable_tool_result_consumes_only_its_operation_when_child_call_ids_match(task_workspace):
    from langchain_core.messages import ToolMessage
    from langgraph.checkpoint.base import empty_checkpoint

    from short_drama.agent.runtime.context import execution_scope
    from short_drama.schemas.assistant.operations.questions import AskUserRequest
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime
    from tests.integration.standard.assistant.test_execution_scopes import calls_for

    _, accepted = new_task(task_workspace)
    saver, context = runtime(task_workspace, accepted)
    factory, settings, _ = task_workspace
    parents = calls_for(factory, accepted, context.scope_id)
    children = [context.scopes.child(context.scope_id, parent) for parent in parents[:2]]
    operations, outputs = [], []
    for scope in children:
        identifier = start_question(context, accepted, scope.id, "same-provider-id")
        operations.append(identifier)
        with factory() as session:
            QuestionService(session, settings).register(
                context.run_id,
                scope.id,
                "same-provider-id",
                AskUserRequest.model_validate(QUESTION),
                fence=context.fence,
            )
        answer(factory, settings, identifier, key=str(identifier))
        with factory() as session:
            result = QuestionService(session, settings).read_answer(identifier, fence=context.fence)
        token = execution_scope.set(scope.id)
        try:
            outputs.append(
                AgentToolExecutionService(context).finish_tool(
                    identifier,
                    ToolMessage(content=json.dumps(result), tool_call_id="same-provider-id"),
                )
            )
        finally:
            execution_scope.reset(token)

    config = saver.put(saver._config(namespace="child:first"), empty_checkpoint(), {}, {})
    saver.put_writes(config, [("messages", [outputs[0]])], "first-write")
    with factory() as session:
        assert session.get(AgentOperation, operations[0]).wait_state["consumed"]
        assert not session.get(AgentOperation, operations[1]).wait_state["consumed"]
    config = saver.put(saver._config(namespace="child:second"), empty_checkpoint(), {}, {})
    saver.put_writes(config, [("messages", [outputs[1]])], "second-write")
    with factory() as session:
        assert session.get(AgentOperation, operations[1]).wait_state["consumed"]
