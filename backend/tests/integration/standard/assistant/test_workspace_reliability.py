"""Workspace regressions on disposable MySQL; the model protocol is a local double."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.agent.harness.runtime import HarnessRuntime
from short_drama.agent.task.contract import ConfirmRequirements
from short_drama.agent.task.requirements import confirm_locked, load_task_requirements
from short_drama.agent.task.state import apply_inputs_locked
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.agent import AgentConversation, AgentRun
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.domain.agent_task_requirement import AgentTaskRequirement
from short_drama.service.project_service import ProjectService
from short_drama.service.project_writing_service import ProjectWritingService
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    task_actor,
    task_service,
)
from tests.integration.standard.assistant.test_harness_runtime import advance, configured
from tests.unit.standard.agent.test_agent_model_gateway import provider as provider

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


def test_new_messages_have_unique_drafts_and_explicit_revision_replaces_old_target(task_workspace):
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id, {"content": "创建10个分镜", "model_config_id": model}, uuid4().hex
        )
        updated = service.send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "分镜改为5个，补充一个角色"},
            uuid4().hex,
        )
    apply_inputs(task_workspace, updated)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        drafts = session.scalars(
            select(AgentTaskRequirement).where(AgentTaskRequirement.task_id == task.id)
        ).all()
        assert len({row.requirement_key for row in drafts}) == len(drafts)
        original = next(row for row in drafts if row.source_message_id == accepted.message.id)
        original.state = "confirmed"
        session.flush()
        values = ConfirmRequirements.model_validate(
            {
                "additions": [
                    {
                        "key": original.requirement_key,
                        "description": "按新指令改为五个分镜",
                        "kind": "write",
                        "source_message_id": str(updated.message.id),
                        "source_quote": updated.message.content,
                        "minimum_count": 5,
                        "artifact_kind": "shot",
                    }
                ]
            }
        )
        result = confirm_locked(
            session, conversation, task, SimpleNamespace(result_refs=[]), values, settings
        )
        active = load_task_requirements(session, task)
        assert original.state == "superseded"
        assert len(active) == 1 and active[0].minimum_count == 5
        assert result["added"] == [active[0].requirement_key]
        with pytest.raises(WorkflowError, match="替代"):
            confirm_locked(
                session, conversation, task, SimpleNamespace(result_refs=[]), values, settings
            )


@pytest.mark.parametrize("correction", ["改为雨夜悬疑", "分镜改为一百个", "改为5个"])
def test_revision_cannot_invent_quantity_or_reuse_the_original_input(task_workspace, correction):
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
    apply_inputs(task_workspace, updated)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        original = session.scalar(
            select(AgentTaskRequirement).where(
                AgentTaskRequirement.source_message_id == accepted.message.id
            )
        )
        original.state = "confirmed"
        session.flush()
        if correction == "改为5个":
            source, quote, count = accepted.message, accepted.message.content, 5
        else:
            source, quote, count = updated.message, correction, 1
        values = ConfirmRequirements.model_validate(
            {
                "additions": [
                    {
                        "key": original.requirement_key,
                        "description": "不能凭空缩减",
                        "kind": "write",
                        "source_message_id": str(source.id),
                        "source_quote": quote,
                        "minimum_count": count,
                        "artifact_kind": "shot",
                    }
                ]
            }
        )
        with pytest.raises(WorkflowError, match="明确新数量"):
            confirm_locked(
                session, conversation, task, SimpleNamespace(result_refs=[]), values, settings
            )
        assert original.minimum_count == 10 and original.state == "confirmed"


def proposal(task_workspace, provider, *, empty=False):
    state = configured(task_workspace, provider)
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
    runtime = HarnessRuntime(factory, settings)
    advance(runtime, accepted, state, calls=[("read-script", "read_current_script", {})])
    patch = {
        "content_version": reference["content_version"],
        "script_id": reference["script_id"],
        "start": 0 if empty else 2,
        "end": 0 if empty else 4,
        "replacement": "新稿",
    }
    advance(runtime, accepted, state, calls=[("propose-script", "propose_script_patch", patch)])
    with factory() as session:
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.task_id == accepted.task.id,
                AgentOperation.tool_name == "propose_script_patch",
            )
        )
        assert operation.status == "succeeded", operation.error
        return project.id, operation.id, document, reference


@pytest.mark.parametrize("empty", [False, True])
def test_proposal_requires_adoption_then_is_idempotent_and_recoverable(
    task_workspace, provider, empty
):
    project_id, operation_id, document, reference = proposal(task_workspace, provider, empty=empty)
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
        restored = writing.restore_script_revision(
            project_id, history[0]["id"], {"content_version": current["content_version"]}
        )
        assert restored["editing_script"]["content"] == ("" if empty else "前😀原文后")
        assert len(writing.script_revisions(project_id)["items"]) == 2
    with factory() as session:
        with pytest.raises(NotFound):
            task_service(session, settings, owner=2).script_proposal(operation_id)


def test_stale_proposal_never_overwrites_later_typing(task_workspace, provider):
    project_id, operation_id, document, reference = proposal(task_workspace, provider)
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
