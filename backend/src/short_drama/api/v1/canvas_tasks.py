from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from short_drama.api.canvas_task_events import canvas_task_text_events
from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_generation import CanvasTaskBindOperation, CanvasTaskBindRead
from short_drama.schemas.canvas_task_runtime import (
    CanvasRuntimeTaskCreate,
    CanvasRuntimeTaskLog,
    CanvasRuntimeTaskRead,
    CanvasTaskTextReplayRead,
)
from short_drama.service.canvas_generation_service import CanvasGenerationService
from short_drama.service.canvas_task_service import CanvasTaskService

router = APIRouter(prefix="/canvas-runtime", tags=["Canvas tasks"])


@router.post("/tasks", response_model=CanvasRuntimeTaskRead, response_model_exclude_none=True)
def create_canvas_task(
    payload: CanvasRuntimeTaskCreate,
    request: Request,
    response: Response,
    service: Canvases,
    key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
):
    result, fresh = CanvasGenerationService(service.session, request.app.state.settings).create(
        payload, key
    )
    response.status_code = 202 if fresh else 200
    return result


@router.get("/tasks", response_model=list[CanvasRuntimeTaskRead], response_model_exclude_none=True)
def list_canvas_tasks(
    request: Request,
    service: Canvases,
    project_id: Annotated[str | None, Query(alias="projectId", max_length=64)] = None,
    active_only: Annotated[bool, Query(alias="activeOnly")] = False,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 30,
    client_operation_id: Annotated[
        str | None, Query(alias="clientOperationId", min_length=8, max_length=128)
    ] = None,
    source_node_id: Annotated[
        str | None, Query(alias="sourceNodeId", min_length=1, max_length=128)
    ] = None,
):
    return CanvasGenerationService(service.session, request.app.state.settings).list(
        project_id=project_id,
        active_only=active_only,
        limit=page_size,
        client_operation_id=client_operation_id,
        source_node_id=source_node_id,
    )


@router.get(
    "/tasks/{task_id}", response_model=CanvasRuntimeTaskRead, response_model_exclude_none=True
)
def canvas_task(task_id: Identifier, request: Request, service: Canvases):
    return CanvasGenerationService(service.session, request.app.state.settings).detail(task_id)


@router.get("/tasks/{task_id}/logs", response_model=list[CanvasRuntimeTaskLog])
def canvas_task_logs(task_id: Identifier, request: Request, service: Canvases):
    return CanvasGenerationService(service.session, request.app.state.settings).logs(task_id)


@router.get("/tasks/{task_id}/text-deltas", response_model=CanvasTaskTextReplayRead)
def canvas_text_replay(
    task_id: Identifier,
    request: Request,
    service: Canvases,
    after: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
):
    return CanvasGenerationService(service.session, request.app.state.settings).text_replay(
        task_id, after
    )


@router.get("/tasks/{task_id}/text-events")
def canvas_text_events(
    task_id: Identifier,
    request: Request,
    service: Canvases,
    after: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    last_event_id: Annotated[int, Header(alias="Last-Event-ID", ge=0, le=9223372036854775807)] = 0,
):
    cursor = max(after, last_event_id)
    initial = CanvasGenerationService(service.session, request.app.state.settings).text_replay(
        task_id, cursor
    )
    return StreamingResponse(
        canvas_task_text_events(request, task_id, service.actor_id, cursor, initial),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=CanvasRuntimeTaskRead,
    response_model_exclude_none=True,
)
def cancel_canvas_task(task_id: Identifier, request: Request, service: Canvases):
    return CanvasGenerationService(service.session, request.app.state.settings).action(
        task_id, "cancel"
    )


@router.post(
    "/tasks/{task_id}/resume",
    response_model=CanvasRuntimeTaskRead,
    response_model_exclude_none=True,
)
def resume_canvas_task(task_id: Identifier, request: Request, service: Canvases):
    return CanvasGenerationService(service.session, request.app.state.settings).action(
        task_id, "resume"
    )


@router.post(
    "/ops/canvas.task.bind", response_model=CanvasTaskBindRead, response_model_exclude_none=True
)
def bind_canvas_task(payload: CanvasTaskBindOperation, service: Canvases):
    return CanvasTaskService(service.session).bind(payload)
