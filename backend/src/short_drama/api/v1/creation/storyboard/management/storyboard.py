from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.schemas.base import Identifier
from short_drama.schemas.creation.storyboard.management.storyboard import (
    StoryboardCreate,
    StoryboardDelete,
    StoryboardDeleteResult,
    StoryboardList,
    StoryboardMove,
    StoryboardMutation,
    StoryboardOrder,
    StoryboardOrderResult,
    StoryboardUpdate,
)
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService

router = APIRouter(
    prefix="/projects/{project_id}/shots",
    tags=["Project storyboard"],
)
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]


def get_storyboard_service(request: Request, session: Session = Depends(get_session)):
    return ProjectStoryboardService(
        session,
        settings=getattr(request.app.state, "settings", None),
        storage=getattr(request.app.state, "storage", None),
    )


Storyboard = Annotated[ProjectStoryboardService, Depends(get_storyboard_service)]


@router.get("", response_model=StoryboardList)
def list_shots(
    project_id: ScopedId,
    service: Storyboard,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    return service.list(
        project_id,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=StoryboardMutation, status_code=201)
def create_shot(
    project_id: ScopedId,
    payload: StoryboardCreate,
    service: Storyboard,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    result = service.create(project_id, payload, idempotency_key)
    response.status_code = 201 if result.pop("created") else 200
    return result


@router.put("/order", response_model=StoryboardOrderResult)
def reorder_shots(
    project_id: ScopedId,
    payload: StoryboardOrder,
    service: Storyboard,
):
    return service.reorder(project_id, payload)


@router.get("/{shot_id}", response_model=StoryboardMutation)
def get_shot(project_id: ScopedId, shot_id: ScopedId, service: Storyboard):
    return service.get(project_id, shot_id)


@router.patch("/{shot_id}", response_model=StoryboardMutation)
def update_shot(
    project_id: ScopedId,
    shot_id: ScopedId,
    payload: StoryboardUpdate,
    service: Storyboard,
):
    return service.update(project_id, shot_id, payload)


@router.post("/{shot_id}/move")
def move_shot(
    project_id: ScopedId,
    shot_id: ScopedId,
    payload: StoryboardMove,
    service: Storyboard,
):
    return service.move(project_id, shot_id, payload)


@router.delete("/{shot_id}", response_model=StoryboardDeleteResult)
def delete_shot(
    project_id: ScopedId,
    shot_id: ScopedId,
    payload: StoryboardDelete,
    service: Storyboard,
):
    return service.delete(project_id, shot_id, payload)
