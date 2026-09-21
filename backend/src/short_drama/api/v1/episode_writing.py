from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from short_drama.api.dependencies import get_episode_writing_service
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.schemas.episode_writing import (
    NovelSave,
    NovelSaved,
    ScriptCandidate,
    ScriptCandidateDetail,
    ScriptSave,
    ScriptSaved,
    ScriptSelect,
    WritingRead,
    WritingVersion,
)
from short_drama.service.episode_writing_service import EpisodeWritingService

router = APIRouter(prefix="/projects/{project_id}/episodes/{episode_id}", tags=["Episode writing"])
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]
Writing = Annotated[EpisodeWritingService, Depends(get_episode_writing_service)]


@router.get("/scripts", response_model=PageResponse[ScriptCandidate])
def list_scripts(
    project_id: ScopedId,
    episode_id: ScopedId,
    service: Writing,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.candidates(project_id, episode_id, offset, limit)


@router.get("/scripts/{script_id}", response_model=ScriptCandidateDetail)
def get_script(project_id: ScopedId, episode_id: ScopedId, script_id: ScopedId, service: Writing):
    return service.candidates(project_id, episode_id, script_id=script_id)


@router.get("/writing", response_model=WritingRead)
def read_writing(project_id: ScopedId, episode_id: ScopedId, service: Writing):
    return service.get(project_id, episode_id)


@router.put("/novel", response_model=NovelSaved)
def save_novel(project_id: ScopedId, episode_id: ScopedId, payload: NovelSave, service: Writing):
    return service.save_novel(project_id, episode_id, payload)


@router.put("/script", response_model=ScriptSaved)
def save_script(project_id: ScopedId, episode_id: ScopedId, payload: ScriptSave, service: Writing):
    return service.save_script(project_id, episode_id, payload)


@router.put("/editing-script", response_model=WritingRead)
def select_script(
    project_id: ScopedId, episode_id: ScopedId, payload: ScriptSelect, service: Writing
):
    return service.select_script(project_id, episode_id, payload)


@router.post("/scripts/{script_id}/confirm", response_model=WritingRead)
def confirm_script(
    project_id: ScopedId,
    episode_id: ScopedId,
    script_id: ScopedId,
    payload: WritingVersion,
    service: Writing,
):
    return service.confirm(project_id, episode_id, script_id, payload)
