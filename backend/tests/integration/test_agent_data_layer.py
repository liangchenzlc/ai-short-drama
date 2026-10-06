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
    EpisodeAsset,
    EpisodeScript,
    MediaAsset,
    MediaFile,
    ShotScript,
)
from short_drama.domain.collaboration import AuditEvent, ProjectMember, User
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
        Path(__file__).resolve().parents[2] / "scripts" / "agent_migration.py",
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


def test_missing_agent_tables_do_not_block_prompt_readiness_and_migrate_twice(
    migration_mysql_engine,
):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in AGENT_TABLES:
                table.drop(connection)
    assert_identity_ready(engine, SimpleNamespace(auth_enabled=True, agent_enabled=False))
    assert_agent_ready(engine, SimpleNamespace(auth_enabled=True, agent_enabled=False))
    with engine.connect() as connection:
        assert inspect_agent_schema(connection)["status"] == "absent"
    with pytest.raises(RuntimeError, match="agent_migration"):
        assert_agent_ready(engine, SimpleNamespace(auth_enabled=True, agent_enabled=True))
    migration = migration_module()
    assert migration.apply(engine)["status"] == "ready"
    assert migration.apply(engine)["status"] == "ready"


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
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.project_service import ProjectService

    project = ProjectService(session).create({"name": "Agent project", "aspect": "16:9"})
    episode = EpisodeService(session).create(
        {
            "project_id": project.id,
            "title": "Episode",
            "position": 1,
            "aspect": "16:9",
        }
    )
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
        session.add(
            ProjectMember(
                id=3, project_id=project.id, user_id=2, status="active", joined_at=utcnow()
            )
        )
    session.info["actor"] = actor(1)
    with session.begin():
        conversation = AgentConversation(
            id=10, owner_user_id=1, project_id=project.id, episode_id=episode.id, title="Private"
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
            episode_id=episode.id,
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
    return project, episode, model, conversation, message, run, tool, artifact


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


def test_revoked_member_loses_all_private_layers_and_shared_outputs(db_session):
    from short_drama.service.ai_model_config_service import AIModelConfigService

    project, episode, *_ = seed(db_session)
    db_session.info["actor"] = actor(2)
    conversation = AgentConversation(
        id=20, owner_user_id=2, project_id=project.id, episode_id=episode.id, title="Member private"
    )
    message = AgentMessage(id=21, conversation_id=20, seq=1, role="user", content="Private")
    # The model is private to this member; no project owner credentials are reused.
    model = AIModelConfigService(db_session).create(
        {
            "service_type": "text",
            "name": "Member decision",
            "provider": "compatible",
            "model_key": "m",
        }
    )
    with db_session.begin():
        run = AgentRun(id=22, conversation_id=20, trigger_message_id=21, model_config_id=model.id)
        turn = AgentTurn(id=23, run_id=22, turn_no=1)
        tool = AgentToolCall(
            id=24,
            run_id=22,
            turn_id=23,
            call_index=1,
            provider_call_id="member-call",
            tool_name="create_text",
            arguments_hash="d" * 64,
            idempotency_key="e" * 64,
        )
        event = AgentEvent(id=25, conversation_id=20, run_id=22, seq=1, event_type="run.queued")
        for entity in (conversation, message, run, turn, tool, event):
            db_session.add(entity)
            db_session.flush()
        for model_type in (
            AgentConversation,
            AgentMessage,
            AgentRun,
            AgentTurn,
            AgentToolCall,
            AgentEvent,
        ):
            assert len(db_session.scalars(select(model_type)).all()) == 1
    db_session.info["actor"] = actor(1)
    with db_session.begin():
        member = db_session.scalar(select(ProjectMember).where(ProjectMember.user_id == 2))
        member.status, member.removed_at = "removed", utcnow()
    db_session.info["actor"] = actor(2)
    with db_session.begin():
        for model_type in (
            AgentConversation,
            AgentMessage,
            AgentRun,
            AgentTurn,
            AgentToolCall,
            AgentEvent,
            AgentArtifact,
        ):
            assert db_session.scalars(select(model_type)).all() == []


def test_queued_runs_keep_unique_message_triggers_and_message_immutability(db_session):
    _, _, model, _conversation, message, run, tool, _artifact = seed(db_session)
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
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.project_service import ProjectService

    project, episode, model, *_ = seed(db_session)
    other_episode = EpisodeService(db_session).create(
        {"project_id": project.id, "title": "Other episode", "position": 2, "aspect": "16:9"}
    )
    other_project = ProjectService(db_session).create({"name": "Other project", "aspect": "16:9"})
    with db_session.begin():
        references = [
            EpisodeScript(id=30, episode_id=other_episode.id, position=1, content="Other script"),
            EpisodeScript(id=31, episode_id=episode.id, position=1, content="Local script"),
            ShotScript(id=32, episode_id=other_episode.id, position=1),
            ShotScript(id=33, episode_id=episode.id, position=1),
            Asset(id=34, project_id=project.id, name="Unlinked", kind="character"),
            Asset(id=35, project_id=project.id, name="Linked", kind="character"),
            Asset(id=36, project_id=other_project.id, name="Foreign", kind="character"),
            MediaFile(
                id=37,
                project_id=other_project.id,
                format_code="image/png",
                storage_locator="foreign.png",
            ),
            MediaFile(
                id=38, project_id=project.id, format_code="image/png", storage_locator="local.png"
            ),
            MediaFile(
                id=39,
                project_id=project.id,
                format_code="image/png",
                storage_locator="other-local.png",
            ),
        ]
        db_session.add_all(references)
        db_session.flush()
        db_session.add(EpisodeAsset(id=40, episode_id=episode.id, asset_id=35, position=1))
        for identifier, project_id in (
            (41, other_project.id),
            (42, project.id),
            (43, project.id),
            (44, project.id),
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
        for identifier, task_id, source_episode_id in (
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
                    request_data={"source": {"episode_id": str(source_episode_id)}},
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
        ({"script_id": 30}, "outside this episode"),
        ({"parent_script_id": 30}, "outside this episode"),
        ({"target_shot_id": 32}, "outside this episode"),
        ({"target_asset_id": 36}, "outside this project"),
        ({"target_asset_id": 34}, "not linked to this episode"),
        ({"media_id": 37}, "outside this project"),
        ({"generation_task_id": 41}, "outside this project"),
        ({"generation_task_id": 42}, "outside this episode"),
        ({"media_asset_id": 49}, "outside this project"),
        ({"media_asset_id": 50, "media_id": 39}, "media references do not match"),
        ({"media_asset_id": 50, "generation_task_id": 44}, "generation references do not match"),
    )
    for refs, error in invalid_references:
        with pytest.raises(NotFound, match=error), db_session.begin():
            db_session.add(
                AgentArtifact(
                    id=51,
                    project_id=project.id,
                    episode_id=episode.id,
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
                project_id=project.id,
                episode_id=episode.id,
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
