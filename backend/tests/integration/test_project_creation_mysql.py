import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.db.session import session_factory
from short_drama.domain import Episode, Project
from short_drama.main import create_app
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService

pytestmark = pytest.mark.integration


def test_http_project_and_episode_persist(mysql_engine, db_session):
    async def run():
        app = create_app(Settings(_env_file=None))
        factory = session_factory(mysql_engine)

        def test_session():
            with factory() as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/projects", json={"name": "story", "aspect": "9:16", "style": "ink"}
            )
            assert response.status_code == 201, response.text
            project_id = response.json()["id"]
            root = f"/api/v1/projects/{project_id}/episodes"
            for position in (1, 2):
                response = await client.post(root, json={"title": "same title"})
                assert response.status_code == 201, response.text
                assert response.json()["position"] == position
                assert response.json()["aspect"] == "9:16"
                assert response.json()["style"] == "ink"
            missing = await client.post("/api/v1/projects/1/episodes", json={"title": "missing"})
            assert missing.status_code == 404
            return int(project_id)

    project_id = asyncio.run(run())
    with db_session.begin():
        project = db_session.get(Project, project_id)
        assert project.created_at == project.last_opened_at == project.updated_at
        episodes = list(db_session.scalars(select(Episode).where(Episode.project_id == project_id)))
        assert len(episodes) == 2


def test_concurrent_append_to_empty_and_gapped_collections(mysql_engine, db_session):
    project = ProjectService(db_session).create_project({"name": "concurrent", "aspect": "16:9"})

    def append_batch():
        barrier = Barrier(4)

        def append(index):
            with Session(mysql_engine, expire_on_commit=False, autoflush=False) as session:
                barrier.wait(timeout=10)
                return EpisodeService(session).create_for_project(project.id, {"title": str(index)})

        with ThreadPoolExecutor(max_workers=4) as executor:
            return list(executor.map(append, range(4)))

    first = append_batch()
    assert sorted(row.position for row in first) == [1, 2, 3, 4]
    highest = max(first, key=lambda row: row.position)
    EpisodeService(db_session).update(highest.id, {"position": 10})
    second = append_batch()
    assert sorted(row.position for row in second) == [11, 12, 13, 14]
    assert len({row.id for row in first + second}) == 8


def test_remove_target_migration_preserves_projects_and_is_repeatable(mysql_engine, db_session):
    project = ProjectService(db_session).create_project({"name": "preserved", "aspect": "9:16"})
    migration = next(
        (Path(__file__).resolve().parents[3] / "docs").rglob("001_remove_project_target.sql")
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
