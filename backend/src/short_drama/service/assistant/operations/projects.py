"""Project directory adapters reuse the current business services inside one scoped transaction."""

from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.assistant.operations.projects import (
    ListProjects,
    ReadProject,
    ReadProjectCollection,
)
from short_drama.service.assistant.operations.invocation import BusinessInvocation
from short_drama.service.creation.assets.management.library import AssetLibraryService
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.service.projects.management.project import ProjectService


def list_projects_locked(
    session: Session,
    identity: BusinessInvocation,
    values: ListProjects,
    receipt_id: int | None,
    settings: Settings | None,
) -> dict[str, Any]:
    return (
        ProjectService(session)
        .list_projects_locked(values.offset, values.limit, values.query)
        .model_dump(mode="json")
    )


def read_project_locked(
    session: Session,
    identity: BusinessInvocation,
    values: ReadProject,
    receipt_id: int | None,
    settings: Settings | None,
) -> dict[str, Any]:
    service = ProjectService(session)
    return service._read(
        service._require(service.model, values.project_id, for_update=False)
    ).model_dump(mode="json")


def _standard_project(session: Session, project_id: int) -> None:
    service = ProjectService(session)
    project = service._require(service.model, project_id, for_update=False)
    if project.workspace_mode != "standard":
        raise WorkflowError("business_project_mode", "此工具只读取标准作品", 403)


def read_assets_locked(
    session: Session,
    identity: BusinessInvocation,
    values: ReadProjectCollection,
    receipt_id: int | None,
    settings: Settings | None,
) -> dict[str, Any]:
    _standard_project(session, values.project_id)
    result = AssetLibraryService(session, settings).list_locked(
        "project", values.project_id, offset=values.offset, limit=values.limit
    )
    return TypeAdapter(dict).dump_python(result, mode="json")


def read_storyboard_locked(
    session: Session,
    identity: BusinessInvocation,
    values: ReadProjectCollection,
    receipt_id: int | None,
    settings: Settings | None,
) -> dict[str, Any]:
    _standard_project(session, values.project_id)
    return ProjectStoryboardService(session, settings).list_locked(
        values.project_id, offset=values.offset, limit=values.limit
    )
