"""Agent persistence invariants exercised against disposable MySQL databases."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.readiness import assert_agent_ready, assert_identity_ready, inspect_agent_schema
from short_drama.domain import (
    AGENT_TABLES,
    AgentArtifact,
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
    AIGenerationRecord,
    Asset,
    AsyncTask,
    Base,
    MediaAsset,
    MediaFile,
    ProjectScript,
    ShotScript,
)
from short_drama.domain.collaboration import AuditEvent, User
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration


def actor(user_id):
    return ActorContext(
        user_id,
        f"user{user_id}",
        f"user{user_id}@example.test",
        True,
        user_id,
        "csrf",
        f"agent-test-{user_id}",
    )


def migration_module():
    spec = importlib.util.spec_from_file_location(
        "agent_migration_test",
        Path(__file__).resolve().parents[4] / "scripts" / "agent_migration.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_agent_schema_is_strictly_ready(migration_mysql_engine):
    with migration_mysql_engine.connect() as connection:
        state = inspect_agent_schema(connection)
    if state != {"status": "ready", "gaps": []}:
        pytest.fail(str(state), pytrace=False)
    assert_agent_ready(
        migration_mysql_engine, SimpleNamespace(auth_enabled=True, agent_enabled=True)
    )


def test_assistant_preference_migration_is_additive_and_never_resets_a_saved_disable(
    migration_mysql_engine,
):
    from scripts.agent_system_migration import apply_migration
    from short_drama.domain.agent_preference import AgentPreference

    engine = migration_mysql_engine
    with engine.begin() as connection:
        AgentPreference.__table__.drop(connection)
    with engine.connect() as connection:
        assert "agent_preferences" in inspect_agent_schema(connection)["gaps"]
    assert apply_migration(engine)["executed"] == ["table:agent_preferences"]
    with engine.begin() as connection:
        connection.execute(AgentPreference.__table__.insert().values(id=1, owner_user_id=1))
        row = connection.execute(select(AgentPreference.__table__)).mappings().one()
        assert row["media_generation_enabled"] == 1 and row["policy_version"] == 1
        connection.execute(
            AgentPreference.__table__.update().values(media_generation_enabled=0, policy_version=2)
        )
    assert apply_migration(engine)["executed"] == []
    with engine.connect() as connection:
        assert connection.scalar(select(AgentPreference.media_generation_enabled)) == 0
        assert connection.scalar(select(AgentPreference.policy_version)) == 2
        assert inspect_agent_schema(connection) == {"status": "ready", "gaps": []}


def test_memory_retry_status_migration_replaces_only_the_legacy_check_and_is_reentrant(
    migration_mysql_engine,
):
    from scripts.agent_system_migration import apply_migration

    engine = migration_mysql_engine
    with engine.begin() as connection:
        before = connection.scalar(text("SELECT COUNT(*) FROM agent_memory_jobs"))
        connection.exec_driver_sql(
            "ALTER TABLE agent_memory_jobs DROP CHECK ck_agent_memory_job_status, "
            "ADD CONSTRAINT ck_agent_memory_job_status CHECK ("
            "status IN ('pending','running','succeeded','failed','stale','cancelled') "
            "AND provider_state IN ('not_started','sending','received','unknown'))"
        )
    with engine.connect() as connection:
        assert (
            "agent_memory_jobs.ck_agent_memory_job_status:check"
            in inspect_agent_schema(connection)["gaps"]
        )
    assert apply_migration(engine)["executed"] == [
        "check:agent_memory_jobs.ck_agent_memory_job_status"
    ]
    assert apply_migration(engine)["executed"] == []
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM agent_memory_jobs")) == before
        assert inspect_agent_schema(connection) == {"status": "ready", "gaps": []}


def test_missing_agent_tables_do_not_block_prompt_readiness_and_migrate_twice(
    migration_mysql_engine,
):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        Base.metadata.drop_all(
            connection,
            tables=[table for table in Base.metadata.tables.values() if table.name in AGENT_TABLES],
        )
    assert_identity_ready(engine, SimpleNamespace(auth_enabled=True, agent_enabled=False))
    assert_agent_ready(engine, SimpleNamespace(auth_enabled=True, agent_enabled=False))
    with engine.connect() as connection:
        assert inspect_agent_schema(connection)["status"] == "absent"
    with pytest.raises(RuntimeError, match="agent_migration"):
        assert_agent_ready(engine, SimpleNamespace(auth_enabled=True, agent_enabled=True))
    migration = migration_module()
    assert migration.apply(engine)["status"] == "ready"
    assert migration.apply(engine)["status"] == "ready"


def test_assistant_media_migration_adds_only_the_missing_private_ledger_and_is_reentrant(
    migration_mysql_engine,
):
    from scripts.agent_system_migration import apply_migration
    from short_drama.domain.agent_media import AgentMediaRequest

    engine = migration_mysql_engine
    with engine.begin() as connection:
        AgentMediaRequest.__table__.drop(connection)
    with engine.connect() as connection:
        assert "agent_media_requests" in inspect_agent_schema(connection)["gaps"]
        conversations = connection.scalar(select(text("COUNT(*)")).select_from(AgentConversation))
    result = apply_migration(engine)
    assert result["executed"] == ["table:agent_media_requests"]
    assert result["conversations_before"] == result["conversations_after"] == conversations
    assert apply_migration(engine)["executed"] == []
    with engine.connect() as connection:
        assert inspect_agent_schema(connection) == {"status": "ready", "gaps": []}


def test_disabled_check_is_not_mistaken_for_readiness(migration_mysql_engine):
    with migration_mysql_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE agent_runs ALTER CHECK ck_agent_runs_status NOT ENFORCED")
        )
    with migration_mysql_engine.connect() as connection:
        state = inspect_agent_schema(connection)
    assert state["status"] == "partial"
    assert "agent_runs.ck_agent_runs_status:check" in state["gaps"]
    with pytest.raises(RuntimeError, match="definitions do not match"):
        migration_module().apply(migration_mysql_engine)
    with migration_mysql_engine.connect() as connection:
        assert inspect_agent_schema(connection) == state
    assert_identity_ready(
        migration_mysql_engine, SimpleNamespace(auth_enabled=True, agent_enabled=False)
    )


def test_partial_agent_schema_resumes_additive_migration(migration_mysql_engine):
    with migration_mysql_engine.begin() as connection:
        Base.metadata.tables["agent_events"].drop(connection)
    with migration_mysql_engine.connect() as connection:
        assert inspect_agent_schema(connection)["status"] == "partial"
    assert migration_module().apply(migration_mysql_engine) == {"status": "ready", "gaps": []}


def test_weakened_check_grouping_is_not_mistaken_for_readiness(migration_mysql_engine):
    with migration_mysql_engine.begin() as connection:
        connection.execute(text("ALTER TABLE agent_runs DROP CHECK ck_agent_runs_time"))
        connection.execute(
            text(
                "ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_time CHECK ("
                "updated_at >= created_at AND started_at IS NULL OR started_at >= created_at "
                "AND (finished_at IS NULL OR finished_at >= created_at) "
                "AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at))"
            )
        )
    with migration_mysql_engine.connect() as connection:
        state = inspect_agent_schema(connection)
    assert state["status"] == "partial"
    assert "agent_runs.ck_agent_runs_time:check" in state["gaps"]


def seed(session):
    from short_drama.service.ai_model_config_service import AIModelConfigService
    from short_drama.service.project_service import ProjectService

    project = ProjectService(session).create_project({"name": "Agent project"})
    episode = ProjectService(session).get(project.id)
    model = AIModelConfigService(session).create(
        {
            "service_type": "text",
            "name": "Decision",
            "provider": "compatible",
            "model_key": "m",
        }
    )
    with session.begin():
        session.add(
            User(
                id=2,
                username="agent_member",
                display_name="Member",
                email="member@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        session.flush()
    session.info["actor"] = actor(1)
    with session.begin():
        conversation = AgentConversation(
            id=10, owner_user_id=1, project_id=project.id, title="Private"
        )
        message = AgentMessage(id=11, conversation_id=10, seq=1, role="user", content="Draft")
        run = AgentRun(
            id=12,
            conversation_id=10,
            trigger_message_id=11,
            initiated_by=1,
            model_config_id=model.id,
        )
        turn = AgentTurn(id=13, run_id=12, turn_no=1)
        tool = AgentToolCall(
            id=14,
            run_id=12,
            turn_id=13,
            call_index=1,
            provider_call_id="call1",
            tool_name="create_text",
            arguments_hash="a" * 64,
            idempotency_key="b" * 64,
        )
        artifact = AgentArtifact(
            id=15,
            project_id=project.id,
            tool_call_id=14,
            result_index=1,
            kind="text_proposal",
            source_content="Shared candidate",
            created_by=1,
        )
        # Scalar FKs intentionally have no ORM relationships. Flush in dependency order.
        for entity in (conversation, message, run, turn, tool, artifact):
            session.add(entity)
            session.flush()
        session.add(
            AgentEvent(id=19, conversation_id=10, run_id=12, seq=1, event_type="run.queued")
        )
    return episode, model, conversation, message, run, tool, artifact


def test_private_history_artifacts_and_audit_boundary(db_session):
    *_, conversation, _message, _run, _tool, artifact = seed(db_session)
    with db_session.begin():
        audit = db_session.scalars(
            select(AuditEvent).where(AuditEvent.object_type.in_(AGENT_TABLES))
        ).all()
        assert [entry.object_type for entry in audit] == ["agent_artifacts"]
    db_session.info["actor"] = actor(2)
    with db_session.begin():
        assert db_session.scalars(select(AgentConversation)).all() == []
        assert db_session.scalars(select(AgentMessage)).all() == []
        assert db_session.scalars(select(AgentRun)).all() == []
        assert db_session.scalars(select(AgentTurn)).all() == []
        assert db_session.scalars(select(AgentToolCall)).all() == []
        assert db_session.scalars(select(AgentEvent)).all() == []
        assert db_session.scalars(select(AgentArtifact)).all() == []
    with pytest.raises(NotFound), db_session.begin():
        conversation.title = "Another member cannot alter private state"
        db_session.flush()
    with pytest.raises(NotFound), db_session.begin():
        artifact.source_content = "Cannot rewrite immutable shared provenance"
        db_session.flush()


def test_queued_runs_keep_unique_message_triggers_and_message_immutability(db_session):
    _, model, _conversation, message, run, tool, _artifact = seed(db_session)
    with db_session.begin():
        db_session.add(
            AgentMessage(id=16, conversation_id=10, seq=2, role="user", content="Second goal")
        )
    with db_session.begin():
        db_session.add(
            AgentRun(
                id=17,
                conversation_id=10,
                trigger_message_id=16,
                initiated_by=1,
                model_config_id=model.id,
            )
        )
        db_session.flush()
        assert db_session.get(AgentRun, 17).status == run.status == "queued"
    with db_session.begin():
        run.status, run.finished_at, run.updated_at = "cancelled", utcnow(), utcnow()
    with pytest.raises(IntegrityError), db_session.begin():
        db_session.add(
            AgentRun(
                id=18,
                conversation_id=10,
                trigger_message_id=16,
                initiated_by=1,
                model_config_id=model.id,
            )
        )
        db_session.flush()
    with pytest.raises(WorkflowError, match="Append a new Agent record"), db_session.begin():
        message.content = "Changed past request"
        db_session.flush()
    with pytest.raises(WorkflowError, match="Append a new Agent record"), db_session.begin():
        tool.arguments_hash = "c" * 64
        db_session.flush()


def test_run_cannot_reference_another_members_private_model(db_session):
    from short_drama.service.ai_model_config_service import AIModelConfigService

    seed(db_session)
    db_session.info["actor"] = actor(2)
    other_model = AIModelConfigService(db_session).create(
        {
            "service_type": "text",
            "name": "Private model",
            "provider": "compatible",
            "model_key": "m",
        }
    )
    db_session.info["actor"] = actor(1)
    with db_session.begin():
        db_session.add(AgentMessage(id=16, conversation_id=10, seq=2, role="user", content="Next"))
    with pytest.raises(NotFound, match="Agent parent"), db_session.begin():
        db_session.add(
            AgentRun(
                id=17, conversation_id=10, trigger_message_id=16, model_config_id=other_model.id
            )
        )
        db_session.flush()


def test_artifact_typed_references_match_episode_project_and_generation(db_session):
    from short_drama.service.project_service import ProjectService

    episode, model, *_ = seed(db_session)
    other_episode = ProjectService(db_session).create_project({"name": "Other work"})
    other_project = ProjectService(db_session).create_project({"name": "Other project"})
    with db_session.begin():
        references = [
            ProjectScript(id=30, project_id=other_episode.id, position=1, content="Other script"),
            ProjectScript(id=31, project_id=episode.id, position=1, content="Local script"),
            ShotScript(id=32, project_id=other_episode.id, position=1),
            ShotScript(id=33, project_id=episode.id, position=1),
            Asset(
                id=34,
                project_id=episode.id,
                position=1,
                deleted_at=utcnow(),
                name="Archived",
                kind="character",
            ),
            Asset(id=35, project_id=episode.id, position=2, name="Local", kind="character"),
            Asset(id=36, project_id=other_project.id, position=1, name="Foreign", kind="character"),
            MediaFile(
                id=37,
                project_id=other_project.id,
                format_code="image/png",
                storage_locator="foreign.png",
            ),
            MediaFile(
                id=38, project_id=episode.id, format_code="image/png", storage_locator="local.png"
            ),
            MediaFile(
                id=39,
                project_id=episode.id,
                format_code="image/png",
                storage_locator="other-local.png",
            ),
        ]
        db_session.add_all(references)
        db_session.flush()
        for identifier, project_id in (
            (41, other_project.id),
            (42, episode.id),
            (43, episode.id),
            (44, episode.id),
        ):
            db_session.add(
                AsyncTask(
                    id=identifier,
                    project_id=project_id,
                    service_type="image",
                    idempotency_key=f"ref-{identifier}",
                    request_hash="f" * 64,
                )
            )
        db_session.flush()
        for identifier, task_id, source_project_id in (
            (45, 41, other_episode.id),
            (46, 42, other_episode.id),
            (47, 43, episode.id),
            (48, 44, episode.id),
        ):
            db_session.add(
                AIGenerationRecord(
                    id=identifier,
                    task_id=task_id,
                    call_no=1,
                    config_id=model.id,
                    config_snapshot={},
                    request_data={"source": {"project_id": str(source_project_id)}},
                )
            )
        db_session.flush()
        db_session.add_all(
            [
                MediaAsset(
                    id=49,
                    record_id=45,
                    output_index=1,
                    media_id=37,
                    media_type="image",
                    name="Foreign output",
                ),
                MediaAsset(
                    id=50,
                    record_id=47,
                    output_index=1,
                    media_id=38,
                    media_type="image",
                    name="Local output",
                ),
            ]
        )
    invalid_references = (
        ({"script_id": 30}, "(?:outside this (?:project|creation)|Agent parent does not exist)"),
        (
            {"parent_script_id": 30},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        (
            {"target_shot_id": 32},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        (
            {"target_asset_id": 36},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        (
            {"target_asset_id": 34},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        ({"media_id": 37}, "(?:outside this (?:project|creation)|Agent parent does not exist)"),
        (
            {"generation_task_id": 41},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        (
            {"generation_task_id": 42},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        (
            {"media_asset_id": 49},
            "(?:outside this (?:project|creation)|Agent parent does not exist)",
        ),
        ({"media_asset_id": 50, "media_id": 39}, "media references do not match"),
        ({"media_asset_id": 50, "generation_task_id": 44}, "generation references do not match"),
    )
    for refs, error in invalid_references:
        with pytest.raises(NotFound, match=error), db_session.begin():
            db_session.add(
                AgentArtifact(
                    id=51,
                    project_id=episode.id,
                    tool_call_id=14,
                    result_index=2,
                    kind="text_proposal",
                    source_content="Candidate",
                    created_by=1,
                    **refs,
                )
            )
            db_session.flush()
    with db_session.begin():
        # Every typed reference is accepted when it resolves to this episode/project/output.
        db_session.add(
            AgentArtifact(
                id=51,
                project_id=episode.id,
                tool_call_id=14,
                result_index=2,
                kind="text_proposal",
                source_content="Candidate",
                created_by=1,
                script_id=31,
                parent_script_id=31,
                target_shot_id=33,
                target_asset_id=35,
                generation_task_id=43,
                media_asset_id=50,
                media_id=38,
            )
        )
