"""Real MySQL evidence for private conversation ownership and transaction contracts."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext, token_hash
from short_drama.domain import AIModelConfig
from short_drama.domain.agent import AgentMessage, AgentRun
from short_drama.domain.collaboration import AuditEvent, ProjectMember, User, UserSession
from short_drama.main import create_app
from short_drama.schemas.agent import ConversationCreate, ConversationPatch
from short_drama.service.agent_conversation_service import AgentConversationService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


def actor(identifier):
    return ActorContext(identifier, "creator", "creator@example.test", True, 1, "hash", "test")


@pytest.fixture
def workspace(db_session):
    db_session.info["actor"] = actor(1)
    project = ProjectService(db_session).create_project({"name": "Agent work", "aspect": "16:9"})
    episode = EpisodeService(db_session).create_for_project(project.id, {"title": "Episode 1"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    with factory() as system, system.begin():
        system.add(
            User(
                id=2,
                username="agent_member",
                display_name="Member",
                email="member@example.test",
                password_hash="test",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        system.flush()
        member = ProjectMember(
            id=next_id(), project_id=project.id, user_id=2, status="active", joined_at=utcnow()
        )
        system.add(member)
    return factory, project.id, episode.id, member.id


def service(session, identifier=1, *, enabled=True):
    session.info["actor"] = actor(identifier)
    return AgentConversationService(session, SimpleNamespace(agent_enabled=enabled))


def test_private_conversations_do_not_leak_to_project_members(workspace):
    factory, project_id, episode_id, member_id = workspace
    with factory() as session:
        owner = service(session)
        created = owner.create_conversation(
            {"project_id": project_id, "episode_id": episode_id, "title": "Private requirements"},
            "create-1",
        )
    with factory() as session:
        member = service(session, 2)
        assert member.list_conversations(project_id, episode_id).items == []
        with pytest.raises(NotFound):
            member.get_conversation(created.id)
        with pytest.raises(NotFound):
            member.patch_conversation(created.id, {"row_version": 1, "title": "attack"})
        own = member.create_conversation({"project_id": project_id, "episode_id": episode_id})
    with factory() as session, session.begin():
        audits = session.scalars(
            select(AuditEvent).where(AuditEvent.project_id == project_id)
        ).all()
        assert not any(row.object_type.startswith("agent_") for row in audits)
        session.get(ProjectMember, member_id).status = "removed"
    with factory() as session:
        with pytest.raises(NotFound):
            service(session, 2).get_conversation(own.id)


def test_create_replay_version_checks_and_archive_restore(workspace):
    factory, project_id, episode_id, _ = workspace
    payload = ConversationCreate(project_id=project_id, episode_id=episode_id, title="Start")
    with factory() as session:
        svc = service(session)
        created = svc.create_conversation(payload, "stable-request")
        assert svc.create_conversation(payload, "stable-request").id == created.id
        with pytest.raises(Conflict):
            svc.create_conversation(payload.model_copy(update={"title": "Other"}), "stable-request")
        renamed = svc.patch_conversation(created.id, ConversationPatch(row_version=1, title="New"))
        assert renamed.row_version == 2
        with pytest.raises(WorkflowError, match="changed"):
            svc.patch_conversation(created.id, {"row_version": 1, "archived": True})
        archived = svc.patch_conversation(created.id, {"row_version": 2, "archived": True})
        assert archived.archived
        assert svc.list_conversations(project_id, episode_id).total == 0
        assert svc.list_conversations(project_id, episode_id, include_archived=True).total == 1
        restored = svc.patch_conversation(created.id, {"row_version": 3, "archived": False})
        assert restored.row_version == 4 and not restored.archived


def test_active_run_must_stop_before_archive(workspace):
    factory, project_id, episode_id, _ = workspace
    with factory() as session:
        conversation = service(session).create_conversation(
            {"project_id": project_id, "episode_id": episode_id}
        )
    with factory() as session, session.begin():
        config_id, message_id = next_id(), next_id()
        session.add(
            AIModelConfig(
                id=config_id,
                owner_user_id=1,
                service_type="text",
                name="Agent",
                model_key="test",
                provider="openai",
                base_url="https://example.com",
            )
        )
        session.add(
            AgentMessage(
                id=message_id,
                conversation_id=conversation.id,
                seq=1,
                role="user",
                content="Create a script",
            )
        )
        session.flush()
        session.add(
            AgentRun(
                id=next_id(),
                conversation_id=conversation.id,
                trigger_message_id=message_id,
                initiated_by=1,
                model_config_id=config_id,
            )
        )
    with factory() as session:
        with pytest.raises(WorkflowError, match="Stop"):
            service(session).patch_conversation(
                conversation.id, {"row_version": 1, "archived": True}
            )


def test_disabled_feature_and_cross_episode_scope(workspace):
    factory, project_id, episode_id, _ = workspace
    with factory() as session:
        with pytest.raises(WorkflowError, match="尚未启用"):
            service(session, enabled=False).list_conversations(project_id, episode_id)
        with pytest.raises(NotFound):
            service(session).create_conversation(
                {"project_id": project_id, "episode_id": next_id()}
            )


def test_authenticated_api_creation_and_private_access(workspace):
    factory, project_id, episode_id, _ = workspace
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=True,
        auth_cookie_secure=False,
        public_origin="http://testserver",
    )
    app = create_app(settings)
    app.state.session_factory = factory
    app.state.agent_schema_ready = True

    def client(identifier):
        token, csrf = f"test-token-{identifier}", f"test-csrf-{identifier}"
        with factory() as session, session.begin():
            session.add(
                UserSession(
                    id=next_id(),
                    user_id=identifier,
                    token_hash=token_hash(token),
                    csrf_hash=token_hash(csrf),
                    created_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=1),
                )
            )
        result = TestClient(app)
        result.cookies.set("sd_session", token)
        result.headers.update({"Origin": settings.public_origin, "X-CSRF-Token": csrf})
        return result

    owner, member = client(1), client(2)
    assert TestClient(app).get("/api/v1/agent/status").status_code == 401
    assert owner.get("/api/v1/agent/status").json() == {"enabled": True, "schema_ready": True}
    body = {"project_id": str(project_id), "episode_id": str(episode_id), "title": "Private"}
    created = owner.post(
        "/api/v1/agent/conversations", json=body, headers={"Idempotency-Key": "api-create"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["row_version"] == 1
    identifier = created.json()["id"]
    repeated = owner.post(
        "/api/v1/agent/conversations", json=body, headers={"Idempotency-Key": "api-create"}
    )
    assert repeated.json()["id"] == identifier
    assert member.get(f"/api/v1/agent/conversations/{identifier}").status_code == 404
    assert (
        member.patch(
            f"/api/v1/agent/conversations/{identifier}", json={"row_version": 1, "title": "Attack"}
        ).status_code
        == 404
    )
    assert (
        owner.patch(
            f"/api/v1/agent/conversations/{identifier}", json={"row_version": 1, "title": "Updated"}
        ).json()["row_version"]
        == 2
    )
    stale = owner.patch(
        f"/api/v1/agent/conversations/{identifier}", json={"row_version": 1, "title": "Stale"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == "2"
