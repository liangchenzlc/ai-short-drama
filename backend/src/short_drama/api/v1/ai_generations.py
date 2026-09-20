from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response

from short_drama.api.dependencies import get_ai_generation_service
from short_drama.schemas.ai_generation import (
    GenerationRetry,
    ImageGenerationCreate,
    TextGenerationCreate,
    VideoGenerationCreate,
)
from short_drama.schemas.base import Identifier

router = APIRouter(prefix="/ai/generations", tags=["ai-generations"])
Service = Annotated[object, Depends(get_ai_generation_service)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _created(service, kind, body, key, response):
    result, created = service.create(kind, body, key)
    response.status_code = 202 if created else 200
    return result


@router.post("/text", status_code=202)
def create_text(
    body: TextGenerationCreate, response: Response, service: Service, idempotency_key: Key
):
    return _created(service, "text", body, idempotency_key, response)


@router.post("/image", status_code=202)
def create_image(
    body: ImageGenerationCreate, response: Response, service: Service, idempotency_key: Key
):
    return _created(service, "image", body, idempotency_key, response)


@router.post("/video", status_code=202)
def create_video(
    body: VideoGenerationCreate, response: Response, service: Service, idempotency_key: Key
):
    return _created(service, "video", body, idempotency_key, response)


@router.get("")
def list_generations(
    service: Service,
    service_type: Literal["text", "image", "video"] | None = None,
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = None,
    config_id: Identifier | None = None,
    source_scene: Literal["shot_image"] | None = None,
    source_id: Identifier | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list(
        offset,
        limit,
        {
            "service_type": service_type,
            "status": status,
            "config_id": config_id,
            "source_scene": source_scene,
            "source_id": source_id,
            "created_after": created_after,
            "created_before": created_before,
        },
    )


@router.get("/{generation_id}")
def detail(generation_id: Identifier, service: Service):
    return service.detail(generation_id)


@router.get("/{generation_id}/records")
def records(generation_id: Identifier, service: Service):
    return service.records(generation_id)


@router.post("/{generation_id}/cancel")
def cancel(generation_id: Identifier, service: Service):
    return service.cancel(generation_id)


@router.post("/{generation_id}/resume")
def resume(generation_id: Identifier, service: Service):
    return service.resume(generation_id)


@router.post("/{generation_id}/retry", status_code=202)
def retry(
    generation_id: Identifier,
    response: Response,
    service: Service,
    idempotency_key: Key,
    body: GenerationRetry | None = None,
):
    result, created = service.retry(generation_id, body, idempotency_key)
    response.status_code = 202 if created else 200
    return result
