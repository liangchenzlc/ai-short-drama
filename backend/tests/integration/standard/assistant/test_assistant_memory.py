"""Real isolated MySQL memory scope, ngram retrieval and stale-source protection."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from short_drama.agent.coordinator.assistant import claim_operation, complete_operation
from short_drama.agent.coordinator.runtime import AgentRuntimeStore
from short_drama.agent.core.access import LeaseLost
from short_drama.agent.legacy.authorization import digest
from short_drama.agent.legacy.tools import execute_tools, prepare_decision
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
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.core.identity import ActorContext
from short_drama.dao.agent_memory_dao import MemoryScopeContext
from short_drama.domain import MediaFile
from short_drama.domain.agent import AgentConversation, AgentMessage, AgentRun, AgentTurn
from short_drama.domain.agent_context import AgentAttachment
from short_drama.domain.agent_memory import AgentMemoryItem, AgentMemoryJob
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.schemas.agent_memory import MemoryCreate, MemoryJobRead
from short_drama.service.agent_memory_service import (
    AgentMemoryService,
    create_memory_locked,
    memory_catalog_locked,
    memory_job_read,
    read_memories_locked,
    search_memory_locked,
    source_rows,
)
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.assistant_task_service import AssistantTaskService
from short_drama.service.base import utcnow
from short_drama.service.project_service import ProjectService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


def actor(owner=1):
    return ActorContext(owner, "creator", "creator@example.test", True, 1, "hash", "test")


@pytest.fixture
def memory_workspace(db_session):
    from tests.integration.standard.assistant.test_global_assistant_tasks import legacy_task_engine

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
                AssistantTaskService(session, settings)
                .create_global_conversation({}, f"conversation-{index}")
                .id
            )
    with legacy_task_engine(factory):
        yield factory, projects, conversations


def service(session, owner=1):
    session.info["actor"] = actor(owner)
    return AgentMemoryService(session)


def memory(scope="user", **fields):
    return {
        "scope_type": scope,
        "kind": "preference",
        "memory_key": "对白节奏",
        "title": "对白节奏",
        "content": "对白简短，转折明确",
        **fields,
    }


def test_memory_scopes_are_independent_from_write_target_and_account(memory_workspace):
    factory, projects, conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        items = [
            manager.create_item(memory(), "user"),
            manager.create_item(
                memory("conversation", conversation_id=conversations[0]), "conversation"
            ),
            manager.create_item(memory("project", project_id=projects[0]), "project-a"),
            manager.create_item(memory("project", project_id=projects[1]), "project-b"),
        ]
        assert {
            item.id
            for item in manager.list_items(
                conversation_id=conversations[0], project_id=projects[0]
            ).items
        } == {item.id for item in items[:3]}
        assert {item.id for item in manager.list_items(conversation_id=conversations[1]).items} == {
            items[0].id
        }
        with session.begin():
            with pytest.raises(NotFound):
                read_memories_locked(
                    session, MemoryScopeContext(1, conversations[0]), [items[2].id]
                )
        with pytest.raises(NotFound):
            service(session, 2).get_item(items[0].id)
        assert service(session, 2).list_items().total == 0


def test_chinese_alias_short_word_and_ngram_search_retrieve_real_ids(memory_workspace):
    factory, _projects, conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        proper = manager.create_item(
            memory(
                memory_key="角色林晚",
                title="林晚的人物设定",
                aliases=["小晚"],
                tags=["角色"],
                content="林晚不愿承认自己的身世，雨夜独自来到车站。",
            ),
            "proper",
        )
        hidden = manager.create_item(
            memory(
                "conversation",
                conversation_id=conversations[1],
                memory_key="hidden",
                content="不应被召回的林晚私设",
            ),
            "hidden",
        )
        with session.begin():
            scope = MemoryScopeContext(1, conversations[0])
            for query in ("小晚", "晚", "身世", "车站", "林晚"):
                found = search_memory_locked(session, scope, query)
                assert str(proper.id) in {item["id"] for item in found["items"]}
                assert str(hidden.id) not in {item["id"] for item in found["items"]}
            catalog = memory_catalog_locked(session, scope, token_budget=120)
            assert catalog["items"][0]["id"] == str(proper.id)
            assert catalog["items"][0]["content_version"] == "1"


def test_versions_and_read_statistics_do_not_change_catalog_identity(memory_workspace):
    factory, _projects, conversations = memory_workspace
    with factory() as session:
        manager = service(session)
        item = manager.create_item(memory(), "create")
        assert manager.create_item(memory(), "create").id == item.id
        with session.begin():
            scope = MemoryScopeContext(1, conversations[0])
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
        assert manager.list_items(conversation_id=conversations[0]).total == 1


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
            memory("conversation", conversation_id=conversations[0], memory_key="dialogue"),
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
        assert len(latest.sources) == (1 if choice == "use_candidate" else 0)
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
            session, SimpleNamespace(agent_enabled=True)
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
                    content=f"约束 {seq}",
                    references=[],
                    artifacts=[],
                    created_at=utcnow(),
                )
            )
        conversation.next_message_seq = count + 1
    return accepted


def test_deletion_previews_shared_memory_and_checks_selected_versions(memory_workspace):
    factory, projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory() as session:
        manager = service(session)
        local = manager.create_item(
            memory("conversation", conversation_id=conversations[0]), "local"
        )
        shared = manager.create_item(
            memory(
                source_conversation_id=conversations[0], source_message_ids=[accepted.message.id]
            ),
            "shared",
        )
        edited = manager.create_item(
            memory(
                "project",
                project_id=projects[0],
                source_conversation_id=conversations[0],
                source_message_ids=[accepted.message.id],
            ),
            "edited",
        )
        manager.patch_item(edited.id, {"content_version": "1", "content": "后来修订的作品设定"})
        tasks = AssistantTaskService(session, SimpleNamespace(agent_enabled=True))
        preview = tasks.deletion_memory_preview(conversations[0])
        by_id = {int(item["id"]): item for item in preview["items"]}
        assert set(by_id) == {shared.id, edited.id}
        assert by_id[shared.id]["protection_reasons"] == []
        assert by_id[edited.id]["protection_reasons"] == ["user_edited"]
        version = preview["row_version"]
        manager.patch_item(shared.id, {"content_version": "1", "content": "预览后发生修改"})
        with pytest.raises(Conflict):
            tasks.delete_global_conversation(
                conversations[0],
                {
                    "row_version": version,
                    "clear_derived_memory": True,
                    "memory_selections": [{"id": shared.id, "content_version": "1"}],
                },
                "stale-preview",
            )
        assert tasks.get_conversation(conversations[0]).status == "active"
        result = tasks.delete_global_conversation(
            conversations[0],
            {
                "row_version": version,
                "clear_derived_memory": True,
                "memory_selections": [{"id": shared.id, "content_version": "2"}],
            },
            "confirmed-preview",
        )
        assert result.status == "deleted"
        assert manager.get_item(shared.id).state == "forgotten"
        assert manager.get_item(edited.id).state == "active"
        with pytest.raises(NotFound):
            manager.get_item(local.id)
        with pytest.raises(NotFound):
            manager.create_item(memory("conversation", conversation_id=conversations[0]), "local")
        assert (
            tasks.delete_global_conversation(
                conversations[0],
                {
                    "row_version": version,
                    "clear_derived_memory": True,
                    "memory_selections": [{"id": shared.id, "content_version": "2"}],
                },
                "confirmed-preview",
            ).status
            == "deleted"
        )


def test_deletion_default_preserves_shared_memory_and_purges_source_offline(memory_workspace):
    from short_drama.agent.session.purge import purge_one

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory() as session:
        manager = service(session)
        shared = manager.create_item(
            memory(
                source_conversation_id=conversations[0], source_message_ids=[accepted.message.id]
            ),
            "retained",
        )
        tasks = AssistantTaskService(session, SimpleNamespace(agent_enabled=True))
        current = tasks.get_conversation(conversations[0])
        tasks.delete_global_conversation(
            conversations[0], {"row_version": current.row_version}, "delete"
        )
        assert manager.get_item(shared.id).state == "active"
    assert purge_one(factory, None)
    assert not purge_one(factory, None)
    with factory.begin() as session:
        message = session.get(AgentMessage, accepted.message.id)
        assert message.content == "[已删除]" and message.references == []
        run = session.get(AgentRun, accepted.run.id)
        assert run.checkpoint == {} and run.config_snapshot == {}
        jobs = session.scalars(
            select(AgentMemoryJob).where(
                AgentMemoryJob.conversation_id == conversations[0], AgentMemoryJob.kind == "purge"
            )
        ).all()
        assert len(jobs) == 1 and jobs[0].status == "succeeded"


def test_deletion_preview_protects_independent_sources_and_unrelated_facts(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory.begin() as session:
        other = AgentMessage(
            id=next_id(),
            conversation_id=conversations[1],
            seq=1,
            role="user",
            content="同样要求短对白",
            references=[],
            artifacts=[],
            created_at=utcnow(),
        )
        session.add(other)
        session.get(AgentConversation, conversations[1]).next_message_seq = 2
        other_id = other.id
    with factory() as session:
        manager = service(session)
        sourced = manager.create_item(
            memory(
                source_conversation_id=conversations[0], source_message_ids=[accepted.message.id]
            ),
            "two-sources",
        )
        unrelated = manager.create_item(memory(memory_key="unrelated"), "unrelated")
        with session.begin():
            create_memory_locked(
                session,
                1,
                MemoryCreate.model_validate(memory()),
                origin="extracted",
                sources=source_rows(session, 1, conversations[1], [other_id]),
            )
        tasks = AssistantTaskService(session, SimpleNamespace(agent_enabled=True))
        preview = tasks.deletion_memory_preview(conversations[0])
        assert preview["items"][0]["id"] == str(sourced.id)
        assert preview["items"][0]["protection_reasons"] == ["independent_sources"]
        with pytest.raises(Conflict):
            tasks.delete_global_conversation(
                conversations[0],
                {
                    "row_version": preview["row_version"],
                    "clear_derived_memory": True,
                    "memory_selections": [{"id": unrelated.id, "content_version": "1"}],
                },
                "unrelated-selection",
            )
        with pytest.raises(NotFound):
            session.info["actor"] = actor(2)
            tasks.deletion_memory_preview(conversations[0])


def test_purge_releases_only_owned_unreferenced_uploads_and_retries_storage(memory_workspace):
    from short_drama.agent.session.purge import purge_one
    from short_drama.domain.collaboration import User

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
        tasks = AssistantTaskService(session, SimpleNamespace(agent_enabled=True))
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
    from short_drama.agent.session.purge import purge_one

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory.begin() as session:
        turn = AgentTurn(
            id=next_id(),
            run_id=accepted.run.id,
            turn_no=1,
            status="sent",
            request_messages=[{"private": "original"}],
            response={"raw": {"private": "original"}},
            usage={},
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(turn)
        turn_id = turn.id
    with factory() as session:
        session.info["actor"] = actor()
        tasks = AssistantTaskService(session, SimpleNamespace(agent_enabled=True))
        current = tasks.get_conversation(conversations[0])
        tasks.delete_global_conversation(
            conversations[0], {"row_version": current.row_version}, "purge"
        )
    assert purge_one(factory, None)
    store = AgentRuntimeStore(factory, runtime_settings())
    claim = SimpleNamespace(run_id=accepted.run.id, turn_id=turn_id)
    with pytest.raises(LeaseLost):
        store.save_raw(claim, {"private": "late"})
    with pytest.raises(LeaseLost):
        store.save_result(claim, {"output": "late"})
    with factory.begin() as session:
        turn = session.get(AgentTurn, turn_id)
        assert turn.response is None and turn.request_messages == []


@pytest.mark.parametrize("kind", ["extract", "summary"])
@pytest.mark.parametrize("prior_attempts", [0, 1])
@pytest.mark.parametrize("code", ["model_credentials_unavailable", "agent_credential_unavailable"])
def test_memory_credential_failure_is_terminal_before_provider_send(
    memory_workspace, monkeypatch, kind, prior_attempts, code
):
    from short_drama.agent.gateway.protocol import AgentGatewayError
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
    runtime = AgentMemoryRuntime(factory, runtime_settings(), None)
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
        ("summary", "invalid_agent_memory_result", True, False, 1, "failed"),
        ("extract", "agent_provider_unknown", False, True, 1, "failed"),
        ("extract", "invalid_agent_memory_result", True, True, 1, "failed"),
    ],
)
def test_memory_retry_classification_preserves_terminal_and_unknown_failures(
    memory_workspace, kind, code, receipt, accepted_unknown, attempts, status
):
    from short_drama.agent.gateway.protocol import AgentGatewayError

    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted, kind=kind)
    store = AgentMemoryStore(factory, runtime_settings())
    for version in range(1, attempts + 1):
        claim = store.claim_execution(identifier, version)
        assert claim is not None
        store.before_send(claim, {"attempt": version})
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


def test_edit_and_forget_keep_provenance_and_hide_old_source_from_context(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    with factory() as session:
        manager = service(session)
        item = manager.create_item(
            memory(
                source_conversation_id=conversations[0],
                source_message_ids=[accepted.message.id],
            ),
            "sourced",
        )
        updated = manager.patch_item(
            item.id, {"content_version": "1", "content": "对白长短按场景决定"}
        )
        assert updated.sources == item.sources
        forgotten = manager.forget_item(item.id, {"content_version": "2"})
        assert forgotten.sources == item.sources
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


def test_phase_summary_chain_is_traceable_scoped_and_invalidated_after_forgetting(memory_workspace):
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
        item = manager.create_item(
            memory(source_conversation_id=conversations[0], source_message_ids=[rows[0]["id"]]),
            "chain-source",
        )
        manager.forget_item(item.id, {"content_version": "1"})
        assert manager.conversation_context(conversations[0])["summary"] is None
        for identifier in chain:
            with pytest.raises(NotFound):
                manager.read_summary(conversations[0], identifier)


def runtime_settings():
    return SimpleNamespace(
        agent_enabled=True,
        agent_lease_seconds=180,
        generation_publish_lease_seconds=15,
        encryption_key=None,
    )


@pytest.mark.parametrize("oversized", ["input", "tool_result"])
def test_whole_context_budget_pauses_without_losing_inputs_or_open_tool_results(
    memory_workspace, oversized
):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    settings = runtime_settings()
    content = "明确约束" * 7000 if oversized == "input" else "保留工具回执再回答"
    with factory() as session:
        session.info["actor"] = actor()
        following = AssistantTaskService(session, settings).send_task_message(
            conversations[1],
            {"content": content, "model_config_id": accepted.run.model_config_id},
            "bounded-task",
        )
    pending = {"calls": {"original-call": {"content": "完整回执" * 20000}}}
    if oversized == "tool_result":
        with factory.begin() as session:
            run = session.get(AgentRun, following.run.id)
            run.checkpoint = {
                **run.checkpoint,
                "history": {"original": True},
                "pending_results": pending,
            }
    store = AgentRuntimeStore(factory, settings)
    assert store.claim_execution(following.run.id, 1, prepare_decision) is None
    with factory.begin() as session:
        run = session.get(AgentRun, following.run.id)
        task = session.get(AgentTask, following.task.id)
        assert task.status == "paused_budget"
        assert run.error["code"] == "agent_context_budget_exhausted"
        assert run.usage["decision_calls"] == 0
        assert session.get(AgentMessage, following.message.id).content == content
        assert not session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        if oversized == "tool_result":
            assert run.checkpoint["pending_results"] == pending


def test_provider_envelope_budget_is_checked_before_a_request_is_marked_sent(memory_workspace):
    from short_drama.agent.gateway.protocol import AgentGatewayError

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    settings = runtime_settings()
    with factory() as session:
        session.info["actor"] = actor()
        following = AssistantTaskService(session, settings).send_task_message(
            conversations[1],
            {"content": "小输入", "model_config_id": accepted.run.model_config_id},
            "provider-budget",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(following.run.id, 1, prepare_decision)
    with pytest.raises(AgentGatewayError) as caught:
        store.before_send(
            claim, {"body": {"messages": [{"role": "user", "content": "扩展" * 80000}]}}
        )
    assert caught.value.code == "agent_context_budget_exhausted"
    with factory.begin() as session:
        assert session.get(AgentTurn, claim.turn_id).status == "prepared"
        assert session.get(AgentRun, claim.run_id).usage["decision_calls"] == 0


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
    store.before_send(claim, {"original": "request"})
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
    factory, _projects, _conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    settings = runtime_settings()
    store = AgentMemoryStore(factory, settings)
    claim = store.claim_execution(identifier, 1)
    store.before_send(claim, {"original": "request"})
    store.save_raw(claim, {"original": "receipt"})
    with factory.begin() as session:
        session.get(AgentMemoryJob, identifier).lease_until = utcnow() - timedelta(seconds=1)
    assert store.recover() == 1
    with pytest.raises(LeaseLost):
        store.settle(claim, {}, {})

    class ReplayGateway:
        calls = 0

        async def run_segment(self, *args, **kwargs):
            raise AssertionError("Recovery must not call a paid endpoint")

        async def replay_segment(self, *args, request_payload, raw_response, **kwargs):
            self.calls += 1
            assert request_payload == {"original": "request"}
            assert raw_response == {"original": "receipt"}
            return SimpleNamespace(
                output=json.dumps(
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
                ),
                usage={"output_tokens": 12},
            )

    gateway = ReplayGateway()
    runtime = AgentMemoryRuntime(factory, settings, gateway)
    runtime.execute_one(identifier, 2)
    runtime.execute_one(identifier, 2)
    assert gateway.calls == 1
    with factory.begin() as session:
        job = session.get(AgentMemoryJob, identifier)
        assert job.status == "succeeded" and job.attempts == 1
        item = session.scalar(
            select(AgentMemoryItem).where(AgentMemoryItem.memory_key == "dialogue")
        )
        assert item.scope_type == "conversation" and item.conversation_id == _conversations[0]
        assert item.content_version == 1


def test_late_memory_receipt_cannot_restore_a_forgotten_source(memory_workspace):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    identifier = maintenance_job(memory_workspace, accepted)
    store = AgentMemoryStore(factory, runtime_settings())
    claim = store.claim_execution(identifier, 1)
    store.before_send(claim, {"original": True})
    with factory() as session:
        manager = service(session)
        item = manager.create_item(
            memory(
                source_conversation_id=conversations[0], source_message_ids=[accepted.message.id]
            ),
            "to-forget",
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


def test_forgetting_user_memory_fences_another_conversation_response(memory_workspace):
    from short_drama.domain.agent import AgentEvent

    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    settings = runtime_settings()
    with factory() as session:
        item = service(session).create_item(memory(), "global-before-flight")
        following = AssistantTaskService(session, settings).send_task_message(
            conversations[1],
            {"content": "继续讨论", "model_config_id": accepted.run.model_config_id},
            "flight",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(following.run.id, 1, prepare_decision)
    store.before_send(claim, {"fixture": True})
    with factory() as session:
        service(session).forget_item(item.id, {"content_version": "1"})
    assert store.save_delta(claim, "旧记忆输出", 0) == "stale"
    with factory() as session:
        assert (
            session.scalar(
                select(AgentEvent.id).where(
                    AgentEvent.conversation_id == conversations[1],
                    AgentEvent.event_type == "assistant.delta",
                )
            )
            is None
        )
    store.save_raw(claim, {"fixture_receipt": True})
    assert not store.save_result(
        claim,
        {
            "output_kind": "text",
            "output": "旧记忆输出",
            "history": {"old": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    with factory.begin() as session:
        run = session.get(AgentRun, following.run.id)
        version = run.message_version
        assert run.status == "running" and run.checkpoint["memory_epoch"] == 1
        assert not session.scalars(
            select(AgentMessage).where(
                AgentMessage.conversation_id == conversations[1],
                AgentMessage.role == "assistant",
            )
        ).all()
    resumed = store.claim_execution(following.run.id, version, prepare_decision)
    assert resumed.inputs["kwargs"]["history"] is None
    assert resumed.inputs["memory_epoch"] == 1


@pytest.mark.parametrize("bad_version", [False, True])
def test_memory_tool_result_is_paired_and_allows_a_following_answer(memory_workspace, bad_version):
    factory, _projects, conversations = memory_workspace
    accepted = seeded_run(memory_workspace, count=2)
    settings = runtime_settings()
    with factory() as session:
        item = service(session).create_item(memory(), "readable")
        following = AssistantTaskService(session, settings).send_task_message(
            conversations[1],
            {"content": "查记忆后回答", "model_config_id": accepted.run.model_config_id},
            "read",
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(following.run.id, 1, prepare_decision)
    store.before_send(claim, {"fixture": True})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {
                        "tool_call_id": "memory-call",
                        "tool_name": "read_memory",
                        "args": {
                            "ids": [str(item.id)],
                            "versions": {str(item.id): "99" if bad_version else "1"},
                        },
                    }
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    execute_tools(factory, settings, claim.run_id, fence=(claim.version, claim.token))
    with factory.begin() as session:
        run = session.get(AgentRun, claim.run_id)
        version = run.message_version
    following_claim = store.claim_execution(claim.run_id, version, prepare_decision)
    result = following_claim.inputs["kwargs"]["deferred_results"]["calls"]["memory-call"]
    if bad_version:
        assert result["error"]["code"] == "conflict"
    else:
        assert result["items"][0]["content"] == item.content
    store.before_send(following_claim, {"fixture": "answer"})
    store.save_raw(following_claim, {"fixture_receipt": "answer"})
    store.save_result(
        following_claim,
        {
            "output_kind": "text",
            "output": "已依据读取结果回答",
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 3, "output_tokens_reported": True},
        },
    )
    with factory() as session:
        assert session.get(AgentRun, claim.run_id).status == "succeeded"


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
    factory, _projects, conversations = workspace
    settings = runtime_settings()
    with factory() as session:
        service(session)
        accepted = AssistantTaskService(session, settings).send_task_message(
            conversations[1],
            {
                "content": content,
                "model_config_id": model_id,
                "memory_project_ids": list(projects),
                "new_task": True,
            },
            key,
        )
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, 1, prepare_decision)
    assert claim is not None
    inputs = claim.inputs["kwargs"]
    request = {
        "request_message_id": str(accepted.message.id),
        "request_hash": digest({"role": "user", "content": content}),
        **arguments,
    }
    store.before_send(claim, {"fixture": "memory-command"})
    store.save_raw(claim, {"fixture_receipt": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {"tool_call_id": f"memory-{index}", "tool_name": tool_name, "args": request}
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
        before_execute(accepted)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    complete_operation(factory, settings, accepted.run.id, operation_id)
    execute_tools(factory, settings, accepted.run.id)
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == run.id)
            .order_by(AgentOperation.id)
        ).all()
        version, status = run.message_version, run.status
    if status == "running":
        answer = store.claim_execution(accepted.run.id, version, prepare_decision)
        assert answer is not None
        store.before_send(answer, {"fixture": "answer"})
        store.save_raw(answer, {"fixture_receipt": "answer"})
        store.save_result(
            answer,
            {
                "output_kind": "text",
                "output": "已按真实回执处理",
                "history": {"fixture_history": True},
                "usage": {"output_tokens": 3, "output_tokens_reported": True},
            },
        )
        with factory() as session:
            assert session.get(AgentRun, accepted.run.id).status == "succeeded"
    return accepted, inputs, operations


@pytest.mark.parametrize("scope", ["conversation", "user", "project"])
def test_explicit_memory_tools_use_real_user_text_scope_and_one_durable_effect(
    memory_workspace, scope
):
    factory, projects, conversations = memory_workspace
    model_id = seeded_run(memory_workspace, count=2).run.model_config_id
    prefix = {"conversation": "记住", "user": "记住账号偏好", "project": "记住当前作品设定"}[scope]
    accepted, inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        f"{prefix}：对白简短",
        "remember_memory",
        {"kind": "preference", "memory_key": "dialogue", "title": "对白节奏"},
        key="explicit",
        projects=[str(projects[0])] if scope == "project" else [],
        repeat=2,
    )
    assert len(operations) == 2 and all(row.status == "succeeded" for row in operations)
    assert operations[1].result["already_applied"] is True
    assert "memory_write_requests" in inputs["instructions"]
    with factory() as session:
        item = service(session).get_item(operations[0].result["memory"]["id"])
        assert item.content == "对白简短" and item.scope_type == scope and item.content_version == 1
        assert item.project_id == (projects[0] if scope == "project" else None)
        assert item.conversation_id == (int(conversations[1]) if scope == "conversation" else None)
        assert len(item.sources) == 1 and item.sources[0]["message_id"] == str(accepted.message.id)
        assert "content" not in operations[0].result["memory"]
    with factory.begin() as session:
        conversation = session.get(AgentConversation, conversations[1])
        rows = message_sources(session, conversation.id, 1, 1)
        assert rows[0]["explicit_memory_processed"]
        job = enqueue_job_locked(
            session, conversation, session.get(AgentRun, accepted.run.id), "extract", rows
        )
        session.flush()
        assert settle_maintenance_locked(
            session,
            conversation,
            job,
            {
                "memories": [
                    {
                        "kind": "preference",
                        "memory_key": "different-title",
                        "title": "重复抽取",
                        "content": "对白简短",
                        "source_message_ids": [str(accepted.message.id)],
                    }
                ]
            },
            {},
        )
    with factory() as session:
        assert (
            service(session)
            .list_items(conversation_id=conversations[1], project_id=projects[0])
            .total
            == 1
        )


def test_explicit_memory_lifecycle_protects_old_sources_and_requires_new_restore(memory_workspace):
    factory, _projects, _conversations = memory_workspace
    model_id = seeded_run(memory_workspace, count=2).run.model_config_id
    text = "记住账号偏好：对白简短"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        text,
        "remember_memory",
        {"kind": "preference", "memory_key": "dialogue", "title": "对白节奏"},
        key="remember",
    )
    identifier = operations[0].result["memory"]["id"]
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "修改记忆“对白节奏”为：对白长短按场景",
        "update_memory",
        {"memory_id": identifier, "content_version": "1"},
        key="update",
    )
    assert operations[0].status == "succeeded"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "忘掉账号记忆“对白节奏”",
        "forget_memory",
        {"memory_id": identifier, "content_version": "2"},
        key="forget",
    )
    assert operations[0].result["memory"]["state"] == "forgotten"
    _accepted, _inputs, denied = run_memory_command(
        memory_workspace,
        model_id,
        text,
        "remember_memory",
        {"kind": "preference", "memory_key": "renamed", "title": "换标题"},
        key="old-remember",
    )
    assert denied[0].status == "failed" and denied[0].error["code"] == "conflict"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        model_id,
        "重新记住账号记忆“对白节奏”",
        "restore_memory",
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
        assert len(item.sources) == 3


@pytest.mark.parametrize("forgery", ["other_task", "hash", "body", "project_scope"])
def test_explicit_memory_tool_rejects_forged_authority_or_content(memory_workspace, forgery):
    factory, _projects, _conversations = memory_workspace
    old = seeded_run(memory_workspace, count=2)
    arguments = {"kind": "preference", "memory_key": "forged", "title": "伪造"}
    content = "记住：真实原文"
    if forgery == "other_task":
        arguments["request_message_id"] = str(old.message.id)
    if forgery == "hash":
        arguments["request_hash"] = "0" * 64
    if forgery == "body":
        arguments["content"] = "模型自己补出的内容"
    if forgery == "project_scope":
        content = "记住当前作品：无绑定作品"
    _accepted, _inputs, operations = run_memory_command(
        memory_workspace,
        old.run.model_config_id,
        content,
        "remember_memory",
        arguments,
        key=forgery,
    )
    assert operations[0].status == "failed"
    with factory() as session:
        assert service(session).list_items().total == 0


def test_explicit_memory_tool_rejects_a_newer_edit_even_if_model_guesses_its_version(
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
        "update_memory",
        {"memory_id": str(item.id), "content_version": "2"},
        key="stale-source",
        before_execute=newer_edit,
    )
    assert operations[0].status == "failed" and operations[0].error["code"] == "conflict"
    with factory() as session:
        assert service(session).get_item(item.id).content == "用户后来纠正的内容"
