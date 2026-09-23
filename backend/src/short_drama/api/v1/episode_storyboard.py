import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.schemas.base import Identifier
from short_drama.schemas.episode_storyboard import (
    StoryboardCreate,
    StoryboardList,
    StoryboardMove,
    StoryboardMutation,
    StoryboardOrder,
    StoryboardOrderResult,
    StoryboardUpdate,
)
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService

router = APIRouter(
    prefix="/projects/{project_id}/episodes/{episode_id}/shots",
    tags=["Episode storyboard"],
)
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]


def get_storyboard_service(request: Request, session: Session = Depends(get_session)):
    return EpisodeStoryboardService(
        session,
        settings=getattr(request.app.state, "settings", None),
        storage=getattr(request.app.state, "storage", None),
    )


Storyboard = Annotated[EpisodeStoryboardService, Depends(get_storyboard_service)]


@router.get("", response_model=StoryboardList)
def list_shots(
    project_id: ScopedId,
    episode_id: ScopedId,
    service: Storyboard,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    include_archived: bool = False,
):
    return service.list(
        project_id,
        episode_id,
        offset=offset,
        limit=limit,
        include_archived=include_archived,
    )


@router.post("", response_model=StoryboardMutation, status_code=201)
def create_shot(
    project_id: ScopedId,
    episode_id: ScopedId,
    payload: StoryboardCreate,
    service: Storyboard,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    result = service.create(project_id, episode_id, payload, idempotency_key)
    response.status_code = 201 if result.pop("created") else 200
    return result


@router.put("/order", response_model=StoryboardOrderResult)
def reorder_shots(
    project_id: ScopedId,
    episode_id: ScopedId,
    payload: StoryboardOrder,
    service: Storyboard,
):
    return service.reorder(project_id, episode_id, payload)


@router.get("/{shot_id}", response_model=StoryboardMutation)
def get_shot(project_id: ScopedId, episode_id: ScopedId, shot_id: ScopedId, service: Storyboard):
    return service.get(project_id, episode_id, shot_id)


@router.patch("/{shot_id}", response_model=StoryboardMutation)
def update_shot(
    project_id: ScopedId,
    episode_id: ScopedId,
    shot_id: ScopedId,
    payload: StoryboardUpdate,
    service: Storyboard,
):
    return service.update(project_id, episode_id, shot_id, payload)


@router.delete("/{shot_id}", status_code=204)
def archive_shot(
    project_id: ScopedId,
    episode_id: ScopedId,
    shot_id: ScopedId,
    service: Storyboard,
    if_match: Annotated[str, Header(alias="If-Match")],
):
    match = re.fullmatch(r'"([1-9][0-9]*)"', if_match)
    if match is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="If-Match must be a strong decimal ETag")
    service.archive(project_id, episode_id, shot_id, int(match.group(1)))


@router.post("/{shot_id}/move")
def move_shot(
    project_id: ScopedId,
    episode_id: ScopedId,
    shot_id: ScopedId,
    payload: StoryboardMove,
    service: Storyboard,
):
    return service.move(project_id, episode_id, shot_id, payload)
