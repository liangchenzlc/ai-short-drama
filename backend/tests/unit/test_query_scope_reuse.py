from types import SimpleNamespace

import pytest
from generation_fixtures import generation_session
from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from short_drama.core.exceptions import WorkflowError
from short_drama.db import access as _access  # noqa: F401
from short_drama.domain import AgentConversation, Episode, Project, ProjectMember, User
from short_drama.service.base import utcnow


def seed_scopes(session):
    session.info.pop("legacy_user_id", None)
    now = utcnow()
    session.add_all(
        [
            User(
                id=identifier,
                username=f"scope_user_{identifier}",
                display_name="Scope fixture",
                email=f"scope_{identifier}@example.test",
                password_hash="test-only",
                status="active",
                created_at=now,
            )
            for identifier in (2, 3)
        ]
    )
    session.flush()
    session.add_all(
        [
            Project(id=10, owner_user_id=1, name="Shared", aspect="16:9"),
            Project(id=20, owner_user_id=3, name="Other", aspect="16:9"),
        ]
    )
    session.flush()
    session.add_all(
        [
            Episode(id=11, project_id=10, position=1, title="Shared episode", aspect="16:9"),
            Episode(id=21, project_id=20, position=1, title="Other episode", aspect="16:9"),
            ProjectMember(id=30, project_id=10, user_id=2, status="active", joined_at=now),
        ]
    )
    session.flush()
    session.add(
        AgentConversation(
            id=40,
            owner_user_id=1,
            project_id=10,
            episode_id=11,
            title="Private conversation",
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()


def visible_ids(session, model):
    return set(session.scalars(select(model.id)))


def test_repeated_scope_queries_follow_actor_changes_aliases_and_private_ownership():
    with generation_session() as session:
        seed_scopes(session)
        project_alias = aliased(Project)
        for identifier, projects, episodes, conversations in (
            (1, {10}, {11}, {40}),
            (2, {10}, {11}, set()),
            (3, {20}, {21}, set()),
            (1, {10}, {11}, {40}),
        ):
            session.info["actor"] = SimpleNamespace(user_id=identifier)
            assert visible_ids(session, Project) == projects
            assert visible_ids(session, Episode) == episodes
            assert visible_ids(session, AgentConversation) == conversations
            assert set(session.scalars(select(project_alias.id))) == projects
            assert session.scalar(select(func.count()).select_from(Project)) == len(projects)
            session.rollback()


def test_scope_reuse_does_not_cache_membership_or_archived_project_results():
    with generation_session() as session:
        seed_scopes(session)
        session.info["actor"] = SimpleNamespace(user_id=2)
        assert visible_ids(session, Episode) == {11}
        session.rollback()
        session.info.pop("actor")
        session.execute(
            update(ProjectMember).where(ProjectMember.id == 30).values(status="removed")
        )
        session.commit()
        session.info["actor"] = SimpleNamespace(user_id=2)
        assert visible_ids(session, Project) == set()
        assert visible_ids(session, Episode) == set()
        session.rollback()
        session.info["actor"] = SimpleNamespace(user_id=1)
        assert visible_ids(session, Project) == {10}
        session.rollback()
        session.info.pop("actor")
        session.execute(update(Project).where(Project.id == 10).values(archived_at=utcnow()))
        session.commit()
        session.info["actor"] = SimpleNamespace(user_id=1)
        assert visible_ids(session, Project) == set()
        assert visible_ids(session, Episode) == set()
        assert visible_ids(session, AgentConversation) == set()


def test_scope_reuse_preserves_unscoped_and_guarded_bulk_write_rejections():
    with generation_session() as session:
        seed_scopes(session)
        session.info["actor"] = SimpleNamespace(user_id=2)
        assert visible_ids(session, Project) == {10}
        with pytest.raises(WorkflowError, match="scoped ORM"):
            session.execute(Project.__table__.select())
        with pytest.raises(WorkflowError, match="Ownership and references"):
            session.execute(update(Project).values(owner_user_id=2))
        with pytest.raises(WorkflowError, match="checked entity writes"):
            session.execute(update(AgentConversation).values(title="Illegal private edit"))
        session.rollback()
        session.info["actor"] = SimpleNamespace(user_id=3)
        assert (
            session.execute(update(Project).where(Project.id == 10).values(name="Denied")).rowcount
            == 0
        )
