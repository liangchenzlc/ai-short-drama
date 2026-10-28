"""画布时间线的编译、渲染与字幕转写入口；原字段保留 camelCase。"""

from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from short_drama.api.v1.canvas.document.router import Canvases
from short_drama.core.identity import require_actor
from short_drama.schemas.canvas.generation.tasks.task_runtime import CanvasRuntimeTaskRead
from short_drama.schemas.canvas.timeline.timeline import (
    CanvasTimelinePlanRead,
    CanvasTimelinePlanRequest,
    CanvasTimelineRenderRequest,
)
from short_drama.schemas.canvas.tools.transcription.transcription import (
    CanvasTranscriptionCreate,
    CanvasTranscriptionModelsRead,
)
from short_drama.service.canvas.timeline.rendering.render import CanvasRenderService
from short_drama.service.canvas.timeline.timeline_compile import compile_timeline_plan
from short_drama.service.canvas.tools.local.local import CanvasLocalService

router = APIRouter(prefix="/canvas-runtime/timeline", tags=["Canvas timeline"])


@router.get("/transcription-models", response_model=CanvasTranscriptionModelsRead)
def list_canvas_transcription_models(request: Request, service: Canvases):
    return CanvasLocalService(service.session, request.app.state.settings).transcription_models()


@router.post(
    "/transcriptions", response_model=CanvasRuntimeTaskRead, response_model_exclude_none=True
)
def create_canvas_timeline_transcription(
    payload: CanvasTranscriptionCreate,
    request: Request,
    response: Response,
    service: Canvases,
    key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
):
    result, fresh = CanvasLocalService(
        service.session, request.app.state.settings
    ).create_transcription(payload, key)
    response.status_code = 202 if fresh else 200
    return result


@router.post("/render-plan", response_model=CanvasTimelinePlanRead)
def read_canvas_timeline_plan(
    payload: CanvasTimelinePlanRequest, service: Canvases
) -> CanvasTimelinePlanRead:
    require_actor(service.session)
    return compile_timeline_plan(payload)


@router.post("/renders", response_model=CanvasRuntimeTaskRead, response_model_exclude_none=True)
def create_canvas_timeline_render(
    payload: CanvasTimelineRenderRequest,
    request: Request,
    response: Response,
    service: Canvases,
    key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
):
    result, fresh = CanvasRenderService(service.session, request.app.state.settings).create(
        payload, key
    )
    response.status_code = 202 if fresh else 200
    return result
