"""Owner-only canvas task prelocking keeps stable order without source projects."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.domain import Project, ProjectCanvas
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.canvas import CanvasService
from tests.support.generation_fixtures import generation_session


def seed(session):
    now = utcnow()
    with session.begin():
        for identifier in (10, 30):
            session.add(
                Project(
                    id=identifier,
                    owner_user_id=1,
                    name="Canvas",
                    workspace_mode="infinite_canvas",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for identifier, project in ((40, 30), (20, 10)):
            session.add(
                ProjectCanvas(
                    id=identifier,
                    project_id=project,
                    source_key=f"canvas-{identifier}",
                    title="Canvas",
                    position=0,
                    properties_json={},
                    created_at=now,
                    updated_at=now,
                    created_by=1,
                    updated_by=1,
                )
            )
    session.info["actor"] = SimpleNamespace(user_id=1)


def test_existing_canvas_batch_locks_projects_and_canvases_in_stable_order(monkeypatch):
    with generation_session() as session:
        seed(session)
        service = CanvasService(session)
        calls = []
        project = service.require_project
        canvas = service.canvas_dao.canvas

        def require(identifier, *, lock=False):
            calls.append(("project", identifier, lock))
            return project(identifier, lock=lock)

        def require_canvas(project_id, identifier, **options):
            calls.append(("canvas", identifier, options["lock"]))
            return canvas(project_id, identifier, **options)

        monkeypatch.setattr(service, "require_project", require)
        monkeypatch.setattr(service.canvas_dao, "canvas", require_canvas)
        with session.begin():
            originals = list(
                session.scalars(select(ProjectCanvas).order_by(ProjectCanvas.id.desc()))
            )
            service.lock_existing_canvases(originals)
        assert calls == [
            ("project", 10, True),
            ("project", 30, True),
            ("canvas", 20, True),
            ("canvas", 40, True),
        ]


def test_foreign_canvas_batch_cannot_materialize_cached_work():
    with generation_session() as session:
        seed(session)
        with session.begin():
            originals = list(session.scalars(select(ProjectCanvas)))
        session.info["actor"] = SimpleNamespace(user_id=2)
        with pytest.raises(NotFound), session.begin():
            CanvasService(session).lock_existing_canvases(originals)
