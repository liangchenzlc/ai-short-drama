"""Canvas parent queries and scope checks shared with ORM write guards."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Project, ProjectCanvas


def canvas_project(session: Session, project_id: int, *, lock: bool = False) -> Project | None:
    query = select(Project).where(Project.id == project_id, Project.archived_at.is_(None))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def canvas_record(
    session: Session,
    project_id: int,
    canvas_id: int,
    *,
    lock: bool = False,
    refresh: bool = False,
) -> ProjectCanvas | None:
    query = select(ProjectCanvas).where(
        ProjectCanvas.project_id == project_id,
        ProjectCanvas.id == canvas_id,
        ProjectCanvas.archived_at.is_(None),
    )
    if refresh or lock:
        query = query.execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def require_canvas_project(session: Session, project_id: int, *, lock: bool = False) -> Project:
    project = canvas_project(session, project_id, lock=lock)
    if project is None:
        raise NotFound("Project does not exist")
    if project.workspace_mode != "infinite_canvas":
        raise WorkflowError("canvas_mode_required", "This project uses standard mode", 409)
    return project


def require_canvas(
    session: Session,
    project_id: int,
    canvas_id: int,
    *,
    lock: bool = False,
    refresh: bool = False,
) -> ProjectCanvas:
    require_canvas_project(session, project_id, lock=lock)
    canvas = canvas_record(session, project_id, canvas_id, lock=lock, refresh=refresh)
    if canvas is None:
        raise NotFound("Canvas does not exist")
    return canvas
