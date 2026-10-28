from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.schemas.base import Identifier
from short_drama.schemas.creation.assets.extraction.extraction import (
    ExtractionApply,
    ExtractionPatch,
)
from short_drama.schemas.creation.storyboard.management.adoption import (
    StoryboardApplied,
    StoryboardApply,
)
from short_drama.schemas.creation.storyboard.management.storyboard import StoryboardResultPage
from short_drama.service.creation.assets.extraction.extraction import AssetExtractionService
from short_drama.service.generations.admission.business import GenerationBusinessService

router = APIRouter(prefix="/projects/{project_id}", tags=["Project generation"])


@router.post("/storyboard-results/{generation_id}/apply", response_model=StoryboardApplied)
def apply_storyboard(
    project_id: Identifier,
    generation_id: Identifier,
    body: StoryboardApply,
    session: Annotated[Session, Depends(get_session)],
):
    return GenerationBusinessService(session).apply_storyboard(project_id, generation_id, body)


@router.get("/asset-extraction-results/{generation_id}")
def extraction_result(
    project_id: Identifier,
    generation_id: Identifier,
    session: Annotated[Session, Depends(get_session)],
):
    return AssetExtractionService(session).get(project_id, generation_id)


@router.patch("/asset-extraction-results/{generation_id}")
def edit_extraction_result(
    project_id: Identifier,
    generation_id: Identifier,
    body: ExtractionPatch,
    session: Annotated[Session, Depends(get_session)],
):
    return AssetExtractionService(session).patch(project_id, generation_id, body)


@router.post("/asset-extraction-results/{generation_id}/apply")
def apply_extraction_result(
    project_id: Identifier,
    generation_id: Identifier,
    body: ExtractionApply,
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return AssetExtractionService(session).apply(project_id, generation_id, body, idempotency_key)


@router.get("/storyboard-results/{generation_id}/shots", response_model=StoryboardResultPage)
def storyboard_result_page(
    project_id: Identifier,
    generation_id: Identifier,
    session: Annotated[Session, Depends(get_session)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return GenerationBusinessService(session).storyboard_result_page(
        project_id, generation_id, offset, limit
    )
