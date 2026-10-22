"""原版协议和上游模型目录入口；身份与渠道秘密由宿主服务处理。"""

from fastapi import APIRouter, Request

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.canvas_catalog import (
    CanvasChannelModelsRead,
    CanvasChannelModelsRequest,
    CanvasPluginCatalogRead,
)
from short_drama.schemas.canvas_runninghub import RunningHubSchemaRequest, RunningHubWorkflow
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_model_discovery_service import CanvasModelDiscoveryService
from short_drama.service.canvas_plugin_catalog import provider_catalog
from short_drama.service.canvas_runninghub_schema_service import CanvasRunningHubSchemaService

router = APIRouter(prefix="/canvas-runtime", tags=["Canvas model catalog"])


@router.get("/plugins/catalog", response_model=CanvasPluginCatalogRead)
def read_canvas_plugin_catalog(
    service: Canvases, scope: str = "user.custom-channel", capability: str | None = None
):
    catalog = CanvasModelCatalogService(service.session)
    _ = catalog.actor_id
    return {"providers": provider_catalog(scope, capability)}


@router.post("/ai/models", response_model=CanvasChannelModelsRead)
def read_canvas_channel_models(
    payload: CanvasChannelModelsRequest, request: Request, service: Canvases
):
    settings = request.app.state.settings
    return CanvasModelDiscoveryService(
        settings,
        CanvasModelCatalogService(service.session, settings=settings),
    ).discover(payload)


@router.post("/ai/models/{model_id}/runninghub-schema", response_model=RunningHubWorkflow)
def read_runninghub_schema(
    model_id: str, payload: RunningHubSchemaRequest, request: Request, service: Canvases
):
    return CanvasRunningHubSchemaService(service.session, request.app.state.settings).read(
        model_id, payload
    )
