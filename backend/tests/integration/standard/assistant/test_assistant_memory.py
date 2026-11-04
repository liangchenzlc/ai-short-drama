"""Real isolated MySQL memory scope, ngram retrieval and stale-source protection."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from short_drama.agent.core.access import LeaseLost
from short_drama.agent.memory.context import (
    enqueue_job_locked,
    extraction_waterline,
    history_projection_locked,
    schedule_maintenance_locked,
    settle_maintenance_locked,
)
from short_drama.agent.memory.runtime import (
    AgentMemoryPublisher,
    AgentMemoryRuntime,
    AgentMemoryStore,
)
from short_drama.agent.memory.sources import message_sources
from short_drama.agent.task.state import apply_inputs_locked
from short_drama.agent.tools.registry import get_spec
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.core.identity import SessionActorContext
from short_drama.dao.assistant.memory.memory import MemoryScopeContext
from short_drama.domain import MediaFile
from short_drama.domain.assistant.context.context import AgentAttachment
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.domain.assistant.memory.memory import AgentMemoryItem, AgentMemoryJob
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.schemas.assistant.memory.memory import MemoryCreate, MemoryJobRead
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.assistant.memory.memory import (
    AgentMemoryService,
    create_memory_locked,
    memory_catalog_locked,
    memory_job_read,
    read_memories_locked,
    search_memory_locked,
    source_rows,
)
from short_drama.service.assistant.tasks.task import AssistantTaskService
from short_drama.service.base import utcnow
from short_drama.service.projects.management.project import ProjectService
from short_drama.utils.snowflake import next_id
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    new_task,
    task_service,
)

pytestmark = pytest.mark.integration


def test_formal_graph_uses_summary_before_501_history_messages(harness_workspace):
    from tests.integration.standard.assistant.test_agent_runtime import runtime_for
    from tests.support.assistant_runtime import RuntimeTransport

    factory, settings, model = harness_workspace
    conversation_read, accepted = new_task(harness_workspace)
    runtime_for(factory, settings, RuntimeTransport([{"text": "已记下"}])).execute(
        accepted.run.id, 1
    )
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversation_read.id)
        for seq in range(conversation.next_message_seq, 503):
            session.add(
                AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    task_id=accepted.task.id,
                    seq=seq,
                    role="user",
                    memory_capture_generation=0,
                    content=f"历史约束{seq}",
                    references=[],
                    artifacts=[],
                    created_at=utcnow(),
                )
            )
        conversation.next_message_seq = 503
        session.flush()
        run = session.get(AgentRun, accepted.run.id)
        rows = message_sources(session, conversation.id, 1, 10)
        job = enqueue_job_locked(session, conversation, run, "summary", rows)
        session.flush()
        assert settle_maintenance_locked(
            session,
            conversation,
            job,
            {
                "facts": [
                    {
                        "kind": "user_fact",
                        "text": "保留长期镜头约束",
                        "source_message_ids": [row["id"] for row in rows if row["role"] == "user"],
                    }
                ]
            },
            {},
        )
        from short_drama.agent.context.graph import _initial_messages

        historical = {"messages": []}
        _initial_messages(
            session,
            conversation,
            session.get(AgentTask, accepted.task.id),
            run,
            {"messages": []},
            historical,
            [],
        )
        assert "保留长期镜头约束" not in json.dumps(
            [message.content for message in historical["messages"]], ensure_ascii=False
        )
    with factory() as session:
        newer = task_service(session, settings).send_task_message(
            conversation_read.id, {"content": "继续", "model_config_id": model}, "long-next"
        )
    transport = RuntimeTransport([{"text": "按长期约束继续"}])
    runtime_for(factory, settings, transport).execute(newer.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, newer.run.id)
        assert run.status == "succeeded", run.error
    wire = json.dumps(transport.main_requests, ensure_ascii=False)
    assert "保留长期镜头约束" in wire and "历史约束502" in wire
    with factory() as session:
        manager = service(session)
        item = create_sourced_memory(
            manager,
            memory(
                source_conversation_id=conversation_read.id,
                source_message_ids=[accepted.message.id],
            ),
        )
        manager.forget_item(item.id, {"content_version": "1"})
        assert manager.conversation_context(conversation_read.id)["summary"] is not None
        last = task_service(session, settings).send_task_message(
            conversation_read.id,
            {"content": "核对当前约束", "model_config_id": model},
            "after-summary-withdrawal",
        )
    withdrawn = RuntimeTransport([{"text": "保留当前对话摘要"}])
    runtime_for(factory, settings, withdrawn).execute(last.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, last.run.id)
        assert run.status == "succeeded", run.error
    assert "保留长期镜头约束" in json.dumps(withdrawn.main_requests, ensure_ascii=False)


def actor(owner=1):
    return SessionActorContext(
        user_id=owner,
        username="creator",
        email="creator@example.test",
        verified=True,
        session_id=1,
        csrf_hash="hash",
        request_id="test",
    )


@pytest.fixture
def memory_workspace(db_session):

    db_session.info["actor"] = actor()
    projects = [
        ProjectService(db_session).create_project({"name": name}).id
        for name in ("甲作品", "乙作品")
    ]
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    settings = SimpleNamespace(agent_enabled=True)
    conversations = []
    for index in range(2):
        with factory() as session:
            session.info["actor"] = actor()
            conversations.append(
                AssistantTaskService(session, settings, tool_lookup=get_spec)
                .create_global_conversation({}, f"conversation-{index}")
                .id
            )
    yield factory, projects, conversations


def service(session, owner=1):
    session.info["actor"] = actor(owner)
    return AgentMemoryService(session)


def memory(**fields):
    return {
        "kind": "preference",
        "memory_key": "对白节奏",
        "title": "对白节奏",
        "content": "对白简短，转折明确",
        **fields,
    }


def test_account_memory_is_shared_between_conversations_and_private_to_its_owner(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        item = manager.create_item(memory(), "account-memory")
        assert [entry.id for entry in manager.list_items().items] == [item.id]
        assert (
            not {"scope_type", "project_id", "conversation_id", "sources"}
            & type(item).model_fields.keys()
        )
        with pytest.raises(NotFound):
            service(session, 2).get_item(item.id)
        assert service(session, 2).list_items().total == 0


def test_chinese_alias_short_word_and_ngram_search_retrieve_real_ids(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        item = manager.create_item(
            memory(
                memory_key="对白",
                title="对白偏好",
                aliases=["短句"],
                tags=["风格"],
                content="以后所有作品对白短促，转折明确",
            ),
            "search",
        )
        with session.begin():
            for query in ("短句", "短", "转折", "对白"):
                found = search_memory_locked(session, MemoryScopeContext(1), query)
                assert str(item.id) in {entry["id"] for entry in found["items"]}
            assert search_memory_locked(session, MemoryScopeContext(2), "对白")["items"] == []


def test_versions_and_read_statistics_do_not_change_catalog_identity(memory_workspace):
    factory, _projects, conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        item = manager.create_item(memory(), "create")
        assert manager.create_item(memory(), "create").id == item.id
        with session.begin():
            scope = MemoryScopeContext(1)
            before = memory_catalog_locked(session, scope)["catalog_version"]
            read = read_memories_locked(
                session, scope, [item.id], versions={str(item.id): "1"}, character_limit=4
            )
            assert read["items"][0]["range"]["next_offset"] == 4
            assert memory_catalog_locked(session, scope)["catalog_version"] == before
        updated = manager.patch_item(
            item.id, {"content_version": "1", "content": "修改为更短的对白"}
        )
        assert updated.content_version == 2
        with pytest.raises(Conflict):
            manager.patch_item(item.id, {"content_version": "1", "content": "过期编辑"})
        assert len(manager.revisions(item.id)) == 2
        with session.begin(), pytest.raises(Conflict):
            read_memories_locked(
                session, MemoryScopeContext(1), [item.id], versions={str(item.id): "1"}
            )


def test_forget_blocks_late_extraction_and_requires_explicit_restore(memory_workspace):
    factory, _projects, conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        item = manager.create_item(memory(), "remember")
        forgotten = manager.forget_item(item.id, {"content_version": "1"})
        assert forgotten.state == "forgotten" and manager.list_items().total == 0
        with session.begin():
            assert (
                create_memory_locked(
                    session, 1, MemoryCreate.model_validate(memory()), origin="extracted"
                )
                is None
            )
        restored = manager.restore_item(item.id, {"content_version": "2"})
        assert restored.state == "active" and restored.content_version == 3
        assert manager.list_items().total == 1


def test_background_candidates_cannot_replace_an_explicit_correction(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        item = manager.create_item(memory(), "original")
        manager.patch_item(item.id, {"content_version": "1", "content": "后来的明确纠正"})
        with session.begin():
            result = create_memory_locked(
                session,
                1,
                MemoryCreate.model_validate(memory(content="旧任务迟到")),
                origin="extracted",
            )
            assert result is None
        assert manager.get_item(item.id).content == "后来的明确纠正"


@pytest.mark.parametrize("choice", ["keep_current", "use_candidate"])
def test_extraction_conflicts_are_persisted_and_require_a_versioned_choice(
    memory_workspace, choice
):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory() as session:
        item = service(session).create_item(
            memory(memory_key="dialogue"),
            "existing",
        )
    identifier = maintenance_job(memory_workspace, accepted)
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        job = session.get(AgentMemoryJob, identifier)
        assert settle_maintenance_locked(
            session,
            conversation,
            job,
            {
                "memories": [
                    {
                        "kind": "preference",
                        "memory_key": "dialogue",
                        "title": "以后对白更短",
                        "content": "以后每句对白不超过十个字",
                        "source_message_ids": [str(accepted.message.id)],
                    }
                ]
            },
            {},
        )
    with factory() as session:
        manager = service(session)
        assert manager.get_item(item.id).content_version == 1
        page = manager.review_jobs(conversations[0])
        assert page.total == 1
        assert manager.review_jobs(conversations[0], offset=1).items == []
        candidate = page.items[0]["clarifications"][0]
        assert candidate["status"] == "pending"
        assert candidate["memory_id"] == str(item.id)
        assert candidate["candidate_content"] == "以后每句对白不超过十个字"
        with pytest.raises(NotFound):
            service(session, 2).review_jobs(conversations[0])
        manager = service(session)
        manager.patch_item(item.id, {"content_version": "1", "content": "预览后人工修改"})
        with pytest.raises(Conflict):
            manager.resolve_clarification(
                identifier, 0, {"content_version": "1", "choice": choice}, "choice"
            )
        result = manager.resolve_clarification(
            identifier, 0, {"content_version": "2", "choice": choice}, "choice"
        )
        assert result["clarifications"][0]["status"] == "resolved"
        assert (
            manager.resolve_clarification(
                identifier, 0, {"content_version": "2", "choice": choice}, "choice"
            )
            == result
        )
        latest = manager.get_item(item.id)
        assert latest.content == (
            "以后每句对白不超过十个字" if choice == "use_candidate" else "预览后人工修改"
        )
        assert latest.content_version == (3 if choice == "use_candidate" else 2)
        assert "sources" not in latest.model_dump()
        manager.forget_item(latest.id, {"content_version": latest.content_version})
        reviewed = manager.review_jobs(conversations[0]).items[0]["clarifications"][0]
        assert reviewed["current_content"] is None and reviewed["candidate_content"] is None
        with pytest.raises(Conflict):
            manager.resolve_clarification(
                identifier,
                0,
                {"content_version": str(latest.content_version + 1), "choice": "use_candidate"},
                "revive",
            )


def test_scope_write_guard_rejects_owner_changes_and_cross_account_rows(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    with factory() as session:
        item = service(session).create_item(memory(), "owned")
        session.info["actor"] = actor(2)
        with session.begin():
            assert (
                session.scalar(select(AgentMemoryItem).where(AgentMemoryItem.id == item.id)) is None
            )
        session.info["actor"] = actor()
        with pytest.raises(NotFound), session.begin():
            row = session.get(AgentMemoryItem, item.id)
            row.owner_user_id = 2
            session.flush()


def seeded_run(workspace, *, count=10):
    factory, _projects, conversations = workspace
    with factory() as session:
        session.info["actor"] = actor()
        model = AIModelConfigService(session).create(
            {
                "service_type": "text",
                "name": "Memory fixture",
                "model_key": "fixture",
                "provider": "compatible",
                "base_url": "https://model.invalid/v1",
            }
        )
        accepted = AssistantTaskService(
            session, SimpleNamespace(agent_enabled=True), tool_lookup=get_spec
        ).send_task_message(
            conversations[0], {"content": "以后对白要短", "model_config_id": model.id}, "seed"
        )
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        task = session.get(AgentTask, accepted.task.id)
        apply_inputs_locked(session, conversation, task, run)
        run.status = task.status = "succeeded"
        run.finished_at = task.finished_at = utcnow()
        for seq in range(2, count + 1):
            session.add(
                AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    task_id=task.id,
                    seq=seq,
                    role="user",
                    memory_capture_generation=0,
                    content=f"约束 {seq}",
                    references=[],
                    artifacts=[],
                    created_at=utcnow(),
                )
            )
        conversation.next_message_seq = count + 1
    return accepted


def test_deleting_a_conversation_preserves_account_memory(memory_workspace):
    from short_drama.agent.session.purge import purge_one

    factory, _projects, conversations = memory_workspace
    with factory() as session:
        item = service(session).create_item(memory(), "survive-delete")
        tasks = AssistantTaskService(
            session, SimpleNamespace(agent_enabled=True), tool_lookup=get_spec
        )
        conversation = tasks.get_conversation(conversations[0])
        tasks.delete_global_conversation(
            conversation.id, {"row_version": str(conversation.row_version)}, "delete"
        )
    purge_one(factory, None)
    with factory() as session:
        assert service(session).get_item(item.id).content == item.content


def test_purge_releases_only_owned_unreferenced_uploads_and_retries_storage(memory_workspace):
    from short_drama.agent.session.purge import purge_one
    from short_drama.domain.identity.accounts.user import User

    factory, _projects, conversations = memory_workspace
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="other_creator",
                display_name="Other creator",
                email="other@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        session.flush()
        media = []
        for name in ("orphan", "shared", "other-owner"):
            row = MediaFile(
                id=next_id(),
                scope_user_id=2 if name == "other-owner" else 1,
                project_id=None,
                format_code="image/png",
                storage_locator=f"minio://images/{name}.png",
                byte_size=8,
                checksum_sha256="a" * 64,
            )
            session.add(row)
            media.append(row)
        session.flush()
        for index, (conversation_id, media_id) in enumerate(
            [
                (conversations[0], media[0].id),
                (conversations[0], media[1].id),
                (conversations[1], media[1].id),
                (conversations[0], media[2].id),
            ]
        ):
            session.add(
                AgentAttachment(
                    id=next_id(),
                    owner_user_id=1,
                    conversation_id=conversation_id,
                    kind="image",
                    name=f"image-{index}",
                    mime_type="image/png",
                    media_id=media_id,
                    checksum_sha256="a" * 64,
                    byte_size=8,
                    input_metadata={},
                    deleted_at=utcnow() if media_id == media[2].id else None,
                    created_at=utcnow(),
                )
            )
        orphan_id, shared_id, other_owner_id = (row.id for row in media)
    with factory() as session:
        session.info["actor"] = actor()
        tasks = AssistantTaskService(
            session, SimpleNamespace(agent_enabled=True), tool_lookup=get_spec
        )
        current = tasks.get_conversation(conversations[0])
        tasks.delete_global_conversation(
            conversations[0], {"row_version": current.row_version}, "purge"
        )

    class Storage:
        calls = []

        def delete(self, locator):
            self.calls.append(locator)
            if len(self.calls) == 1:
                raise ConnectionError("storage fixture unavailable")

    storage = Storage()
    assert purge_one(factory, storage)
    with factory.begin() as session:
        assert session.get(MediaFile, orphan_id) is None
        assert session.get(MediaFile, shared_id) is not None
        assert session.get(MediaFile, other_owner_id) is not None
        assert len(session.scalars(select(AgentAttachment)).all()) == 1
        job = session.scalar(select(AgentMemoryJob).where(AgentMemoryJob.kind == "purge"))
        assert job.status == "pending" and job.error["code"] == "agent_purge_storage_unavailable"
        job.next_run_at = utcnow() - timedelta(seconds=1)
    assert purge_one(factory, storage)
    assert storage.calls == ["minio://images/orphan.png", "minio://images/orphan.png"]
    assert not purge_one(factory, storage)


def test_late_model_receipts_cannot_restore_purged_conversation_content(memory_workspace):
    from short_drama.agent.gateway.contracts import AgentGatewayError
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.agent.session.purge import purge_one
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        task = session.get(AgentTask, accepted.task.id)
        run.status, run.finished_at = "queued", None
        task.status, task.finished_at = "queued", None
    _saver, context = runtime((factory, runtime_settings(), accepted.run.model_config_id), accepted)
    ledger = RunModelLedger(context)
    ledger.before_send(
        {
            "model_call_id": "late-private",
            "source": "assistant",
            "body": {"max_tokens": 8, "messages": [{"role": "user", "content": "私有"}]},
        }
    )
    with factory() as session:
        session.info["actor"] = actor()
        tasks = AssistantTaskService(session, runtime_settings(), tool_lookup=get_spec)
        current = tasks.get_conversation(conversations[0])
        tasks.delete_global_conversation(current.id, {"row_version": current.row_version}, "purge")
    assert purge_one(factory, None)
    with pytest.raises(AgentGatewayError, match="agent_admission_denied"):
        ledger.save_raw({"model_call_id": "late-private", "private": "late"})
    with factory() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id))
        assert turn.response is None and turn.request_messages == []


@pytest.mark.parametrize("kind", ["extract", "summary"])
@pytest.mark.parametrize("prior_attempts", [0, 1])
@pytest.mark.parametrize("code", ["model_credentials_unavailable", "agent_credential_unavailable"])
def test_memory_credential_failure_is_terminal_before_provider_send(
    memory_workspace, monkeypatch, kind, prior_attempts, code
):
    from short_drama.agent.gateway.contracts import AgentGatewayError
    from short_drama.core.exceptions import WorkflowError

    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted, kind=kind)
    with factory.begin() as session:
        session.get(AgentMemoryJob, identifier).attempts = prior_attempts

    def unavailable(*args):
        if code == "agent_credential_unavailable":
            raise AgentGatewayError(code)
        raise WorkflowError("model_credentials_unavailable", "credential fixture")

    monkeypatch.setattr("short_drama.agent.memory.runtime.model_credentials", unavailable)
    runtime = AgentMemoryRuntime(factory, runtime_settings())
    runtime.execute_one(identifier, 1)
    assert runtime.store.recover() == 0
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        assert job.status == "failed" and job.attempts == prior_attempts
        assert job.error["code"] == code and job.message_version == 1
        assert job.provider_state == "not_started" and job.raw_result is None
        assert job.lease_token is None and job.publish_token is None
        assert job.finished_at is not None
    assert runtime.store.claim_execution(identifier, 1) is None


@pytest.mark.parametrize(
    ("kind", "code", "receipt", "accepted_unknown", "attempts", "status"),
    [
        ("extract", "agent_provider_rate_limited", False, False, 3, "abandoned"),
        ("summary", "agent_provider_rate_limited", False, False, 3, "failed"),
        ("extract", "invalid_agent_memory_result", True, False, 3, "abandoned"),
        ("extract", "agent_response_truncated", True, False, 3, "abandoned"),
        ("extract", "agent_provider_authentication", True, False, 1, "failed"),
        ("extract", "agent_provider_rejected", True, False, 1, "failed"),
        ("summary", "invalid_agent_memory_result", True, False, 3, "failed"),
        ("summary", "invalid_agent_memory_result", True, True, 1, "failed"),
        ("extract", "agent_provider_unknown", False, True, 1, "failed"),
        ("extract", "invalid_agent_memory_result", True, True, 1, "failed"),
    ],
)
def test_memory_retry_classification_preserves_terminal_and_unknown_failures(
    memory_workspace, kind, code, receipt, accepted_unknown, attempts, status
):
    from short_drama.agent.gateway.contracts import AgentGatewayError

    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted, kind=kind)
    store = AgentMemoryStore(factory, runtime_settings())
    for version in range(1, attempts + 1):
        claim = store.claim_execution(identifier, version)
        assert claim is not None
        store.before_send(claim, {"attempt": version, "body": {"messages": []}})
        if receipt:
            store.save_raw(claim, {"response": version})
        before_failure = utcnow()
        store.fail(claim, AgentGatewayError(code, accepted_unknown=accepted_unknown))
        with factory.begin() as session:
            job = session.get(AgentMemoryJob, identifier)
            assert job.attempts == version and job.error == {"code": code}
            assert job.lease_token is None and job.publish_token is None
            if version < attempts:
                assert job.status == "pending" and job.message_version == version + 1
                assert job.provider_state == "not_started" and job.raw_result is None
                assert job.next_run_at >= before_failure + timedelta(seconds=15 * version)
                job.next_run_at = utcnow() - timedelta(seconds=1)
            else:
                assert job.status == status and job.message_status == "idle"
                assert job.finished_at is not None and job.message_version == version
                if receipt:
                    assert job.raw_result["raw"] == {"response": version}
                if accepted_unknown and not receipt:
                    assert job.provider_state == "unknown"
                assert MemoryJobRead.model_validate(memory_job_read(job)).status == status
                if kind == "extract":
                    assert extraction_waterline(session, job.conversation_id) == (
                        job.to_seq if status == "abandoned" else 0
                    )
    assert store.claim_execution(identifier, attempts) is None


@pytest.mark.parametrize("provider_state", ["not_started", "received", "unknown"])
def test_explicit_maintenance_retry_preserves_receipts_and_cannot_duplicate_a_child(
    memory_workspace, provider_state
):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    receipt = {"request": {"original": True}, "raw": {"original": "receipt"}}
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        job.status, job.message_status, job.provider_state = "failed", "idle", provider_state
        job.raw_result = receipt
        job.error = {"code": "invalid_agent_memory_result"}
        job.finished_at = utcnow()
    with factory() as session:
        manager = service(session)
        if provider_state == "unknown":
            with pytest.raises(Conflict):
                manager.retry_job(identifier, {"message_version": "1"}, "retry")
            return
        retried = manager.retry_job(identifier, {"message_version": "1"}, "retry")
        assert retried["id"] != str(identifier) and retried["status"] == "pending"
        assert manager.retry_job(identifier, {"message_version": "1"}, "retry") == retried
        with pytest.raises(Conflict):
            manager.retry_job(identifier, {"message_version": "1"}, "another-retry")
        with pytest.raises(NotFound):
            service(session, 2).retry_job(identifier, {"message_version": "1"}, "other-user")
        details = service(session).conversation_context(conversations[0])
        original = next(item for item in details["jobs"] if item["id"] == str(identifier))
        assert original["retry_job_id"] == retried["id"]
    with factory.begin() as session:
        assert session.get(AgentMemoryJob, identifier).raw_result == receipt
        child = session.get(AgentMemoryJob, int(retried["id"]))
        assert child.raw_result is None and child.provider_state == "not_started"
        assert child.source_snapshot["manual_retry_of"] == str(identifier)


def test_maintenance_does_not_skip_an_earlier_failed_extraction_range(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace)
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        first = enqueue_job_locked(
            session, conversation, run, "extract", message_sources(session, conversation.id, 1, 2)
        )
        first.status = "failed"
        later = enqueue_job_locked(
            session, conversation, run, "extract", message_sources(session, conversation.id, 3, 4)
        )
        later.status = "succeeded"
        session.flush()
        schedule_maintenance_locked(session, conversation, run)
        session.flush()
        assert not session.scalars(
            select(AgentMemoryJob).where(
                AgentMemoryJob.conversation_id == conversation.id,
                AgentMemoryJob.kind == "extract",
                AgentMemoryJob.from_seq > 4,
            )
        ).all()


@pytest.mark.parametrize("count", [0, 10, 100])
@pytest.mark.parametrize("kind", ["extract", "summary"])
def test_recovery_does_not_relock_successfully_retried_covered_history(
    memory_workspace, count, kind
):
    from sqlalchemy import event

    from short_drama.agent.memory.recovery import recover_failed_locked, retry_child_locked

    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=max(count, 1))
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        messages = message_sources(session, conversation.id, 1, max(count, 1))
        for index in range(count):
            parent = enqueue_job_locked(session, conversation, run, kind, [messages[index]])
            parent.status, parent.message_status = "failed", "idle"
            parent.error = {"code": "invalid_agent_memory_result"}
            child = retry_child_locked(session, parent, run, f"covered-{kind}-{index}", {})
            child.status, child.message_status = "succeeded", "idle"
        if kind == "summary":
            conversation.summary_covers_until_seq = count
        session.flush()
    statements = []

    def measured(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        event.listen(factory.kw["bind"], "before_cursor_execute", measured)
        try:
            recover_failed_locked(session, conversation, run)
        finally:
            event.remove(factory.kw["bind"], "before_cursor_execute", measured)
        assert len(statements) <= 4
        assert sum("FOR UPDATE" in statement.upper() for statement in statements) <= 2
        parents = session.scalars(
            select(AgentMemoryJob).where(AgentMemoryJob.status == "failed")
        ).all()
        assert len(parents) == count


@pytest.mark.parametrize("kind", ["extract", "summary"])
def test_successful_child_outside_continuous_coverage_still_checks_changed_source(
    memory_workspace, kind
):
    from short_drama.agent.memory.recovery import recover_failed_locked, retry_child_locked

    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=3)
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        messages = message_sources(session, conversation.id, 2, 3)
        parent = enqueue_job_locked(session, conversation, run, kind, messages)
        parent.status, parent.message_status = "failed", "idle"
        child = retry_child_locked(session, parent, run, "success-after-gap", {})
        child.status, child.message_status = "succeeded", "idle"
        parent_id, child_id = parent.id, child.id
        session.get(AgentMessage, int(messages[0]["id"])).content = "当前未覆盖来源已变"
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        assert extraction_waterline(session, conversation.id) == 0
        recover_failed_locked(session, conversation, run)
        assert session.get(AgentMemoryJob, parent_id).status == "stale"
        assert session.get(AgentMemoryJob, child_id).status == "succeeded"


@pytest.mark.parametrize("model_change", ["edited", "deleted", "disabled", "unchanged"])
@pytest.mark.parametrize("provider_state", ["not_started", "received", "unknown", "sending"])
def test_new_valid_run_recovers_failed_memory_with_invalid_old_model(
    memory_workspace, model_change, provider_state
):
    from short_drama.domain import AIModelConfig
    from tests.support.assistant_runtime import RuntimeTransport

    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    receipt = {"request": {"original": True}, "raw": {"original": "invalid"}}
    with factory.begin() as session:
        original = session.get(AgentMemoryJob, identifier)
        original.status, original.message_status = "failed", "idle"
        original.provider_state = provider_state
        original.raw_result = receipt if provider_state == "received" else None
        original.error = {"code": "invalid_agent_memory_result"}
        original.finished_at = utcnow()
        old = session.get(AIModelConfig, session.get(AgentRun, accepted.run.id).model_config_id)
        if model_change == "edited":
            old.row_version += 1
        elif model_change == "deleted":
            old.is_deleted = 1
        elif model_change == "disabled":
            old.enabled = 0
    with factory() as session:
        session.info["actor"] = actor()
        model = AIModelConfigService(session).create(
            {
                "service_type": "text",
                "name": "Replacement",
                "model_key": "fixture",
                "provider": "compatible",
                "base_url": "https://model.invalid/v1",
            }
        )
        newer = AssistantTaskService(
            session, runtime_settings(), tool_lookup=get_spec
        ).send_task_message(
            conversations[0], {"content": "继续整理", "model_config_id": model.id}, "new-model"
        )
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, newer.run.id)
        schedule_maintenance_locked(session, conversation, run)
        session.flush()
        schedule_maintenance_locked(session, conversation, run)
        original = session.get(AgentMemoryJob, identifier)
        child_id = original.source_snapshot.get("retry_job_id")
        if model_change == "unchanged" or provider_state in {"unknown", "sending"}:
            assert child_id is None
            assert extraction_waterline(session, conversation.id) == 0
            return
        assert child_id is not None
        child = session.get(AgentMemoryJob, int(child_id))
        assert child.run_id == run.id and child.provider_state == "not_started"
        assert child.source_hash == original.source_hash
        assert child.source_snapshot["messages"] == original.source_snapshot["messages"]
        assert original.raw_result == (receipt if provider_state == "received" else None)
    transport = RuntimeTransport([{"text": '{"memories":[]}'}])
    AgentMemoryRuntime(factory, runtime_settings(), transport=transport).execute_one(
        int(child_id), 1
    )
    with factory() as session:
        child = session.get(AgentMemoryJob, int(child_id))
        assert child.status == "succeeded", child.error
        assert extraction_waterline(session, conversations[0]) == 2
    assert len(transport.main_requests) == 1


def test_legal_long_chinese_source_is_not_rejected_by_batch_byte_target(memory_workspace):
    from tests.support.assistant_runtime import RuntimeTransport

    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=1)
    with factory.begin() as session:
        session.get(AgentMessage, accepted.message.id).content = "创" * 32000
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        schedule_maintenance_locked(session, conversation, run)
        session.flush()
        job = session.scalar(select(AgentMemoryJob).where(AgentMemoryJob.kind == "extract"))
        assert job.status == "pending", job.error
        identifier = job.id
        assert job.source_snapshot["messages"][0]["content"] == "创" * 32000
    transport = RuntimeTransport([{"text": '{"memories":[]}'}])
    AgentMemoryRuntime(factory, runtime_settings(), transport=transport).execute_one(identifier, 1)
    with factory() as session:
        job = session.get(AgentMemoryJob, identifier)
        assert job.status == "succeeded", job.error
        assert job.attempts == 1 and extraction_waterline(session, conversations[0]) == 1
    assert "创" * 32000 in json.dumps(transport.requests, ensure_ascii=False)


@pytest.mark.parametrize("change", ["edit", "forget"])
@pytest.mark.parametrize(
    "provider_state,retry_state",
    [
        ("not_started", None),
        ("not_started", "not_started"),
        ("not_started", "stale"),
        ("unknown", None),
        ("not_started", "unknown"),
    ],
)
def test_changed_failed_source_restarts_at_same_waterline_without_resending_unknown(
    memory_workspace, change, provider_state, retry_state
):
    from tests.support.assistant_runtime import RuntimeTransport

    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        job.status, job.message_status = "failed", "idle"
        job.provider_state = provider_state
        job.error = {"code": "agent_memory_source_too_large"}
        job.finished_at = utcnow()
    if retry_state is not None:
        with factory() as session:
            retried = service(session).retry_job(
                identifier, {"message_version": "1"}, "source-retry"
            )
        with factory.begin() as session:
            child = session.get(AgentMemoryJob, int(retried["id"]))
            child.status, child.message_status = (
                "stale" if retry_state == "stale" else "failed",
                "idle",
            )
            child.provider_state = "not_started" if retry_state == "stale" else retry_state
            child.error = {"code": "agent_memory_source_too_large"}
            child.finished_at = utcnow()
    with factory.begin() as session:
        frozen = session.get(AgentMemoryJob, identifier).source_snapshot
        if change == "edit":
            session.get(AgentMessage, accepted.message.id).content = "更新后的短约束"
    if change == "forget":
        with factory() as session:
            manager = service(session)
            item = create_sourced_memory(
                manager,
                memory(
                    source_conversation_id=conversations[0],
                    source_message_ids=[accepted.message.id],
                ),
            )
            manager.forget_item(item.id, {"content_version": "1"})
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        schedule_maintenance_locked(session, conversation, run)
        session.flush()
        original = session.get(AgentMemoryJob, identifier)
        assert original.source_snapshot == frozen
        assert extraction_waterline(session, conversation.id) == 0
        pending = session.scalars(
            select(AgentMemoryJob).where(
                AgentMemoryJob.kind == "extract", AgentMemoryJob.status == "pending"
            )
        ).all()
        if provider_state == "unknown" or retry_state == "unknown":
            assert original.status == ("failed" if provider_state == "unknown" else "stale")
            assert not pending
            if retry_state == "unknown":
                assert session.get(AgentMemoryJob, int(retried["id"])).provider_state == "unknown"
            return
        assert original.status == "stale" and len(pending) == 1
        child = pending[0]
        assert child.from_seq == 1 and child.to_seq == 2
        assert child.source_hash != original.source_hash
        first = child.source_snapshot["messages"][0]
        if change == "forget":
            assert first["memory_restricted"] and first["content"] != "以后对白要短"
        else:
            assert first["content"] == "更新后的短约束"
        child_id = child.id
    AgentMemoryRuntime(
        factory, runtime_settings(), transport=RuntimeTransport([{"text": '{"memories":[]}'}])
    ).execute_one(child_id, 1)
    with factory() as session:
        assert extraction_waterline(session, conversations[0]) == 2


def test_memory_actual_context_budget_rejects_before_send(memory_workspace):
    from short_drama.agent.gateway.contracts import AgentGatewayError

    factory, _, _ = memory_workspace
    accepted = seeded_run(memory_workspace, count=1)
    identifier = maintenance_job(memory_workspace, accepted)
    store = AgentMemoryStore(factory, runtime_settings())
    claim = store.claim_execution(identifier, 1)
    with pytest.raises(AgentGatewayError, match="agent_context_budget_exhausted"):
        store.before_send(
            claim, {"body": {"messages": [{"role": "user", "content": "创" * 100000}]}}
        )
    with factory() as session:
        job = session.get(AgentMemoryJob, identifier)
        assert job.provider_state == "not_started" and job.attempts == 0


def test_memory_failure_explains_safe_recovery(memory_workspace):
    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=1)
    identifier = maintenance_job(memory_workspace, accepted)
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        job.status, job.message_status = "failed", "idle"
        job.error = {"code": "invalid_agent_memory_result", "private": "must-not-leak"}
        job.finished_at = utcnow()
    with factory() as session:
        details = service(session).conversation_context(conversations[0])
        failed = next(item for item in details["jobs"] if item["id"] == str(identifier))
        assert "重试" in failed["error"]["message"] and "引用" in failed["error"]["message"]
        assert "recovery_hint" not in failed
        assert "must-not-leak" not in json.dumps(details, default=str)


@pytest.mark.parametrize("repaired", [True, False])
def test_summary_known_invalid_coverage_retries_with_receipts_and_a_three_send_limit(
    memory_workspace, repaired
):
    from tests.support.assistant_runtime import RuntimeTransport

    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted, kind="summary")
    with factory() as session:
        source = session.get(AgentMemoryJob, identifier).source_snapshot["messages"]
    valid = {
        "facts": [
            {
                "kind": "user_fact",
                "text": "保留两条用户约束",
                "source_message_ids": [row["id"] for row in source],
            }
        ]
    }
    replies = ["{}", json.dumps(valid, ensure_ascii=False)] if repaired else ["{}"] * 3
    transport = RuntimeTransport([{"text": value} for value in replies])
    runtime = AgentMemoryRuntime(factory, runtime_settings(), transport=transport)
    for attempt in range(1, len(replies) + 1):
        runtime.execute_one(identifier, attempt)
        with factory.begin() as session:
            job = session.get(AgentMemoryJob, identifier)
            assert job.attempts == attempt
            if attempt < len(replies):
                assert job.status == "pending", job.error
                assert job.source_snapshot["attempt_receipts"][-1]["attempt"] == attempt
                assert job.source_snapshot["attempt_receipts"][-1]["receipt"]["raw"]
                job.next_run_at = utcnow() - timedelta(seconds=1)
            else:
                assert job.status == ("succeeded" if repaired else "failed")
                assert (
                    bool(session.get(AgentConversation, conversations[0]).active_summary_id)
                    == repaired
                )
    runtime.execute_one(identifier, len(replies))
    assert len(transport.main_requests) == len(replies)
    assert "validation_feedback" in json.dumps(transport.main_requests[1], ensure_ascii=False)


def test_unknown_summary_source_change_does_not_schedule_a_second_paid_request(memory_workspace):
    factory, _, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=10)
    identifier = maintenance_job(memory_workspace, accepted, kind="summary")
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        job.status, job.message_status, job.provider_state = "failed", "idle", "unknown"
        job.error = {"code": "agent_acceptance_unknown"}
        job.finished_at = utcnow()
        session.get(AgentMessage, accepted.message.id).content = "新约束" * 6000
    with factory.begin() as session:
        schedule_maintenance_locked(
            session,
            session.get(AgentConversation, conversations[0]),
            session.get(AgentRun, accepted.run.id),
        )
        session.flush()
        summaries = session.scalars(
            select(AgentMemoryJob).where(AgentMemoryJob.kind == "summary")
        ).all()
        assert [row.id for row in summaries] == [identifier]
        assert summaries[0].provider_state == "unknown"


def test_edit_and_forget_keep_provenance_and_hide_old_source_from_context(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory() as session:
        manager = service(session)
        item = create_sourced_memory(
            manager,
            memory(
                source_conversation_id=conversations[0],
                source_message_ids=[accepted.message.id],
            ),
        )
        updated = manager.patch_item(
            item.id, {"content_version": "1", "content": "对白长短按场景决定"}
        )
        assert "sources" not in updated.model_dump()
        forgotten = manager.forget_item(item.id, {"content_version": "2"})
        assert "sources" not in forgotten.model_dump()
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        task = session.get(AgentTask, accepted.task.id)
        history = history_projection_locked(session, conversation, task, 1, 2)
        assert history["items"][0]["content"] != "以后对白要短"
        assert history["items"][0]["source_restricted"]


def test_summary_activation_preserves_appends_and_fences_another_candidate(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        rows = message_sources(session, conversation.id, 1, 2)
        first = enqueue_job_locked(session, conversation, run, "summary", rows[:1])
        second = enqueue_job_locked(session, conversation, run, "summary", rows)
        session.flush()
        conversation.next_message_seq = 4
        session.add(
            AgentMessage(
                id=next_id(),
                conversation_id=conversation.id,
                task_id=accepted.task.id,
                seq=3,
                role="user",
                content="整理期间的新输入",
                references=[],
                artifacts=[],
                created_at=utcnow(),
            )
        )
        session.flush()
        payload = {
            "facts": [
                {"kind": "user_fact", "text": "对白要短", "source_message_ids": [rows[0]["id"]]}
            ]
        }
        assert settle_maintenance_locked(session, conversation, first, payload, {})
        assert conversation.summary_covers_until_seq == 1 and conversation.next_message_seq == 4
        assert not settle_maintenance_locked(session, conversation, second, payload, {})
        assert second.status == "stale" and conversation.summary_covers_until_seq == 1


def test_phase_summary_chain_is_independent_of_account_memory(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=3)
    chain = []
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        rows = message_sources(session, conversation.id, 1, 3)
        for index, row in enumerate(rows):
            job = enqueue_job_locked(session, conversation, run, "summary", [row])
            session.flush()
            assert settle_maintenance_locked(
                session,
                conversation,
                job,
                {
                    "facts": [
                        {
                            "kind": "user_fact",
                            "text": "保留对白与人物关系",
                            "source_message_ids": [row["id"]],
                            "prior_fact_indexes": [0] if index else [],
                        }
                    ]
                },
                {},
            )
            chain.append(conversation.active_summary_id)
    with factory() as session:
        manager = service(session)
        current = manager.conversation_context(conversations[0])["summary"]
        assert current["id"] == str(chain[-1]) and current["base_summary_id"] == str(chain[-2])
        assert current["facts"][0]["source_message_ids"] == [rows[-1]["id"]]
        for index in range(2, -1, -1):
            prior = manager.read_summary(conversations[0], chain[index])
            assert prior["base_summary_id"] == (str(chain[index - 1]) if index else None)
            assert prior["facts"][0]["source_message_ids"] == [rows[index]["id"]]
        with pytest.raises(NotFound):
            manager.read_summary(conversations[1], chain[0])
        with pytest.raises(NotFound):
            service(session, 2).read_summary(conversations[0], chain[0])
        manager = service(session)
        item = create_sourced_memory(
            manager,
            memory(source_conversation_id=conversations[0], source_message_ids=[rows[0]["id"]]),
        )
        manager.forget_item(item.id, {"content_version": "1"})
        assert manager.conversation_context(conversations[0])["summary"]["id"] == str(chain[-1])
        for identifier in chain:
            assert manager.read_summary(conversations[0], identifier)


def runtime_settings():
    return SimpleNamespace(
        agent_enabled=True,
        agent_lease_seconds=180,
        generation_publish_lease_seconds=15,
        encryption_key=None,
    )


def maintenance_job(workspace, accepted, *, kind="extract"):
    factory, _projects, conversations = workspace
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        job = enqueue_job_locked(
            session,
            conversation,
            run,
            kind,
            message_sources(session, conversation.id, 1, conversation.next_message_seq - 1),
        )
        session.flush()
        return job.id


def test_memory_publish_claim_and_unknown_recovery_do_not_resend(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    settings = runtime_settings()
    sent = []
    publisher = AgentMemoryPublisher(factory, settings, send=lambda *args: sent.append(args))
    assert publisher.tick() and not publisher.tick()
    assert sent == [(identifier, 1)]
    store = publisher.store
    claim = store.claim_execution(identifier, 1)
    assert claim is not None and store.claim_execution(identifier, 1) is None
    store.before_send(claim, {"original": "request", "body": {"messages": []}})
    with factory.begin() as session:
        session.get(AgentMemoryJob, identifier).lease_until = utcnow() - timedelta(seconds=1)
    assert store.recover() == 1
    with factory() as session:
        job = session.get(AgentMemoryJob, identifier)
        assert job.status == "failed" and job.provider_state == "unknown"
        assert job.attempts == 1 and job.error["code"] == "agent_acceptance_unknown"
    assert not publisher.tick() and store.claim_execution(identifier, 1) is None


def test_memory_context_details_hide_frozen_provider_data_and_other_accounts(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    with factory.begin() as session:
        session.get(AgentMemoryJob, identifier).raw_result = {"private": "raw-provider-body"}
    with factory() as session:
        details = service(session).conversation_context(conversations[0])
        assert details["jobs"][0]["id"] == str(identifier)
        assert "raw-provider-body" not in str(details) and "source_snapshot" not in str(details)
        with pytest.raises(NotFound):
            service(session, 2).conversation_context(conversations[0])


def test_complete_memory_receipt_recovers_by_local_replay_only(memory_workspace):
    import asyncio

    from tests.support.chat_transport import JSONChatTransport

    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    settings = runtime_settings()
    store = AgentMemoryStore(factory, settings)
    claim = store.claim_execution(identifier, 1)
    transport = JSONChatTransport(
        {
            "memories": [
                {
                    "kind": "preference",
                    "memory_key": "dialogue",
                    "title": "对白偏好",
                    "content": "对白要短",
                    "source_message_ids": [str(accepted.message.id)],
                }
            ]
        }
    )
    runtime = AgentMemoryRuntime(factory, settings, transport=transport)
    output, usage = asyncio.run(runtime._model_result(claim, ""))
    assert json.loads(output)["memories"][0]["content"] == "对白要短"
    assert usage["output_tokens"] == 12
    with factory.begin() as session:
        session.get(AgentMemoryJob, identifier).lease_until = utcnow() - timedelta(seconds=1)
    assert store.recover() == 1
    with pytest.raises(LeaseLost):
        store.settle(claim, {}, {})

    runtime.execute_one(identifier, 2)
    runtime.execute_one(identifier, 2)
    assert len(transport.requests) == 1
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        assert job.status == "succeeded" and job.attempts == 1
        item = session.scalar(
            select(AgentMemoryItem).where(AgentMemoryItem.memory_key == "dialogue")
        )
        assert not hasattr(item, "conversation_id")
        assert item.content_version == 1


def test_late_memory_receipt_cannot_restore_a_forgotten_source(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    store = AgentMemoryStore(factory, runtime_settings())
    claim = store.claim_execution(identifier, 1)
    store.before_send(claim, {"original": True, "body": {"messages": []}})
    with factory() as session:
        manager = service(session)
        item = create_sourced_memory(
            manager,
            memory(
                source_conversation_id=conversations[0], source_message_ids=[accepted.message.id]
            ),
        )
        manager.forget_item(item.id, {"content_version": "1"})
    store.save_raw(claim, {"late": True})
    assert not store.settle(
        claim,
        {
            "memories": [
                {
                    "kind": "preference",
                    "memory_key": "renamed",
                    "title": "换标题的旧事实",
                    "content": "对白要短",
                    "source_message_ids": [str(accepted.message.id)],
                }
            ]
        },
        {},
    )
    with factory() as session:
        assert session.get(AgentMemoryJob, identifier).status == "stale"
        session.rollback()
        assert service(session).list_items().total == 0


@pytest.mark.parametrize("bad_version", [False, True])
def test_memory_tool_result_is_paired_and_allows_a_following_answer(memory_workspace, bad_version):
    from short_drama.agent.runtime.executor import AgentRuntime
    from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    settings = runtime_settings()
    with factory() as session:
        item = service(session).create_item(memory(), "readable")
        following = AssistantTaskService(session, settings, tool_lookup=get_spec).send_task_message(
            conversations[1],
            {"content": "查记忆后回答", "model_config_id": accepted.run.model_config_id},
            "read",
        )
    transport = RuntimeTransport(
        [
            calls(
                (
                    "memory-call",
                    "runtime__read_memory",
                    {
                        "ids": [str(item.id)],
                        "versions": {str(item.id): "99" if bad_version else "1"},
                    },
                )
            ),
            {"text": "已依据读取结果回答"},
        ]
    )
    AgentRuntime(factory, settings, transport=transport).execute(following.run.id, 1)
    result = tool_result(transport.main_requests[-1], "memory-call")
    assert (
        result["error"]["code"] == "conflict"
        if bad_version
        else result["items"][0]["content"] == item.content
    )
    calls_in_request = [m for m in transport.main_requests[-1]["messages"] if m.get("tool_calls")]
    assert any(call["id"] == "memory-call" for m in calls_in_request for call in m["tool_calls"])
    with factory() as session:
        assert session.get(AgentRun, following.run.id).status == "succeeded"


def run_memory_command(
    workspace,
    model_id,
    content,
    tool_name,
    arguments,
    *,
    key,
    projects=(),
    repeat=1,
    before_execute=None,
):
    from short_drama.agent.runtime.executor import AgentRuntime
    from tests.support.assistant_runtime import RuntimeTransport, calls

    factory, _projects, conversations = workspace
    settings = runtime_settings()
    with factory() as session:
        service(session)
        accepted = AssistantTaskService(session, settings, tool_lookup=get_spec).send_task_message(
            conversations[1],
            {
                "content": content,
                "model_config_id": model_id,
                "new_task": True,
            },
            key,
        )
    arguments = arguments(accepted) if callable(arguments) else arguments
    request = {"source_message_ids": [str(accepted.message.id)], **arguments}
    changed = False

    def boundary(_body, index):
        nonlocal changed
        if before_execute and not changed:
            changed = True
            before_execute(accepted)

    transport = RuntimeTransport(
        [
            *[calls((f"memory-{index}", tool_name, request)) for index in range(repeat)],
            {"text": "已按真实回执处理"},
        ],
        before_reply=boundary,
    )
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == accepted.run.id)
            .order_by(AgentOperation.id)
        ).all()
    return accepted, transport, operations


def test_explicit_memory_tools_save_account_preferences_once(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    model_id = seeded_run(memory_workspace, count=2).run.model_config_id
    accepted, _transport, operations = run_memory_command(
        memory_workspace,
        model_id,
        "记住：以后对白简短",
        "runtime__remember_memory",
        {
            "kind": "preference",
            "memory_key": "short-dialogue",
            "title": "对白偏好",
            "content": "以后对白简短",
        },
        key="remember-account",
        repeat=2,
    )
    assert all(operation.status == "succeeded" for operation in operations)
    with factory() as session:
        items = service(session).list_items().items
        assert len(items) == 1 and items[0].content == "以后对白简短"


def test_explicit_memory_lifecycle_protects_old_sources_and_requires_new_restore(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    model_id = seeded_run(memory_workspace, count=2).run.model_config_id
    text = "记住账号偏好：对白简短"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        text,
        "runtime__remember_memory",
        {
            "kind": "preference",
            "memory_key": "dialogue",
            "title": "对白节奏",
            "content": "对白简短",
        },
        key="remember",
    )
    identifier = operations[0].result["memory"]["id"]
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "修改记忆“对白节奏”为：对白长短按场景",
        "runtime__update_memory",
        {"memory_id": identifier, "content_version": "1", "content": "对白长短按场景"},
        key="update",
    )
    assert operations[0].status == "succeeded"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "忘掉账号记忆“对白节奏”",
        "runtime__forget_memory",
        {"memory_id": identifier, "content_version": "2"},
        key="forget",
    )
    assert operations[0].result["memory"]["state"] == "forgotten"
    _accepted, _inputs, denied = run_memory_command(
        memory_workspace,
        model_id,
        text,
        "runtime__remember_memory",
        {
            "kind": "preference",
            "memory_key": "dialogue",
            "title": "换标题",
            "content": "对白简短",
        },
        key="old-remember",
    )
    assert denied[0].status == "failed" and denied[0].error["code"] == "assistant_input_scope"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "重新记住账号记忆“对白节奏”",
        "runtime__restore_memory",
        {"memory_id": identifier, "content_version": "3"},
        key="restore",
    )
    assert operations[0].status == "succeeded"
    with factory() as session:
        item = service(session).get_item(identifier)
        assert (
            item.state == "active"
            and item.content == "对白长短按场景"
            and item.content_version == 4
        )
        assert service(session).list_items().total == 1
        assert "sources" not in item.model_dump()


@pytest.mark.parametrize("forgery", ["other_task", "hash", "identity", "project_scope"])
def test_explicit_memory_tool_rejects_forged_authority_or_content(memory_workspace, forgery):
    factory, _projects, _conversations = memory_workspace
    old = seeded_run(memory_workspace, count=2)
    arguments = {
        "kind": "preference",
        "memory_key": "forged",
        "title": "伪造",
        "content": "真实原文",
    }
    content = "记住：真实原文"
    if forgery == "other_task":
        arguments["source_message_ids"] = [str(old.message.id)]
    if forgery == "hash":
        arguments["request_hash"] = "0" * 64
    if forgery == "identity":
        arguments["user_id"] = "2"
    if forgery == "project_scope":
        content = "记住当前作品：无绑定作品"
        arguments["scope_type"] = "project"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        old.run.model_config_id,
        content,
        "runtime__remember_memory",
        arguments,
        key=forgery,
    )
    assert operations[0].status == "failed"
    with factory() as session:
        assert service(session).list_items().total == 0


def test_explicit_memory_tool_rejects_a_stale_version_after_a_human_edit(
    memory_workspace,
):
    factory, _projects, _conversations = memory_workspace
    model_id = seeded_run(memory_workspace, count=2).run.model_config_id
    with factory() as session:
        item = service(session).create_item(memory(), "before-command")

    def newer_edit(_accepted):
        with factory() as session:
            service(session).patch_item(
                item.id, {"content_version": "1", "content": "用户后来纠正的内容"}
            )

    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "修改记忆“对白节奏”为：旧指令内容",
        "runtime__update_memory",
        {"memory_id": str(item.id), "content_version": "1", "content": "旧指令内容"},
        key="stale-source",
        before_execute=newer_edit,
    )
    assert operations[0].status == "failed" and operations[0].error["code"] == "conflict"
    with factory() as session:
        assert service(session).get_item(item.id).content == "用户后来纠正的内容"


def create_sourced_memory(manager, values):
    from short_drama.service.assistant.memory.memory_store import memory_read

    values = dict(values)
    conversation = int(values.pop("source_conversation_id"))
    identifiers = [int(value) for value in values.pop("source_message_ids")]
    with manager._transaction():
        owner = manager._owner()
        sources = source_rows(manager.session, owner, conversation, identifiers)
        row = create_memory_locked(
            manager.session, owner, MemoryCreate.model_validate(values), sources=sources
        )
        manager.session.flush()
        return memory_read(manager.session, row)


@pytest.mark.parametrize("boundary", ["queued", "sending", "received"])
def test_memory_off_on_invalidates_extraction_at_every_boundary(memory_workspace, boundary):
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService

    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    store = AgentMemoryStore(factory, runtime_settings())
    claim = None
    if boundary != "queued":
        claim = store.claim_execution(identifier, 1)
        store.before_send(claim, {"body": {"messages": []}})
        if boundary == "received":
            store.save_raw(claim, {"complete": True})
    with factory() as session:
        service(session)
        preferences = AssistantSettingsService(session, runtime_settings())
        off = preferences.update_settings(
            {"settings_version": "0", "long_term_memory_enabled": False}
        )
        preferences.update_settings(
            {"settings_version": str(off.settings_version), "long_term_memory_enabled": True}
        )
    if claim is None:
        assert store.claim_execution(identifier, 1) is None
    else:
        if boundary == "sending":
            store.save_raw(claim, {"complete": True})
        assert not store.settle(
            claim,
            {
                "memories": [
                    {
                        "kind": "preference",
                        "memory_key": "late",
                        "title": "旧偏好",
                        "content": "对白短",
                        "source_message_ids": [str(accepted.message.id)],
                    }
                ]
            },
            {},
        )
    with factory() as session:
        assert session.get(AgentMemoryJob, identifier).status == "abandoned"
        session.rollback()
        assert service(session).list_items().total == 0


def test_disabled_memory_remains_manually_manageable_and_keeps_summaries(memory_workspace):
    from short_drama.core.exceptions import WorkflowError
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory() as session:
        manager = service(session)
        item = manager.create_item(memory(), "manual")
        AssistantSettingsService(session, runtime_settings()).update_settings(
            {
                "settings_version": "0",
                "long_term_memory_enabled": False,
            }
        )
        assert (
            manager.patch_item(item.id, {"content_version": "1", "content": "人工纠正"}).content
            == "人工纠正"
        )
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[0])
        run = session.get(AgentRun, accepted.run.id)
        sources = message_sources(session, conversation.id, 1, 2)
        extraction = enqueue_job_locked(session, conversation, run, "extract", sources)
        summary = enqueue_job_locked(session, conversation, run, "summary", sources)
        assert extraction.status == "succeeded" and summary.status == "pending"
        with pytest.raises(WorkflowError, match="长期记忆已关闭"):
            get_spec("runtime__memory_catalog").handler.locked_handler(
                session,
                conversation,
                session.get(AgentTask, accepted.task.id),
                None,
                SimpleNamespace(offset=0, limit=30),
                runtime_settings(),
            )
