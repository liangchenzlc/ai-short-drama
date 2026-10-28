import base64

import pytest
from sqlalchemy.orm import sessionmaker

from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.domain.identity.accounts.user import User
from short_drama.service.base import utcnow
from short_drama.service.projects.management.project import ProjectService


def actor(identifier):
    return ActorContext(identifier, "creator", "creator@example.test", True, 1, "hash", "test")


@pytest.fixture
def workspace(db_session):
    db_session.info["actor"] = actor(1)
    project = ProjectService(db_session).create_project(
        {
            "name": "Agent work",
        }
    )
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
    return factory, project.id, None


def settings():
    return Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=True,
        auth_cookie_secure=False,
        public_origin="http://testserver",
        encryption_key=base64.b64encode(b"0" * 32).decode(),
    )
