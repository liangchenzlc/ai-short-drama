import asyncio
from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from short_drama.api.dependencies import get_project_service
from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Project, ProjectNovel, ProjectScript
from short_drama.main import create_app
from short_drama.schemas.project import ProjectRead
from short_drama.schemas.project_creation import ProjectCreateRequest, ProjectPatchRequest
from short_drama.service.project_service import ProjectService
from tests.support.generation_fixtures import generation_session


def test_creation_is_one_work_without_child_episode_or_visual_defaults():
    with generation_session() as session:
        service = ProjectService(session)
        created = service.create_project({"name": "  story  "})
        assert created.name == "story" and created.workspace_mode == "standard"
        assert created.content_version == created.storyboard_version == 1
        assert created.editing_script_id is None
        assert created.last_opened_at == created.created_at == created.updated_at
        assert service.get(created.id).id == created.id
        assert service.list_projects().total == 1
        with session.begin():
            for model in (ProjectNovel, ProjectScript):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        assert (
            not {"aspect", "style", "synopsis", "episode_count"} & type(created).model_fields.keys()
        )


def test_failed_insert_rolls_back_the_work(monkeypatch):
    with generation_session() as session:
        service = ProjectService(session)
        original = service.dao.create

        def fail_after_insert(values):
            original(values)
            raise RuntimeError("injected failure after flush")

        monkeypatch.setattr(service.dao, "create", fail_after_insert)
        with pytest.raises(RuntimeError, match="injected"):
            service.create_project({"name": "story"})
        assert not session.in_transaction()
        assert service.list_projects().total == 0
        monkeypatch.setattr(service.dao, "create", original)
        assert service.create_project({"name": "retry"}).name == "retry"


def test_name_and_version_are_the_only_patch_inputs():
    assert ProjectCreateRequest(name=" x ").name == "x"
    for field in ("aspect", "style", "synopsis", "owner_user_id", "last_opened_at"):
        with pytest.raises(ValidationError):
            ProjectCreateRequest.model_validate({"name": "ok", field: "old"})
        with pytest.raises(ValidationError):
            ProjectPatchRequest.model_validate({"name": "ok", "row_version": "1", field: "old"})
    with generation_session() as session:
        service = ProjectService(session)
        forged = ProjectCreateRequest.model_construct(name="")
        with pytest.raises(ValidationError):
            service.create_project(forged)
        assert session.scalar(select(func.count()).select_from(Project)) == 0
        session.rollback()
        created = service.create_project({"name": "first"})
        updated = service.update_project(created.id, {"name": "second", "row_version": "1"})
        assert updated.name == "second" and updated.row_version == 2
        with pytest.raises(WorkflowError) as conflict:
            service.update_project(created.id, {"name": "stale", "row_version": "1"})
        assert conflict.value.code == "project_version_conflict"
        assert service.get(created.id).name == "second"


def test_http_contract_closes_episode_and_archive_routes():
    now = datetime(2026, 9, 20, 1, 2, 3)

    class Projects:
        def create_project(self, payload):
            return ProjectRead(id=2**60, **payload.model_dump(), created_at=now)

        def update_project(self, identifier, payload):
            raise WorkflowError("project_version_conflict", "stale", 409)

        def delete_project(self, identifier, row_version):
            assert identifier == 2**60 and row_version == 1

    async def run():
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_project_service] = Projects
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/projects", json={"name": " x "})
            assert response.status_code == 201, response.text
            assert response.json()["id"] == str(2**60)
            assert response.json()["created_at"] == "2026-09-20T01:02:03Z"
            for payload in ({"name": " "}, {"name": "x" * 121}, {"name": "ok", "aspect": "16:9"}):
                assert (await client.post("/api/v1/projects", json=payload)).status_code == 422
            root = f"/api/v1/projects/{2**60}"
            assert (await client.post(root + "/episodes", json={"title": "old"})).status_code == 404
            assert (await client.delete(root)).status_code == 422
            deleted = await client.delete(root, params={"row_version": "1"})
            assert deleted.status_code == 204 and deleted.content == b""
            for version in ("0", "true", "1.0", str(2**64)):
                assert (
                    await client.delete(root, params={"row_version": version})
                ).status_code == 422
            conflict = await client.patch(root, json={"name": "new", "row_version": "1"})
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "project_version_conflict"

    asyncio.run(run())


def test_project_deletion_fences_old_version_and_hides_owned_content():
    from short_drama.core.exceptions import NotFound
    from short_drama.core.identity import ActorContext
    from short_drama.service.project_writing_service import ProjectWritingService

    with generation_session() as session:
        session.info["actor"] = ActorContext(1, "owner", "owner@example.test", True, 1, "", "test")
        service = ProjectService(session)
        created = service.create_project({"name": "original"})
        ProjectWritingService(session).save_novel(
            created.id, {"content": "preserved novel", "content_version": "1"}
        )
        renamed = service.update_project(created.id, {"name": "renamed", "row_version": "1"})
        with pytest.raises(WorkflowError) as stale:
            service.delete_project(created.id, "1")
        assert stale.value.code == "project_version_conflict"
        assert service.get(created.id).name == "renamed"
        session.info["actor"] = ActorContext(2, "other", "other@example.test", True, 2, "", "test")
        with pytest.raises(NotFound):
            service.delete_project(created.id, str(renamed.row_version))
        session.info["actor"] = ActorContext(1, "owner", "owner@example.test", True, 1, "", "test")
        service.delete_project(created.id, str(renamed.row_version))
        assert service.list_projects().total == 0
        with pytest.raises(NotFound):
            service.get(created.id)
        with pytest.raises(NotFound):
            service.open(created.id)
        with pytest.raises(NotFound):
            service.delete_project(created.id, str(renamed.row_version))
        session.info.pop("actor")
        with session.begin():
            stored = session.get(Project, created.id)
            assert stored.archived_at is not None and stored.row_version == 3
            assert session.scalar(select(ProjectNovel)).content == "preserved novel"


def test_deleting_canvas_project_invalidates_all_prior_recycle_receipts():
    from short_drama.core.identity import ActorContext
    from short_drama.domain import ProjectCanvas
    from short_drama.service.canvas_recycle_service import CanvasRecycleService
    from short_drama.service.canvas_service import CanvasService

    with generation_session() as session:
        session.info["actor"] = ActorContext(1, "owner", "owner@example.test", True, 1, "", "test")
        service = ProjectService(session)
        project = service.create_project(
            {"name": "canvas", "workspace_mode": "infinite_canvas"}, idempotency_key="create-canvas"
        )
        with session.begin():
            first = session.scalar(select(ProjectCanvas))
            first_id, source_key = first.id, first.source_key
        canvases = CanvasService(session)
        with session.begin():
            session.add(
                ProjectCanvas(
                    **canvases.audit(),
                    project_id=project.id,
                    source_key="second-canvas",
                    title="second",
                    position=1,
                    schema_version=1,
                    row_version=1,
                    properties_json={},
                )
            )
        canvases.archive(project.id, first_id, 1, "archive-first")
        recycle = CanvasRecycleService(session)
        assert len(recycle.list_archived()["items"]) == 1
        service.delete_project(project.id, "1")
        assert recycle.list_archived()["items"] == []
        with pytest.raises(WorkflowError) as stale:
            recycle.restore_archived(source_key, "archive-first", "restore-after-delete")
        assert stale.value.code == "canvas_revision_conflict"
        assert service.list_projects().total == 0
        session.info.pop("actor")
        with session.begin():
            rows = list(session.scalars(select(ProjectCanvas)))
            assert len(rows) == 2 and all(row.archived_at is not None for row in rows)
