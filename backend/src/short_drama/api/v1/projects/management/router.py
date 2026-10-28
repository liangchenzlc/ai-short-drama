from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status

from short_drama.api.dependencies.projects import get_project_service
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.schemas.projects.management.creation import (
    ProjectCreateRequest,
    ProjectPatchRequest,
)
from short_drama.schemas.projects.management.project import ProjectRead, ProjectSummary
from short_drama.service.projects.management.project import ProjectService

router = APIRouter(prefix="/projects", tags=["Creations"])
ProjectId = Annotated[Identifier, Path(description="Decimal Snowflake project identifier")]
Projects = Annotated[ProjectService, Depends(get_project_service)]


@router.get("", response_model=PageResponse[ProjectSummary])
def list_projects(
    service: Projects,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str, Query(max_length=120)] = "",
):
    return service.list_projects(offset, limit, q.strip())


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: ProjectId, service: Projects):
    return service.get(project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: ProjectId, payload: ProjectPatchRequest, service: Projects):
    return service.update_project(project_id, payload)


@router.post("/{project_id}/open", response_model=ProjectRead)
def open_project(project_id: ProjectId, service: Projects):
    return service.open(project_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: ProjectId,
    service: Projects,
    row_version: Annotated[
        Identifier, Query(description="Project version confirmed before deletion")
    ],
):
    service.delete_project(project_id, row_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateRequest,
    service: Annotated[ProjectService, Depends(get_project_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
):
    if payload.workspace_mode == "infinite_canvas":
        return service.create_project(payload, idempotency_key=idempotency_key)
    return service.create_project(payload)
