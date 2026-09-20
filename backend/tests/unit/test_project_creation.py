import asyncio
from datetime import datetime

import httpx
import pytest
from generation_fixtures import generation_session
from pydantic import ValidationError
from sqlalchemy import func, select

from short_drama.api.dependencies import get_episode_service, get_project_service
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, DatabaseUnavailable, NotFound
from short_drama.domain import EpisodeNovel, EpisodeScript, Project
from short_drama.main import create_app
from short_drama.schemas.episode import EpisodeRead
from short_drama.schemas.project import ProjectRead
from short_drama.schemas.project_creation import EpisodeCreateRequest, ProjectCreateRequest
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService


def test_creation_inherits_current_defaults_and_keeps_existing_episodes_independent():
    with generation_session() as session:
        projects = ProjectService(session)
        episodes = EpisodeService(session)
        project = projects.create_project({"name": "  story  ", "aspect": "9:16", "style": "ink"})
        assert project.name == "story"
        assert project.last_opened_at == project.created_at == project.updated_at
        assert project.created_by is project.updated_by is None
        first = episodes.create_for_project(project.id, {"title": "  first  "})
        assert (first.position, first.title, first.aspect, first.style) == (
            1,
            "first",
            "9:16",
            "ink",
        )
        assert projects.get(project.id).updated_at == project.updated_at
        projects.update(project.id, {"aspect": "16:9", "style": "watercolor"})
        inherited = episodes.create_for_project(project.id, {"title": "second"})
        assert (inherited.aspect, inherited.style) == ("16:9", "watercolor")
        override = episodes.create_for_project(
            project.id, {"title": "second", "aspect": "9:16", "style": ""}
        )
        assert (override.position, override.aspect, override.style) == (3, "9:16", "")
        assert episodes.get(first.id).style == "ink"
        assert episodes.get(first.id).aspect == "9:16"
        for model in (EpisodeNovel, EpisodeScript):
            with session.begin():
                assert session.scalar(select(func.count()).select_from(model)) == 0
        # Sorting uses MAX + 1, not COUNT + 1; gaps are valid.
        episodes.update(override.id, {"position": 10})
        assert episodes.create_for_project(project.id, {"title": "after gap"}).position == 11


def test_missing_parent_and_order_exhaustion_leave_no_partial_record():
    with generation_session() as session:
        episodes = EpisodeService(session)
        with pytest.raises(NotFound):
            episodes.create_for_project(123, {"title": "missing"})
        project = ProjectService(session).create_project({"name": "story", "aspect": "16:9"})
        episodes.create(
            {"project_id": project.id, "position": 2**32 - 1, "title": "last", "aspect": "16:9"}
        )
        with pytest.raises(Conflict):
            episodes.create_for_project(project.id, {"title": "overflow"})
        assert episodes.list().total == 1
        assert not session.in_transaction()


def test_insert_failure_rolls_back_and_releases_transaction(monkeypatch):
    with generation_session() as session:
        project = ProjectService(session).create_project({"name": "story", "aspect": "16:9"})
        episodes = EpisodeService(session)
        original = episodes.dao.create

        def fail_after_insert(values):
            original(values)
            raise RuntimeError("injected failure after flush")

        monkeypatch.setattr(episodes.dao, "create", fail_after_insert)
        with pytest.raises(RuntimeError):
            episodes.create_for_project(project.id, {"title": "rollback"})
        assert not session.in_transaction()
        assert episodes.list().total == 0
        monkeypatch.setattr(episodes.dao, "create", original)
        assert episodes.create_for_project(project.id, {"title": "retry"}).position == 1


def test_request_boundaries_and_revalidation():
    assert ProjectCreateRequest(name=" x ", aspect="16:9").name == "x"
    ProjectCreateRequest(name="x" * 120, aspect="9:16", synopsis="s" * 2000, style="s" * 255)
    EpisodeCreateRequest(title="x" * 255, synopsis="s" * 500)
    with generation_session() as session:
        forged = ProjectCreateRequest.model_construct(name="", aspect="16:9")
        with pytest.raises(ValidationError):
            ProjectService(session).create_project(forged)
        assert session.scalar(select(func.count()).select_from(Project)) == 0


def test_http_creation_contract_validation_serialization_and_errors():
    now = datetime(2026, 9, 20, 1, 2, 3)
    calls = []

    class Projects:
        def create_project(self, payload):
            calls.append(payload)
            return ProjectRead(
                id=2**60, **payload.model_dump(), created_at=now, updated_at=now, last_opened_at=now
            )

    class Episodes:
        error = None

        def create_for_project(self, project_id, payload):
            if self.error:
                raise self.error("Operation unavailable")
            calls.append(payload)
            return EpisodeRead(
                id=2**60 + 1,
                project_id=project_id,
                position=1,
                title=payload.title,
                synopsis=payload.synopsis,
                aspect="9:16",
                style="ink",
                created_at=now,
                updated_at=now,
            )

    async def run():
        app = create_app(Settings(_env_file=None))
        episodes = Episodes()
        app.dependency_overrides[get_project_service] = Projects
        app.dependency_overrides[get_episode_service] = lambda: episodes
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            result = await client.post("/api/v1/projects", json={"name": " x ", "aspect": "9:16"})
            assert result.status_code == 201, result.text
            assert result.json()["id"] == str(2**60)
            assert result.json()["name"] == "x"
            assert result.json()["created_at"] == "2026-09-20T01:02:03Z"
            assert "target_ms" not in result.json()
            root = f"/api/v1/projects/{2**60}/episodes"
            result = await client.post(root, json={"title": "first"})
            assert result.status_code == 201, result.text
            assert result.json()["project_id"] == str(2**60)
            assert result.json()["id"] == str(2**60 + 1)
            assert result.json()["created_at"].endswith("Z")
            for field, value in (
                ("name", " "),
                ("name", "x" * 121),
                ("aspect", "1:1"),
                ("synopsis", "x" * 2001),
                ("style", "x" * 256),
                ("style", None),
                ("synopsis", None),
                ("target_ms", 60000),
                ("id", "1"),
                ("created_by", "1"),
                ("last_opened_at", "2026-09-20T00:00:00Z"),
            ):
                response = await client.post(
                    "/api/v1/projects", json={"name": "ok", "aspect": "9:16", field: value}
                )
                assert response.status_code == 422, response.text
            for field, value in (
                ("title", " "),
                ("title", "x" * 256),
                ("synopsis", "x" * 501),
                ("aspect", None),
                ("aspect", "1:1"),
                ("style", None),
                ("style", "x" * 256),
                ("project_id", "1"),
                ("position", 1),
                ("updated_at", "2026-01-01"),
            ):
                response = await client.post(root, json={"title": "ok", field: value})
                assert response.status_code == 422, response.text
                assert response.json()["error"]["code"] == "validation_error"
            for identifier in ("0", "-1", "demo-rain", str(2**64)):
                response = await client.post(
                    f"/api/v1/projects/{identifier}/episodes", json={"title": "ok"}
                )
                assert response.status_code == 422
            assert len(calls) == 2
            for error in (NotFound, Conflict, DatabaseUnavailable):
                episodes.error = error
                response = await client.post(root, json={"title": "ok"})
                assert response.status_code == error.status_code
                assert response.json()["error"]["code"] == error.code

    asyncio.run(run())
