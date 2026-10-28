from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from short_drama.api.dependencies.creation import get_project_writing_service
from short_drama.schemas.base import Identifier
from short_drama.schemas.creation.writing.script.documents import (
    ScriptRevisionDetail,
    ScriptRevisionRead,
    ScriptSave,
    ScriptSaved,
    StorySettingsRead,
    StorySettingsSave,
    WritingRead,
)
from short_drama.service.creation.writing.script.documents import ProjectWritingService

router = APIRouter(prefix="/projects/{project_id}", tags=["Project writing"])
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]
Writing = Annotated[ProjectWritingService, Depends(get_project_writing_service)]


@router.get("/story-settings", response_model=StorySettingsRead)
def read_story_settings(project_id: ScopedId, service: Writing):
    return service.story_settings(project_id)


@router.put("/story-settings", response_model=StorySettingsRead)
def save_story_settings(project_id: ScopedId, payload: StorySettingsSave, service: Writing):
    return service.save_story_settings(project_id, payload)


@router.get("/script-revisions", response_model=dict[str, list[ScriptRevisionRead]])
def list_script_revisions(
    project_id: ScopedId,
    service: Writing,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.script_revisions(project_id, offset, limit)


@router.get("/script-revisions/{revision_id}", response_model=ScriptRevisionDetail)
def get_script_revision(project_id: ScopedId, revision_id: ScopedId, service: Writing):
    return service.script_revisions(project_id, revision_id=revision_id)


@router.get("/writing", response_model=WritingRead)
def read_writing(project_id: ScopedId, service: Writing):
    return service.get(project_id)


@router.put("/script", response_model=ScriptSaved)
def save_script(project_id: ScopedId, payload: ScriptSave, service: Writing):
    return service.save_script(project_id, payload)
