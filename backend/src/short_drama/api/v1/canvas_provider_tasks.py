"""保持原版取回入口；供应商只接收原任务 GET 查询，不创建生成。"""

from fastapi import APIRouter, Request

from short_drama.ai import GenerationGateway
from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_provider_query import CanvasProviderTaskQueryRead
from short_drama.service.canvas_provider_query_service import CanvasProviderQueryService

router = APIRouter(prefix="/canvas-runtime", tags=["Canvas tasks"])


@router.post(
    "/tasks/{task_id}/query-provider",
    response_model=CanvasProviderTaskQueryRead,
    response_model_exclude_none=True,
)
def query_original_canvas_video(task_id: Identifier, request: Request, service: Canvases):
    settings = request.app.state.settings
    return CanvasProviderQueryService(
        service.session,
        request.app.state.session_factory,
        settings,
        GenerationGateway(settings),
        request.app.state.storage,
    ).query(task_id)
