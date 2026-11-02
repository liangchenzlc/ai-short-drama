"""Workspace regressions on disposable MySQL; the model protocol is a local double."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.agent.runtime.executor import AgentRuntime
from short_drama.agent.task.state import apply_inputs_locked
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.service.creation.writing.script.documents import ProjectWritingService
from short_drama.service.projects.management.project import ProjectService
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    task_actor,
    task_service,
)
from tests.support.assistant_runtime import RuntimeTransport, calls

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def apply_inputs(workspace, accepted):
    factory, _settings, _model = workspace
    with factory.begin() as session:
        session.info["actor"] = task_actor()
        task = session.get(AgentTask, accepted.task.id)
        apply_inputs_locked(
            session,
            session.get(AgentConversation, task.conversation_id),
            task,
            session.get(AgentRun, accepted.run.id),
        )


def test_stopped_harness_message_creates_one_fresh_attempt_and_replays(task_workspace):
    conversation, accepted = new_task(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, uuid4().hex)
        assert stopped.task.status == "stopped"
        body = {"task_id": accepted.task.id, "content": "继续完善人物动机"}
        resumed = service.send_task_message(conversation.id, body, "continue-original")
        replay = service.send_task_message(conversation.id, body, "continue-original")
        assert resumed.task.id == accepted.task.id
        assert resumed.task.status == "queued" and resumed.run.id != accepted.run.id
        assert replay.run.id == resumed.run.id and replay.message.id == resumed.message.id
        assert (
            len(session.scalars(select(AgentRun).where(AgentRun.task_id == accepted.task.id)).all())
            == 2
        )


@pytest.mark.parametrize("correction", ["改为雨夜悬疑", "分镜改为一百个", "改为5个"])
def test_user_corrections_keep_the_original_text_and_are_consumed_in_order(
    task_workspace, correction
):
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id, {"content": "创建10个分镜", "model_config_id": model}, uuid4().hex
        )
        updated = service.send_task_message(
            conversation.id, {"task_id": accepted.task.id, "content": correction}, uuid4().hex
        )
    transport = RuntimeTransport([{"text": "按用户最新更正继续"}])
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    contents = [message["content"] for message in transport.main_requests[0]["messages"]]
    assert contents.count(accepted.message.content) == contents.count(correction) == 1
    assert contents.index(accepted.message.content) < contents.index(correction)
    with factory() as session:
        inputs = session.scalars(
            select(AgentTaskInput)
            .where(AgentTaskInput.task_id == accepted.task.id)
            .order_by(AgentTaskInput.input_revision)
        ).all()
        assert [(item.input_revision, item.message_id) for item in inputs] == [
            (1, accepted.message.id),
            (2, updated.message.id),
        ]
        task = session.get(AgentTask, accepted.task.id)
        assert task.applied_input_revision == task.input_revision == 2
        assert "task_contract" not in task.effective_state


def proposal(task_workspace, *, empty=False):
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        project = ProjectService(session).create_project({"name": "剧本建议测试"})
        writing = ProjectWritingService(session)
        document = writing.get(project.id)
        if not empty:
            writing.save_script(
                project.id, {"content_version": "1", "script_id": None, "content": "前😀原文后"}
            )
            document = writing.get(project.id)
        reference = {
            "project_id": project.id,
            "script_id": document["editing_script"]["id"] if document["editing_script"] else None,
            "content_version": document["content_version"],
        }
        if not empty:
            reference.update(start=2, end=4)
        conversation = service.create_global_conversation({"project_id": project.id}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {"content": "改写剧本", "model_config_id": model, "script_reference": reference},
            uuid4().hex,
        )
    patch = {
        "content_version": reference["content_version"],
        "script_id": reference["script_id"],
        "start": 0 if empty else 2,
        "end": 0 if empty else 4,
        "replacement": "新稿",
    }
    transport = RuntimeTransport(
        [
            calls(("read-script", "workbench__read_current_script", reference)),
            calls(("propose-script", "runtime__propose_script_patch", patch)),
            {"text": "建议已准备，等待用户采用"},
        ]
    )
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "runtime__propose_script_patch",
            )
        )
        assert operation.status == "succeeded", operation.error
        return project.id, operation.id, document, reference


@pytest.mark.parametrize("empty", [False, True])
def test_proposal_requires_adoption_then_is_idempotent_and_recoverable(task_workspace, empty):
    project_id, operation_id, document, reference = proposal(task_workspace, empty=empty)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        writing = ProjectWritingService(session)
        assert writing.get(project_id) == document, (
            "Preparing a proposal must not mutate the script"
        )
        preview = service.script_proposal(operation_id)
        assert preview["before"] == ("" if empty else "原文") and preview["after"] == "新稿"
        receipt = service.apply_script_proposal(
            operation_id, {"content_version": reference["content_version"]}
        )
        assert (
            service.apply_script_proposal(
                operation_id, {"content_version": reference["content_version"]}
            )
            == receipt
        )
        current = writing.get(project_id)
        assert current["editing_script"]["content"] == ("新稿" if empty else "前😀新稿后")
        history = writing.script_revisions(project_id)["items"]
        assert len(history) == 1 and history[0]["source"] == "assistant"
        snapshot = writing.script_revisions(project_id, revision_id=history[0]["id"])
        assert snapshot["before_content"] == ("" if empty else "前😀原文后")
        assert snapshot["after_content"] == current["editing_script"]["content"]
    with factory() as session:
        with pytest.raises(NotFound):
            task_service(session, settings, owner=2).script_proposal(operation_id)


def test_stale_proposal_never_overwrites_later_typing(task_workspace):
    project_id, operation_id, document, reference = proposal(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        writing = ProjectWritingService(session)
        writing.save_script(
            project_id,
            {
                "content_version": document["content_version"],
                "script_id": reference["script_id"],
                "content": "用户后续修改",
            },
        )
        with pytest.raises(WorkflowError) as error:
            service.apply_script_proposal(
                operation_id, {"content_version": reference["content_version"]}
            )
        assert error.value.code == "assistant_script_changed"
        assert writing.get(project_id)["editing_script"]["content"] == "用户后续修改"


def test_stop_waits_for_every_inflight_operation(task_workspace):
    from short_drama.agent.task.controls import apply_controls_locked
    from short_drama.core.time import utcnow
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.service.assistant.execution.scope import AgentExecutionScopeService
    from short_drama.utils.snowflake import next_id
    from tests.integration.standard.assistant.test_execution_scopes import calls_for
    from tests.integration.standard.assistant.test_graph_checkpointer import bound

    conversation, accepted = new_task(task_workspace)
    graph = bound(task_workspace, accepted)
    root = AgentExecutionScopeService(graph).root()
    calls = calls_for(graph.factory, accepted, root.id)
    with graph.factory.begin() as session:
        session.get(AgentRun, accepted.run.id).status = "running"
        for call_id in calls:
            session.add(
                AgentOperation(
                    id=next_id(),
                    task_id=accepted.task.id,
                    run_id=accepted.run.id,
                    scope_id=root.id,
                    tool_call_id=call_id,
                    tool_name="task",
                    plan_revision=1,
                    input_revision=1,
                    payload_hash="a" * 64,
                    arguments={},
                    result_refs=[],
                    changes=[],
                )
            )
    _, settings, _ = task_workspace
    with graph.factory() as session:
        response = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, uuid4().hex
        )
        assert response.task.control_state == "stop_pending"
        assert response.run.status == "running"
    with graph.factory() as session:
        assert session.get(AgentRun, accepted.run.id).cancel_requested == 1
    for index, call_id in enumerate(calls):
        with graph.factory.begin() as session:
            task = session.get(AgentTask, accepted.task.id)
            run = session.get(AgentRun, accepted.run.id)
            conv = session.get(AgentConversation, conversation.id)
            op = session.scalar(
                select(AgentOperation).where(AgentOperation.tool_call_id == call_id)
            )
            op.status, op.phase, op.finished_at = "succeeded", "terminal", utcnow()
            session.get(AgentToolCall, call_id).status = "succeeded"
            session.flush()
            applied = apply_controls_locked(session, conv, task, run)
            assert applied is (index == 2)
            assert task.control_state == ("none" if index == 2 else "stop_pending")
    with graph.factory() as session:
        assert session.get(AgentTask, accepted.task.id).status == "stopped"
