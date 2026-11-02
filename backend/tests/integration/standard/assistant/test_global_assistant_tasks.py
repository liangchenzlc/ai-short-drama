"""Disposable MySQL task/control races; model receipts are explicit test doubles."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from short_drama.agent.tools.registry import get_spec
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import SessionActorContext
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask, AgentTaskControl
from short_drama.domain.identity.accounts.user import User
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.assistant.tasks.task import AssistantTaskService
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration


def task_actor(identifier=1):
    return SessionActorContext(
        user_id=identifier,
        username="creator",
        email="creator@example.test",
        verified=True,
        session_id=1,
        csrf_hash="hash",
        request_id="test",
    )


def _workspace(db_session):
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


@pytest.fixture
def harness_workspace(db_session):
    return _workspace(db_session)


@pytest.fixture
def task_workspace(db_session):
    return _workspace(db_session)


def task_service(session, settings, owner=1):
    session.info["actor"] = task_actor(owner)
    return AssistantTaskService(session, settings, tool_lookup=get_spec)


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


def test_state_projects_large_checkpoints_without_mysql_filesort(task_workspace):
    conversation, accepted = new_task(task_workspace)
    factory, settings, model = task_workspace
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.checkpoint = {
            **run.checkpoint,
            "history": {"image": "A" * 450000},
            "awaiting_artifacts": ["42"],
        }
        run.status, run.finished_at = "succeeded", utcnow()
        task = session.get(AgentTask, accepted.task.id)
        task.status, task.phase = "succeeded", "terminal"
        session.commit()
        service = task_service(session, settings)
        newer = service.send_task_message(
            conversation.id, {"content": "第二个任务", "model_config_id": model}, "second-task"
        )
        run = session.get(AgentRun, newer.run.id)
        run.checkpoint = {**run.checkpoint, "history": {"image": "B" * 450000}}
        session.commit()
        session.connection().exec_driver_sql("SET SESSION sort_buffer_size = 262144")
        session.commit()
        statements = []
        event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))
        state = service.task_runtime_state(conversation.id)
        assert [row.id for row in state.runs] == [accepted.run.id, newer.run.id]
        assert state.runs[0].awaiting_artifact_ids == [42]
        assert state.runs[1].model_name == "Task fixture"
        assert len(state.model_dump_json()) < 16000
        projected_statements = list(statements)
        assert session.get(AgentRun, accepted.run.id).checkpoint["history"]["image"] == (
            "A" * 450000
        )
        for statement in projected_statements:
            if "agent_runs" not in str(statement):
                continue
            columns = {str(column) for column in statement.selected_columns}
            assert "agent_runs.checkpoint" not in columns
            assert "agent_runs.config_snapshot" not in columns


def test_project_task_conversations_isolate_creation_selection_and_idempotency(task_workspace):
    projects = subject_assets(task_workspace)
    first, second = (item["project_id"] for item in projects)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        legacy = service.create_global_conversation({}, "historical-account")
        one = service.create_global_conversation({"project_id": first}, "project-one")
        two = service.create_global_conversation({"project_id": second}, "project-two")
        assert one.project_id == int(first) and two.project_id == int(second)
        assert service.create_global_conversation({"project_id": first}, "project-one").id == one.id
        with pytest.raises(Conflict):
            service.create_global_conversation({"project_id": second}, "project-one")
        assert [row.id for row in service.list_global_conversations(project_id=first).items] == [
            one.id
        ]
        assert [row.id for row in service.list_global_conversations(project_id=second).items] == [
            two.id
        ]
        assert service.get_conversation(legacy.id).project_id is None
    with factory() as session:
        service = task_service(session, settings, owner=2)
        with pytest.raises(NotFound):
            service.list_global_conversations(project_id=first)
        with pytest.raises(NotFound):
            service.create_global_conversation({"project_id": first}, "foreign-project")


def test_project_task_workers_fence_archived_projects_and_deletion_receipts_replay(task_workspace):
    from short_drama.agent.core.access import lock_run, may_decide
    from short_drama.domain import Project

    project_id = subject_assets(task_workspace)[0]["project_id"]
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation(
            {"project_id": project_id}, "project-lifecycle"
        )
        accepted = service.send_task_message(
            conversation.id, {"content": "讨论", "model_config_id": model}, "lifecycle-message"
        )
    with factory.begin() as session:
        project, row, run = lock_run(session, accepted.run.id)
        assert may_decide(session, project, row, run)
        project.archived_at = utcnow()
        session.flush()
        assert not may_decide(session, project, row, run)
    with factory() as session:
        service = task_service(session, settings)
        with pytest.raises(NotFound):
            service.get_conversation(conversation.id)
    with factory.begin() as session:
        session.get(Project, int(project_id)).archived_at = None
    with factory() as session:
        service = task_service(session, settings)
        body = {"row_version": service.get_conversation(conversation.id).row_version}
        deleted = service.delete_global_conversation(conversation.id, body, "project-delete")
        assert deleted.status == "deleted"
        assert service.delete_global_conversation(conversation.id, body, "project-delete").id == (
            conversation.id
        )


def execute_reply(workspace, accepted, *, text="讨论结果", requests=()):
    from short_drama.agent.runtime.executor import AgentRuntime
    from tests.support.assistant_runtime import RuntimeTransport, calls

    factory, settings, _model = workspace
    transport = RuntimeTransport([*([calls(*requests)] if requests else []), {"text": text}])
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, version)
    return transport


def test_discussion_revision_replaces_only_the_reply_and_preserves_versions(task_workspace):
    from short_drama.domain.assistant.conversations.models import AgentMessage

    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
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
    transport = execute_reply(task_workspace, revised, text="修订后的讨论回复")
    assert all(not request.get("tools") for request in transport.main_requests)
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
    execute_reply(task_workspace, accepted)
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
    execute_reply(
        task_workspace,
        later,
        requests=[
            ("target", "runtime__describe_creation_target", {}),
        ],
    )
    with factory() as session:
        service = task_service(session, settings)
        eligibility = service.discussion_eligibility(conversation.id, accepted.message.id)
        assert not eligibility.allowed
        assert eligibility.code == "assistant_history_dependency"


def test_discussion_revision_preserves_history_used_by_saved_memory(task_workspace):
    from short_drama.schemas.assistant.memory.memory import MemoryCreate
    from short_drama.service.assistant.memory.memory import create_memory_locked, source_rows

    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
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
    from short_drama.domain.assistant.conversations.models import AgentMessage

    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
    factory, settings, _model = task_workspace
    with factory() as session:
        revised = task_service(session, settings).revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "regenerate", "row_version": "1"},
            "discussion-regenerate",
        )
    execute_reply(
        task_workspace,
        revised,
        requests=[
            ("forbidden", "runtime__describe_creation_target", {}),
        ],
    )
    with factory() as session:
        service = task_service(session, settings)
        assert service.get_run(revised.run.id).status == "failed"
        assert session.scalar(select(func.count(AgentOperation.id))) == 0
        reply = session.scalar(select(AgentMessage).where(AgentMessage.role == "assistant"))
        assert reply.content == "讨论结果"


def test_discussion_revision_excludes_later_discussion_and_reuses_original_request(task_workspace):
    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
    factory, settings, model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        later = service.send_task_message(
            conversation.id,
            {"content": "后续私有讨论不可倒灌原回复", "model_config_id": model},
            "later",
        )
    execute_reply(task_workspace, later, text="后续助手结论不可倒灌原回复")
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
        with pytest.raises(ValidationError):
            service.control_task(
                revised.task.id, {"kind": "redirect", "content": "新方向"}, "redirect"
            )
    transport = execute_reply(task_workspace, revised)
    assert "后续私有讨论不可倒灌原回复" not in str(transport.requests)
    assert "后续助手结论不可倒灌原回复" not in str(transport.requests)


def test_discussion_revision_invalidates_summaries_and_fences_late_extraction(task_workspace):
    from short_drama.agent.memory.context import enqueue_job_locked, settle_maintenance_locked
    from short_drama.agent.memory.sources import message_sources
    from short_drama.domain.assistant.memory.memory import AgentConversationSummary, AgentMemoryJob

    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
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


def test_stopped_discussion_revision_keeps_readonly_boundary_when_requested_again(task_workspace):
    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
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
        with pytest.raises(ValidationError) as unavailable:
            service.control_task(
                revised.task.id,
                {"kind": "resume", "expected_control_seq": str(stopped.task.control_seq)},
                "resume-revision",
            )
        assert unavailable.value.errors()[0]["type"] == "literal_error"
        with pytest.raises(WorkflowError) as readonly:
            service.send_task_message(
                conversation.id,
                {"task_id": revised.task.id, "content": "继续执行新要求"},
                "cannot-convert-revision",
            )
        assert readonly.value.code == "assistant_discussion_readonly"
        resumed = service.revise_discussion(
            conversation.id,
            accepted.message.id,
            {"kind": "regenerate", "row_version": "1"},
            "request-revision-again",
        )
    transport = execute_reply(task_workspace, resumed)
    assert all(not request.get("tools") for request in transport.main_requests)
    with factory() as session:
        original = session.get(AgentRun, revised.run.id).checkpoint["discussion_revision"]
        continued = session.get(AgentRun, resumed.run.id).checkpoint["discussion_revision"]
        assert original["source_message_id"] == continued["source_message_id"]
        assert original["reply_message_id"] == continued["reply_message_id"]
        assert revised.run.id != resumed.run.id


def test_discussion_revision_rejects_cross_account_reads_and_arbitrary_message_rewrites(
    task_workspace,
):
    from short_drama.domain.assistant.conversations.history import AgentDiscussionRevision
    from short_drama.domain.assistant.conversations.models import AgentMessage

    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
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
    from short_drama.agent.session.purge import purge_one
    from short_drama.domain.assistant.conversations.history import AgentDiscussionRevision

    conversation, accepted = new_task(task_workspace)
    execute_reply(task_workspace, accepted)
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
    from short_drama.domain.assistant.operations.preference import AgentPreference
    from short_drama.service.assistant.tasks.policy import AssistantPolicyService

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
    from short_drama.domain.assistant.operations.preference import AgentPreference
    from short_drama.service.assistant.tasks.policy import AssistantPolicyService

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

    from short_drama.service.assistant.tasks.policy import AssistantPolicyService

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
    from short_drama.domain.assistant.operations.preference import AgentPreference
    from short_drama.schemas.base import UINT64_MAX
    from short_drama.service.assistant.tasks.policy import AssistantPolicyService

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
    from short_drama.service.assistant.tasks.policy import AssistantPolicyService

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


def test_stop_then_message_creates_a_new_attempt_and_preserves_control_receipts(harness_workspace):
    factory, settings, _model = harness_workspace
    conversation, accepted = new_task(harness_workspace)
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "stop")
        assert stopped.task.status == "stopped" and stopped.run.status == "cancelled"
        body = {"task_id": str(accepted.task.id), "content": "保留已保存内容，继续补充"}
        continued = service.send_task_message(conversation.id, body, "continued-input")
        assert continued.task.id == accepted.task.id and continued.run.id != accepted.run.id
        assert continued.task.status == "queued"
        replay = service.send_task_message(conversation.id, body, "continued-input")
        assert replay.message.id == continued.message.id and replay.run.id == continued.run.id
        assert service.control_task(accepted.task.id, {"kind": "stop"}, "stop").control_id == (
            stopped.control_id
        )
        stopped_again = service.control_task(accepted.task.id, {"kind": "stop"}, "stop-again")
        assert stopped_again.control_id != stopped.control_id
        assert stopped_again.task.status == "stopped"
        controls = session.scalars(
            select(AgentTaskControl)
            .where(AgentTaskControl.task_id == accepted.task.id)
            .order_by(AgentTaskControl.seq)
        ).all()
        assert [control.kind for control in controls] == ["stop", "stop"]


def test_global_event_replay_requires_resync_for_ahead_or_missing_cursors(task_workspace):
    from sqlalchemy import delete

    from short_drama.domain.assistant.conversations.models import AgentEvent
    from short_drama.service.assistant.execution.run import AgentRunService

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


def test_task_reference_guard_rejects_an_input_from_another_task(task_workspace):
    from short_drama.domain.assistant.tasks.task import AgentTaskInput
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
    from short_drama.service.creation.assets.management.assets import AssetService
    from short_drama.service.projects.management.project import ProjectService

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


def image_group_workspace(workspace, *, kind="asset"):
    from short_drama.core.config import Settings
    from short_drama.domain import MediaFile
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.media.image_groups.groups import ImageGroupService
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
    workspace, content, references, *, override=None, repeat=1, before_execute=None, item_index=2
):
    from short_drama.agent.task.state import pending_task_locked
    from tests.support.assistant_tools import scope_graph

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
    with factory() as session:
        task = pending_task_locked(session, session.get(AgentTask, accepted.task.id))
        catalog = task.context_snapshot["subjects"][0]["image_catalog"]
    group = next((g for g in catalog["groups"] if g["is_current"]), catalog["groups"][0])
    slot = next((s for s in group["items"] if s["output_index"] == item_index), group["items"][0])
    choice = {
        "project_id": references[0]["project_id"],
        "target_kind": catalog["target_kind"],
        "target_id": catalog["target_id"],
        "group_id": group["id"],
        "item_id": slot["id"],
        "expected_primary_version": group["primary_version"],
        "expected_item_version": slot["item_version"],
        "expected_target_version": catalog["target_version"],
    }
    context, _transport, operations = scope_graph(
        workspace,
        accepted,
        [
            (f"primary-{i}", "workbench__set_image_group_primary", {**choice, **(override or {})})
            for i in range(repeat)
        ],
        before_tool=(lambda: before_execute(conversation, accepted, choice))
        if before_execute
        else None,
    )
    return conversation, accepted, context, operations


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
    assert "workbench__set_image_group_primary" in claim.permitted_tool_names
    assert [operation.status for operation in operations] == ["succeeded", "failed"]
    assert operations[1].error["code"] == "image_version_conflict" and operations[1].changes == []
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
        task_workspace, "把已选图片组的第三张设为主图", [historical], item_index=3
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
        "kind": "image_slot",
        "id": groups[0]["items"][1]["id"],
        "revision": "2",
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
    assert operations[0].status == "failed"
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
        assert session.get(AgentTask, accepted.task.id).control_state == "stop_pending"
        assert session.get(AgentRun, accepted.run.id).cancel_requested


@pytest.mark.parametrize("context", ["worker", "scoped", "late_failure"])
def test_image_primary_restores_actor_scope_and_rolls_back_late_failure(
    task_workspace, monkeypatch, context
):
    from short_drama.domain import Asset, ImageGroup
    from short_drama.service.assistant.operations.image import AssistantImageService
    from short_drama.service.media.image_groups.groups import ImageGroupService

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
    assert operations[0].status == ("failed" if context == "late_failure" else "succeeded")
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
    assert operations[0].status == "failed" and "上限" in operations[0].error["message"]
    assert operations[0].changes == [] and operations[0].result_refs == []
    with factory() as session:
        assert session.get(ImageGroup, int(groups[0]["id"])).primary_index == 1


def test_image_primary_versions_feed_following_authorized_standard_edit(task_workspace):
    from short_drama.domain import Asset, Project
    from tests.support.assistant_tools import scope_graph

    reference, groups = image_group_workspace(task_workspace)
    conversation, accepted, context, operations = image_primary_run(
        task_workspace, "把已选素材的第二张设为主图", [reference]
    )
    assert operations[0].status == "succeeded"
    factory, settings, _model = task_workspace
    expected = str(int(reference["revision"]) + 1)
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id, {"content": "修改已选素材的提示词：改成雨中逆光"}, "after-primary-edit"
        )
        storyboard_version = str(
            session.get(Project, int(reference["project_id"])).storyboard_version
        )
    _, _, saved = scope_graph(
        task_workspace,
        accepted,
        [
            (
                "after-primary-edit",
                "workbench__apply_standard_patch",
                {
                    "project_id": reference["project_id"],
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
            )
        ],
        context=context,
    )
    assert saved[0].status == "succeeded", saved[0].error
    with factory() as session:
        asset = session.get(Asset, int(reference["id"]))
        assert asset.prompt == "雨中逆光" and asset.row_version == int(expected) + 1
        assert str(asset.media_id) == groups[0]["items"][1]["media_id"]


def test_standard_patch_creates_real_assets_shots_and_local_associations_in_one_transaction(
    task_workspace,
):
    from short_drama.domain import Asset, AssetVersion, Project, ShotAsset, ShotScript
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
    from short_drama.service.creation.assets.images.image import AssetImageService
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
    from tests.support.assistant_tools import scope_graph

    factory, settings, model = workspace
    target_id = str(project_id or references[0]["project_id"])
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, "standard-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": content,
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": target_id},
                "subject_selection": {"expected_input_revision": "0", "subjects": list(references)},
            },
            "standard-message",
        )
    _context, transport, operations = scope_graph(
        workspace,
        accepted,
        [
            (
                f"standard-{index}",
                "workbench__apply_standard_patch",
                {
                    "project_id": target_id,
                    **(authority or {}),
                    "patch": patch,
                },
            )
            for index, patch in enumerate(patches)
        ],
        before_tool=(lambda: before_execute(conversation, accepted)) if before_execute else None,
    )
    return conversation, accepted, transport, operations


def canvas_patch_workspace(workspace):
    from short_drama.service.canvas.document.canvas import CanvasService
    from short_drama.service.projects.management.project import ProjectService

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
    from tests.support.assistant_tools import scope_graph

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
    _context, transport, operations = scope_graph(
        workspace,
        accepted,
        [
            (
                f"canvas-{index}",
                "workbench__apply_canvas_patch",
                {
                    "project_id": str(project_id),
                    "canvas_id": canvas_id,
                    **(authority or {}),
                    "patch": patch,
                },
            )
            for index, patch in enumerate(patches)
        ],
        before_tool=(lambda: before_execute(conversation, accepted)) if before_execute else None,
    )
    return conversation, accepted, transport, operations


def test_canvas_patch_builds_four_native_nodes_and_preserves_unselected_work_and_viewport(
    task_workspace,
):
    from short_drama.domain.canvas.document.canvas import CanvasRevision
    from short_drama.service.assistant.operations.canvas import AssistantCanvasService
    from short_drama.service.canvas.document.canvas import CanvasService
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
    from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasRevision
    from short_drama.service.assistant.operations.canvas import AssistantCanvasService
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
    from short_drama.service.assistant.operations.canvas import AssistantCanvasService
    from short_drama.service.canvas.document.canvas import CanvasService
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
    from short_drama.service.assistant.operations.canvas import AssistantCanvasService
    from short_drama.service.canvas.document.canvas import CanvasService
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
    from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState
    from short_drama.schemas.base import UINT64_MAX
    from short_drama.service.assistant.operations.canvas import AssistantCanvasService
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
    from short_drama.agent.coordinator.media_runtime import collect_media_results

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
    assert collect_media_results(flow.factory, flow.settings) == 0
    return flow


def media_adoption_snapshot(flow):
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        change = next(
            item
            for item in operation.changes
            if item["kind"] not in {"canvas_document", "storyboard_document"}
        )
        reference = {
            **flow.reference,
            **change["after"],
            "id": change["id"],
            "row_version": change.get("target_version", change["after"]["row_version"]),
        }
        revision = next(
            (
                item["after"]["row_version"]
                for item in operation.changes
                if item["kind"] == "canvas_document"
            ),
            None,
        )
        return reference, revision


def fresh_media_arguments(flow, message):
    from copy import deepcopy

    from short_drama.agent.task.state import pending_task_locked

    with flow.factory() as session:
        task = pending_task_locked(session, session.get(AgentTask, message.task.id))
        request = deepcopy(flow.body["requests"][0])
        snapshot = next(
            item for item in task.context_snapshot["subjects"] if item["id"] == request["target_id"]
        )
        request["row_version"] = snapshot["revision"]
        if request["target_kind"] == "node":
            request["private_revision"] = snapshot["write_versions"]["private_revision"]
        elif request["target_kind"] == "character_voice":
            request["voice_version"] = snapshot["voice_binding"]["row_version"]
        elif request["target_kind"] == "dialogue_audio":
            request.update(
                {
                    key: snapshot["sound_binding"][key]
                    for key in ("assembly_version", "timeline_hash")
                }
            )
        return {"requests": [request]}


def continue_undo_flow_tool(flow, content, tool_name, arguments, *, key, references=None):
    from tests.support.assistant_tools import scope_graph

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
    args = arguments(message) if callable(arguments) else arguments
    args = {"project_id": flow.reference["project_id"], **args}
    if getattr(flow, "canvas_id", None):
        args.setdefault("canvas_id", flow.body["canvas_id"])
    _, _, operations = scope_graph(
        (flow.factory, flow.settings, None),
        message,
        [(key, tool_name, args)],
    )
    assert operations[0].status == "succeeded", (operations[0].error, operations[0].result)
    with flow.factory() as session:
        task_service(session, flow.settings).control_task(
            flow.accepted.task.id, {"kind": "stop"}, f"{key}-stop"
        )
    return operations[0].id


@pytest.mark.parametrize("kind", ["shot_image", "video"])
@pytest.mark.parametrize(
    "manual_change", [None, "waiting", "after_adoption", "group_after_latest", "group_after_middle"]
)
def test_batch_undo_reverse_media_after_structure_preserves_interleaved_storyboard_versions(
    task_workspace, tmp_path, kind, manual_change
):
    from copy import deepcopy
    from types import SimpleNamespace

    from short_drama.agent.coordinator.media_runtime import collect_media_results
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
        "workbench__apply_standard_patch",
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
        return fresh_media_arguments(flow, message)

    latest = continue_undo_flow_tool(
        flow,
        "为已选分镜生成一张图片：新画面"
        if kind == "shot_image"
        else "为已选分镜生成一段视频：新画面",
        "workbench__submit_generation_batch",
        arguments,
        key="latest-media",
        references=references,
    )

    def advance_manual_version():
        from short_drama.service.creation.storyboard.management.storyboard import (
            ProjectStoryboardService,
        )

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
        from short_drama.service.media.image_groups.groups import ImageGroupService

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
    assert collect_media_results(flow.factory, flow.settings) == 0
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

    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.domain import Asset
    from short_drama.service.assistant.operations.dialogue import sound_document_binding

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        ref, canvas_revision = media_adoption_snapshot(flow)
        session.rollback()
        if kind == "canvas":
            content, tool = "修改已选节点的提示词：中间正文", "workbench__apply_canvas_patch"
            patch = {
                "expected_revision": canvas_revision,
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
            content, tool = "修改已选台词的正文：中间正文", "workbench__apply_standard_patch"
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
            content, tool = "修改已选素材的提示词：中间正文", "workbench__apply_standard_patch"
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
            from short_drama.domain.canvas.document.canvas import CanvasNode

            references[0]["revision"] = str(
                session.scalar(
                    select(CanvasNode.row_version).where(
                        CanvasNode.project_id == int(ref["project_id"]),
                        CanvasNode.node_key == ref["id"],
                    )
                )
            )

    def arguments(message):
        return fresh_media_arguments(flow, message)

    latest = continue_undo_flow_tool(
        flow,
        {
            "image": "为已选素材生成一张图片：新画面",
            "canvas": "为已选图片节点生成一个结果：新画面",
            "sound": "为已选台词生成配音",
            "voice": "为已选角色生成一个音色：新的声音",
        }[kind],
        "workbench__submit_generation_batch",
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
    assert collect_media_results(flow.factory, flow.settings) == 0
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

    flow = settled_media_undo_flow(task_workspace, tmp_path, kind)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        ref, canvas_revision = media_adoption_snapshot(flow)
        session.rollback()
        if kind == "canvas":
            content = "修改已选节点的提示词：后来改写"
            tool_name = "workbench__apply_canvas_patch"
            patch = {
                "expected_revision": canvas_revision,
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
            content, tool_name = "修改已选台词的正文：后来改写", "workbench__apply_standard_patch"
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
            tool_name = "workbench__apply_standard_patch"
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
    later_id = continue_undo_flow_tool(
        flow, content, tool_name, {"patch": patch}, key="later-structure"
    )
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        preview = service.undo_preview(later_id)
        assert all(item.allowed for item in preview.items)
        service.undo_batch(
            later_id,
            {"preview_hash": preview.preview_hash, "selected_keys": [i.key for i in preview.items]},
            "undo-later-structure",
        )
        if later_edit:
            from short_drama.domain import Asset, ShotScript
            from short_drama.domain.canvas.document.canvas import CanvasNode
            from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound
    from short_drama.domain.creation.sound.voices.voices import CharacterVoice
    from short_drama.service.canvas.document.canvas import CanvasService

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
        assert original.result_refs[0]["kind"] == "generation"
        generation_id = int(original.result["items"][0]["generation_id"])
        target = session.scalar(
            select(AIGenerationRecord.config_snapshot).where(
                AIGenerationRecord.task_id == generation_id
            )
        )["business_media"]
        outputs = list(
            session.scalars(
                select(MediaAsset)
                .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
                .where(AIGenerationRecord.task_id == generation_id)
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
            metadata = target["before"]["document"]["metadata"]
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
    from short_drama.domain.canvas.document.canvas import CanvasNodeUserState
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound
    from short_drama.domain.creation.sound.voices.voices import CharacterVoice

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
    from short_drama.service.assistant.operations.image import image_adoption_state, image_target

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
        task_workspace, "把已选图片组的第三张设为主图", [historical], item_index=3
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
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.domain.creation.sound.voices.voices import CharacterVoice
    from short_drama.service.creation.sound.voices.voices import NativeVoiceService

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
    from short_drama.dao.assistant.undo.undo import AgentUndoDAO
    from short_drama.domain import Asset
    from short_drama.domain.assistant.undo.undo import AgentBatchUndo

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
    from short_drama.service.canvas.document.canvas import CanvasService

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
    from short_drama.service.canvas.document.canvas import CanvasService

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
    from short_drama.service.canvas.document.canvas import CanvasService

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
    from short_drama.domain.assistant.undo.undo import AgentBatchUndo
    from short_drama.service.canvas.document.canvas import CanvasService

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
    from short_drama.service.canvas.document.canvas import CanvasService

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

    from short_drama.domain.creation.sound.tracks.sound import ProjectSound
    from short_drama.service.creation.sound.tracks.sound import ProjectSoundService

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
    settings.minio_image_bucket = "test-images"
    settings.minio_video_bucket = "test-videos"
    settings.minio_audio_bucket = "test-audio"
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

    from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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
    from short_drama.domain.assistant.undo.undo import AgentBatchUndo

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
    from short_drama.service.creation.assets.management.assets import AssetService

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
    import short_drama.service.assistant.undo.undo as assistant_undo_service
    from short_drama.domain import Asset, Project, ShotScript
    from short_drama.domain.canvas.document.canvas import CanvasNode
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound
    from short_drama.service.assistant.operations.standard import AssistantStandardService
    from short_drama.service.canvas.document.canvas import CanvasService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
    from short_drama.service.creation.storyboard.management.storyboard import (
        ProjectStoryboardService,
    )
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.domain.creation.assembly.document.assembly import (
        ProjectAssembly,
        ProjectAssemblyClip,
    )
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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


def test_batch_undo_receipt_survives_new_run_and_is_scrubbed_with_deleted_conversation(
    task_workspace,
):
    from short_drama.agent.session.purge import purge_one
    from short_drama.domain.assistant.undo.undo import AgentBatchUndo
    from tests.integration.standard.assistant.test_agent_runtime import runtime_for
    from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

    conversation, accepted, operation = undo_test_batch(task_workspace)
    factory, settings, _model = task_workspace
    with factory() as session:
        service = task_service(session, settings)
        service.control_task(accepted.task.id, {"kind": "stop"}, "stop-resume-undo")
        preview = service.undo_preview(operation.id)
        receipt = service.undo_batch(
            operation.id,
            {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            },
            "resume-after-undo",
        )
        continued = service.send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "继续核对刚才的撤销收据，不要重建成果"},
            "continue-after-undo",
        )
    assert continued.task.id == accepted.task.id and continued.run.id != accepted.run.id
    observed = {}

    def delete_during_model(body):
        observed["undo"] = tool_result(body, "read-undo")["undo_receipts"]
        with factory() as session:
            service = task_service(session, settings)
            service.control_task(accepted.task.id, {"kind": "stop"}, "stop-after-undo-continue")
            current = service.get_conversation(conversation.id)
            service.delete_global_conversation(
                conversation.id,
                {"row_version": str(current.row_version)},
                "delete-undone-conversation",
            )
        assert not purge_one(factory, SimpleNamespace(delete=lambda locator: None))
        return {"text": "停止前的迟到响应"}

    transport = RuntimeTransport(
        [
            calls(
                (
                    "read-undo",
                    "runtime__read_operation_receipt",
                    {"operation_id": str(operation.id)},
                )
            ),
            delete_during_model,
        ]
    )
    runtime_for(factory, settings, transport).execute(continued.run.id, 1)
    assert observed["undo"][0]["id"] == str(receipt.id)
    assert purge_one(factory, SimpleNamespace(delete=lambda locator: None))
    with factory() as session:
        row = session.get(AgentBatchUndo, receipt.id)
        assert row.selected_keys == [] and row.changes == [] and row.result_refs == []
        assert not session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.conversation_id == conversation.id,
                AgentMessage.content == "停止前的迟到响应",
            )
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    from short_drama.service.assistant.operations.standard import AssistantStandardService
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
    with factory() as session:
        from short_drama.agent.task.state import pending_task_locked

        selected_snapshots = pending_task_locked(
            session, session.get(AgentTask, accepted.task.id)
        ).context_snapshot["subjects"]
    body = {
        "project_id": reference["project_id"],
        **({"canvas_id": execution_target["canvas_id"]} if native_canvas else {}),
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
    from tests.support.assistant_tools import scope_graph

    context, transport, operations = scope_graph(
        workspace,
        accepted,
        [("media-1", "workbench__submit_generation_batch", body)],
        before_tool=(lambda: before_execute(accepted, reference)) if before_execute else None,
    )
    operation_id = operations[0].id
    return SimpleNamespace(
        factory=factory,
        settings=settings,
        conversation=conversation,
        accepted=accepted,
        reference=reference,
        references=references,
        operation_id=operation_id,
        context=context,
        transport=transport,
        body=body,
    )


def media_worker(flow, provider=None, *, storage=None):
    from short_drama.service.generations.execution.execution import GenerationExecutionService
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


def drain_assistant_media(flow, worker, *, generation_ids=None):
    from short_drama.domain import AsyncTask

    if generation_ids is None:
        with flow.factory() as session:
            receipt = session.get(AgentOperation, flow.operation_id).result
            generation_ids = [int(item["generation_id"]) for item in receipt.get("items", [])]
    for identifier in generation_ids:
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
            with flow.factory() as session:
                task = session.get(AsyncTask, identifier)
                pytest.fail(
                    f"Assistant native image task did not settle: status={task.status}, "
                    f"next_action={task.next_action}, error={task.error}"
                )


@pytest.mark.parametrize("explicit_reference", [False, True])
def test_assistant_media_native_shot_adoption_does_not_send_unselected_linked_content(
    task_workspace, explicit_reference
):
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.domain import AIGenerationRecord, Asset, ImageGroup, ShotImage, ShotScript
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.creation.storyboard.references.asset import ShotAssetService

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
    assert collect_media_results(flow.factory, flow.settings) == 0 and len(provider.calls) == 1
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
        assert operation.result_refs[0]["kind"] == "generation"
        record = session.scalar(select(AIGenerationRecord))
        assert "未选入" not in str(record.request_data) and "未授权发送" not in str(
            record.request_data
        )


def canvas_media_flow(
    workspace, *, kind="image", count=1, limit=None, nodes=1, target_indices=None, **kwargs
):
    from short_drama.domain.canvas.document.canvas import CanvasNode
    from short_drama.service.canvas.document.canvas import CanvasService

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
        targets=[references[index] for index in target_indices]
        if target_indices is not None
        else None,
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
    from short_drama.service.creation.assembly.rendering.renderer import executable

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


def dialogue_audio_workspace(workspace):
    from short_drama.domain.creation.assembly.document.assembly import ProjectAssembly
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound
    from short_drama.schemas.creation.sound.tracks.sound import SoundDocument
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


def sound_text_revision(workspace, project_id):
    from short_drama.service.assistant.operations.dialogue import sound_document_binding

    factory, _settings, _model = workspace
    with factory.begin() as session:
        binding = sound_document_binding(session, project_id)
    return {key: binding[key] for key in ("row_version", "assembly_version", "timeline_hash")}


@pytest.mark.parametrize("existing_sound", [True, False])
def test_assistant_sound_text_creates_with_a_frozen_zero_or_existing_sound_version(
    task_workspace,
    existing_sound,
):
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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
    ["outside_scope", "field", "guessed_version", "timeline", "native", "disabled", "batch_limit"],
)
def test_assistant_sound_text_rejects_forged_scope_fields_versions_and_ineligible_sources(
    task_workspace,
    change,
):
    from copy import deepcopy

    from short_drama.domain.creation.assembly.document.assembly import ProjectAssembly
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound
    from short_drama.domain.creation.sound.voices.voices import ProjectSoundMode

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, settings, _text_model = task_workspace
    revision = sound_text_revision(task_workspace, refs[0]["project_id"])
    content = "修改已选台词的正文：新的正文"
    action = {"action": "update_sound_line", "line": "line_0", "patch": {"text": "新的正文"}}
    if change == "outside_scope":
        action["line"] = "line_2"
    elif change == "field":
        action["patch"]["duration_ms"] = 1000
    elif change == "guessed_version":
        revision["row_version"] = "2"
    elif change == "batch_limit":
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
                "actions": (
                    [{**action, "key": f"line_{index}"} for index in range(51)]
                    if change == "batch_limit"
                    else [action]
                ),
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
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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


def test_assistant_sound_text_noop_keeps_versions_and_returns_the_real_line(task_workspace):
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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


@pytest.mark.parametrize(
    "change", ["stale_creation_version", "missing_revision", "capacity", "stop"]
)
def test_assistant_sound_text_creation_rechecks_current_version_and_finishes_only_claimed_work(
    task_workspace,
    change,
):
    from copy import deepcopy

    from short_drama.domain.creation.sound.tracks.sound import ProjectSound

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

    def change_source(conversation, accepted):
        if change == "stop":
            with factory() as session:
                task_service(session, settings).control_task(
                    accepted.task.id, {"kind": "stop"}, "sound-started-stop"
                )
        else:
            with factory.begin() as session:
                if change == "stale_creation_version":
                    sound = session.get(ProjectSound, assembly_id)
                    document = deepcopy(sound.document)
                    document["subtitles"][0]["text"] = "后来的人工字幕"
                    sound.document, sound.row_version = document, 2

    _conversation, accepted, _claim, operations = standard_tool_run(
        task_workspace,
        "创建一条台词",
        [
            {
                **({"sound_revision": revision} if change != "missing_revision" else {}),
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
        from short_drama.service.assistant.execution.run import AgentRunService

        with factory() as session:
            run = session.get(AgentRun, accepted.run.id)
            claim = SimpleNamespace(run_id=run.id, fence=(run.message_version, run.lease_token))
        with factory() as session:
            AgentRunService(session, settings, tool_lookup=get_spec).finish(
                claim, status="cancelled"
            )
        with factory() as session:
            assert session.get(AgentTask, accepted.task.id).status == "stopped"
            assert session.get(ProjectSound, assembly_id).row_version == 2
    else:
        assert operation.status in {"conflict", "failed"}, operation.result
        assert operation.changes == [] and operation.result_refs == []
        with factory() as session:
            sound = session.get(ProjectSound, assembly_id)
            assert len(sound.document["dialogue"]) == (300 if change == "capacity" else 3)
            assert sound.row_version == (2 if change == "stale_creation_version" else 1)
