from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status

from short_drama.api.dependencies.ai_config import get_ai_config_service
from short_drama.schemas.ai_config.discovery.catalog import (
    ModelDiscoveryRead,
    ModelDiscoveryRequest,
)
from short_drama.schemas.ai_config.management.models import (
    AIModelConfigCreate,
    AIModelConfigDefault,
    AIModelConfigOptions,
    AIModelConfigRead,
    AIModelConfigUpdate,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.service.ai_config.discovery.catalog import ModelDiscoveryService
from short_drama.service.ai_config.management.models import AIModelConfigService

router = APIRouter(prefix="/ai-model-configs", tags=["AI model configurations"])
ConfigService = Annotated[AIModelConfigService, Depends(get_ai_config_service)]
ConfigId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]


@router.get("/options", response_model=AIModelConfigOptions)
def model_options(service: ConfigService, service_type: Literal["text", "image", "video", "audio"]):
    return service.options(service_type)


@router.post("/discover-models", response_model=ModelDiscoveryRead)
def discover_models(payload: ModelDiscoveryRequest, request: Request, service: ConfigService):
    return ModelDiscoveryService(request.app.state.settings, service).discover(payload)


@router.get("", response_model=PageResponse[AIModelConfigRead])
def list_configs(
    service: ConfigService,
    service_type: Annotated[Literal["text", "image", "video", "audio"] | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    create_key: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
):
    filters = {}
    if service_type is not None:
        filters["service_type"] = service_type
    if create_key is not None:
        filters["create_key"] = create_key
    return service.list(offset=offset, limit=limit, filters=filters)


@router.post("", response_model=AIModelConfigRead, status_code=status.HTTP_201_CREATED)
def create_config(
    payload: AIModelConfigCreate,
    service: ConfigService,
    response: Response,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")
    ],
):
    result, fresh = service.create_once(payload, idempotency_key)
    response.status_code = 201 if fresh else 200
    return result


@router.get("/{config_id}", response_model=AIModelConfigRead)
def get_config(config_id: ConfigId, service: ConfigService):
    return service.get(config_id)


@router.get("/{config_id}/capabilities")
def get_capabilities(config_id: ConfigId, service: ConfigService):
    return service.capabilities(config_id)


@router.patch("/{config_id}", response_model=AIModelConfigRead)
def update_config(config_id: ConfigId, payload: AIModelConfigUpdate, service: ConfigService):
    return service.update(config_id, payload)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(
    config_id: ConfigId,
    service: ConfigService,
    row_version: Annotated[Identifier, Query(description="Last observed configuration version")],
):
    service.delete(config_id, row_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{config_id}/default", response_model=AIModelConfigRead)
def set_default(config_id: ConfigId, payload: AIModelConfigDefault, service: ConfigService):
    return service.set_default(config_id, payload.row_version)
