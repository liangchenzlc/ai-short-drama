import asyncio
import re
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from short_drama.api.dependencies.session import get_session
from short_drama.core.config import Settings
from short_drama.domain import Project
from short_drama.main import create_app
from short_drama.service.projects.management.project import ProjectService
from tests.support.integration.identity import session_factory

pytestmark = pytest.mark.integration


def test_http_project_and_script_persist(mysql_engine, db_session):
    async def run():
        url = mysql_engine.url
        app = create_app(
            Settings(
                _env_file=None,
                db_host=url.host,
                db_port=url.port or 3306,
                db_user=url.username,
                db_password=url.password or "",
                db_name=url.database,
                auth_enabled=False,
                agent_enabled=False,
            )
        )
        factory = session_factory(mysql_engine)

        def test_session():
            with factory() as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
        ):
            response = await client.post("/api/v1/projects", json={"name": "story"})
            assert response.status_code == 201, response.text
            project_id = response.json()["id"]
            root = f"/api/v1/projects/{project_id}"
            assert (await client.get(root)).json()["name"] == "story"
            saved = await client.put(
                root + "/script",
                json={"content_version": "1", "script_id": None, "content": "  持久化正文\n"},
            )
            assert saved.status_code == 200, saved.text
            current = await client.get(root + "/writing")
            assert current.status_code == 200
            assert current.json()["editing_script"]["content"] == "  持久化正文\n"
            assert (
                await client.post(root + "/episodes", json={"title": "retired"})
            ).status_code == 404
            return int(project_id)

    project_id = asyncio.run(run())
    with db_session.begin():
        project = db_session.get(Project, project_id)
        assert project.created_at == project.last_opened_at == project.updated_at


def test_remove_target_migration_preserves_projects_and_is_repeatable(mysql_engine, db_session):
    project = ProjectService(db_session).create_project({"name": "preserved"})
    migration = next(
        (Path(__file__).resolve().parents[5] / "docs").rglob("001_remove_project_target.sql")
    )
    source = re.sub(r"(?m)^\s*--.*$", "", migration.read_text(encoding="utf-8"))
    with mysql_engine.begin() as connection:
        # Recreate only the obsolete fields in this isolated test database.
        connection.execute(
            text(
                "ALTER TABLE projects ADD COLUMN target_ms INT UNSIGNED NOT NULL DEFAULT 60000, "
                "ADD CONSTRAINT ck_projects_target CHECK (target_ms BETWEEN 1000 AND 3600000)"
            )
        )
        for _ in range(2):
            for statement in source.split(";"):
                if statement.strip():
                    connection.execute(text(statement))
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='projects' AND COLUMN_NAME='target_ms'"
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='projects' "
                    "AND CONSTRAINT_NAME='ck_projects_target'"
                )
            )
            == 0
        )
    assert ProjectService(db_session).get(project.id).name == "preserved"
