from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

import short_drama.db.access as _access  # noqa: F401
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AgentConversation, Project, User
from short_drama.service.base import utcnow
from tests.support.generation_fixtures import generation_session


def seed_scopes(session):
    session.info.pop("actor", None)
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
            Project(id=10, owner_user_id=1, name="Owned"),
            Project(id=20, owner_user_id=3, name="Other"),
        ]
    )
    session.flush()
    session.add(
        AgentConversation(
            id=40,
            owner_user_id=1,
            project_id=10,
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
        for identifier, projects, conversations in (
            (1, {10}, {40}),
            (2, set(), set()),
            (3, {20}, set()),
            (1, {10}, {40}),
        ):
            session.info["actor"] = SimpleNamespace(user_id=identifier)
            assert visible_ids(session, Project) == projects
            assert visible_ids(session, AgentConversation) == conversations
            assert set(session.scalars(select(project_alias.id))) == projects
            assert session.scalar(select(func.count()).select_from(Project)) == len(projects)
            session.rollback()


def test_scope_reuse_does_not_cache_ownership_or_archived_project_results():
    with generation_session() as session:
        seed_scopes(session)
        session.info["actor"] = SimpleNamespace(user_id=1)
        assert visible_ids(session, Project) == {10}
        session.rollback()
        session.info.pop("actor")
        session.execute(update(Project).where(Project.id == 10).values(owner_user_id=2))
        session.commit()
        session.info["actor"] = SimpleNamespace(user_id=1)
        assert visible_ids(session, Project) == set()
        assert visible_ids(session, AgentConversation) == set()
        session.rollback()
        session.info["actor"] = SimpleNamespace(user_id=2)
        assert visible_ids(session, Project) == {10}
        assert visible_ids(session, AgentConversation) == set()
        session.rollback()
        session.info.pop("actor")
        session.execute(update(Project).where(Project.id == 10).values(archived_at=utcnow()))
        session.commit()
        session.info["actor"] = SimpleNamespace(user_id=2)
        assert visible_ids(session, Project) == set()
        assert visible_ids(session, AgentConversation) == set()


def test_scope_reuse_preserves_unscoped_and_guarded_bulk_write_rejections():
    with generation_session() as session:
        seed_scopes(session)
        session.info["actor"] = SimpleNamespace(user_id=2)
        assert visible_ids(session, Project) == set()
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
