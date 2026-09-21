from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.schemas.base import Identifier
from short_drama.schemas.storyboard_apply import StoryboardApplied, StoryboardApply
from short_drama.service.generation_business_service import GenerationBusinessService

router = APIRouter(
    prefix="/projects/{project_id}/episodes/{episode_id}", tags=["Episode generation"]
)


@router.post("/storyboard-results/{generation_id}/apply", response_model=StoryboardApplied)
def apply_storyboard(
    project_id: Identifier,
    episode_id: Identifier,
    generation_id: Identifier,
    body: StoryboardApply,
    session: Annotated[Session, Depends(get_session)],
):
    return GenerationBusinessService(session).apply_storyboard(
        project_id, episode_id, generation_id, body
    )
