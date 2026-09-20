from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from short_drama.api.dependencies import get_episode_service, get_project_service
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.schemas.episode import EpisodeDetail, EpisodeRead
from short_drama.schemas.project import ProjectRead, ProjectSummary
from short_drama.schemas.project_creation import (
    EpisodeCreateRequest,
    EpisodePatchRequest,
    ProjectCreateRequest,
    ProjectPatchRequest,
)
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["Projects and episodes"])
ProjectId = Annotated[Identifier, Path(description="Decimal Snowflake project identifier")]
EpisodeId = Annotated[Identifier, Path(description="Decimal Snowflake episode identifier")]
Projects = Annotated[ProjectService, Depends(get_project_service)]
Episodes = Annotated[EpisodeService, Depends(get_episode_service)]


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
def delete_project(project_id: ProjectId, service: Projects):
    service.delete(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/episodes", response_model=PageResponse[EpisodeRead])
def list_episodes(
    project_id: ProjectId,
    service: Episodes,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list_for_project(project_id, offset, limit)


@router.get("/{project_id}/episodes/{episode_id}", response_model=EpisodeDetail)
def get_episode(project_id: ProjectId, episode_id: EpisodeId, service: Episodes):
    return service.get_for_project(project_id, episode_id)


@router.patch("/{project_id}/episodes/{episode_id}", response_model=EpisodeRead)
def update_episode(
    project_id: ProjectId,
    episode_id: EpisodeId,
    payload: EpisodePatchRequest,
    service: Episodes,
):
    return service.update_for_project(project_id, episode_id, payload)


@router.delete("/{project_id}/episodes/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_episode(project_id: ProjectId, episode_id: EpisodeId, service: Episodes):
    service.delete_for_project(project_id, episode_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateRequest,
    service: Annotated[ProjectService, Depends(get_project_service)],
):
    return service.create_project(payload)


@router.post(
    "/{project_id}/episodes", response_model=EpisodeRead, status_code=status.HTTP_201_CREATED
)
def create_episode(
    project_id: Annotated[Identifier, Path(description="Decimal Snowflake project identifier")],
    payload: EpisodeCreateRequest,
    service: Annotated[EpisodeService, Depends(get_episode_service)],
):
    return service.create_for_project(project_id, payload)
