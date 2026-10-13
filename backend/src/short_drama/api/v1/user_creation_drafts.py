from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from short_drama.api.dependencies import get_user_creation_draft_service
from short_drama.schemas.base import Identifier
from short_drama.schemas.user_creation_draft import (
    DraftPurpose,
    UserCreationDraftCreate,
    UserCreationDraftRead,
    UserCreationDraftUpdate,
)
from short_drama.service.user_creation_draft_service import UserCreationDraftService

router = APIRouter(
    tags=["User creation drafts"], prefix="/projects/{project_id}/episodes/{episode_id}"
)
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]
Draft = Annotated[UserCreationDraftService, Depends(get_user_creation_draft_service)]


@router.get("/drafts", response_model=list[UserCreationDraftRead])
def list_user_drafts(project_id: ScopedId, episode_id: ScopedId, service: Draft):
    return service.list_for_episode(project_id, episode_id)


@router.get("/drafts/{object_id}/{purpose}", response_model=UserCreationDraftRead | None)
def get_user_draft(
    project_id: ScopedId,
    episode_id: ScopedId,
    object_id: ScopedId,
    purpose: DraftPurpose,
    service: Draft,
):
    return service.get_for_scope(project_id, episode_id, object_id, purpose)


@router.post("/drafts", response_model=UserCreationDraftRead, status_code=status.HTTP_201_CREATED)
def create_user_draft(
    project_id: ScopedId,
    episode_id: ScopedId,
    payload: UserCreationDraftCreate,
    service: Draft,
):
    return service.create_for_scope(project_id, episode_id, payload)


@router.patch("/drafts/{object_id}/{purpose}", response_model=UserCreationDraftRead)
def update_user_draft(
    project_id: ScopedId,
    episode_id: ScopedId,
    object_id: ScopedId,
    purpose: DraftPurpose,
    payload: UserCreationDraftUpdate,
    service: Draft,
):
    return service.update_for_scope(project_id, episode_id, object_id, purpose, payload)


@router.delete("/drafts/{object_id}/{purpose}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_draft(
    project_id: ScopedId,
    episode_id: ScopedId,
    object_id: ScopedId,
    purpose: DraftPurpose,
    service: Draft,
):
    service.delete_for_scope(project_id, episode_id, object_id, purpose)
