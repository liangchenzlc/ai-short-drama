from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.schemas.base import Identifier
from short_drama.schemas.generation_batch import BatchCreate, BatchPreflight, BatchRetry
from short_drama.service.generation_batch_service import GenerationBatchService

router = APIRouter(prefix="/ai/generation-batches", tags=["generation-batches"])


def service(request: Request, session: Session = Depends(get_session)):
    return GenerationBatchService(session, request.app.state.settings)


Service = Annotated[GenerationBatchService, Depends(service)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/capabilities")
def capabilities(request: Request):
    settings = request.app.state.settings
    return {
        "enabled": settings.generation_batches_enabled,
        "max_items": 100,
        "image_concurrency": settings.generation_batch_image_concurrency,
        "video_concurrency": settings.generation_batch_video_concurrency,
    }


@router.post("/preflight")
def preflight(body: BatchPreflight, svc: Service):
    return svc.preflight(body)


@router.post("")
def create(body: BatchCreate, svc: Service, idempotency_key: Key, response: Response):
    result, created = svc.create(body, idempotency_key)
    response.status_code = 202 if created else 200
    return result


@router.get("")
def history(svc: Service, offset: Offset = 0, limit: Limit = 20):
    return svc.list(offset, limit)


@router.get("/{batch_id}")
@router.get("/{batch_id}/items")
def detail(batch_id: Identifier, svc: Service, offset: Offset = 0, limit: Limit = 20):
    return svc.detail(batch_id, offset, limit)


@router.post("/{batch_id}/retry-failed", status_code=202)
def retry(batch_id: Identifier, body: BatchRetry, svc: Service, idempotency_key: Key):
    return svc.retry_failed(batch_id, body, idempotency_key)


@router.post("/{batch_id}/{action}")
def control(batch_id: Identifier, action: Literal["pause", "resume", "cancel"], svc: Service):
    return svc.control(batch_id, action)
