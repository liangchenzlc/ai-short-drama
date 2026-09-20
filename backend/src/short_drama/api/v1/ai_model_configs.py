from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from short_drama.api.dependencies import get_ai_config_service
from short_drama.schemas.ai_model_config import (
    AIModelConfigCreate,
    AIModelConfigDefault,
    AIModelConfigRead,
    AIModelConfigUpdate,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.schemas.model_discovery import ModelDiscoveryRead, ModelDiscoveryRequest
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.model_discovery_service import ModelDiscoveryService

router = APIRouter(prefix="/ai-model-configs", tags=["AI model configurations"])
ConfigService = Annotated[AIModelConfigService, Depends(get_ai_config_service)]
ConfigId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]


@router.post("/discover-models", response_model=ModelDiscoveryRead)
def discover_models(payload: ModelDiscoveryRequest, request: Request, service: ConfigService):
    return ModelDiscoveryService(request.app.state.settings, service).discover(payload)


@router.get("", response_model=PageResponse[AIModelConfigRead])
def list_configs(
    service: ConfigService,
    service_type: Annotated[Literal["text", "image", "video"] | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    filters = {"service_type": service_type} if service_type is not None else None
    return service.list(offset=offset, limit=limit, filters=filters)


@router.post("", response_model=AIModelConfigRead, status_code=status.HTTP_201_CREATED)
def create_config(payload: AIModelConfigCreate, service: ConfigService):
    return service.create(payload)


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
