"""本人深度动作捕捉准入；只接受已授权稳定媒体，不接受组件路径。"""

from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.canvas_depth import CanvasDepthCaptureCreate
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskRead
from short_drama.service.canvas_local_service import CanvasLocalService

router = APIRouter(prefix="/canvas-runtime", tags=["Canvas local media"])


@router.post(
    "/depth-captures", response_model=CanvasRuntimeTaskRead, response_model_exclude_none=True
)
def create_canvas_depth_capture(
    payload: CanvasDepthCaptureCreate,
    request: Request,
    response: Response,
    service: Canvases,
    key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
):
    result, fresh = CanvasLocalService(
        service.session, request.app.state.settings
    ).create_depth_capture(payload, key)
    response.status_code = 202 if fresh else 200
    return result
