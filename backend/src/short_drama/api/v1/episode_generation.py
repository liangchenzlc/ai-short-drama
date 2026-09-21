from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.schemas.asset_extraction import ExtractionApply, ExtractionPatch
from short_drama.schemas.base import Identifier
from short_drama.schemas.storyboard_apply import StoryboardApplied, StoryboardApply
from short_drama.service.asset_extraction_service import AssetExtractionService
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


@router.get("/asset-extraction-results/{generation_id}")
def extraction_result(
    project_id: Identifier,
    episode_id: Identifier,
    generation_id: Identifier,
    session: Annotated[Session, Depends(get_session)],
):
    return AssetExtractionService(session).get(project_id, episode_id, generation_id)


@router.patch("/asset-extraction-results/{generation_id}")
def edit_extraction_result(
    project_id: Identifier,
    episode_id: Identifier,
    generation_id: Identifier,
    body: ExtractionPatch,
    session: Annotated[Session, Depends(get_session)],
):
    return AssetExtractionService(session).patch(project_id, episode_id, generation_id, body)


@router.post("/asset-extraction-results/{generation_id}/apply")
def apply_extraction_result(
    project_id: Identifier,
    episode_id: Identifier,
    generation_id: Identifier,
    body: ExtractionApply,
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return AssetExtractionService(session).apply(
        project_id, episode_id, generation_id, body, idempotency_key
    )
