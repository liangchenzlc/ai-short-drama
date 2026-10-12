from fastapi import APIRouter, Request, Response

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskRead
from short_drama.schemas.canvas_tool_runs import (
    CanvasToolApprovalsRead,
    CanvasToolApprove,
    CanvasToolClaim,
    CanvasToolExecute,
    CanvasToolGuard,
    CanvasToolHeartbeatRead,
    CanvasToolPrepare,
    CanvasToolReleaseRead,
    CanvasToolRunCreate,
    CanvasToolRunDetail,
    CanvasToolRunList,
    CanvasToolRunRead,
    CanvasToolSubmissionRead,
)
from short_drama.service.canvas_tool_run_service import CanvasToolRunService

router = APIRouter(prefix="/canvas-runtime/creation-runs", tags=["Canvas editor tools"])


def service(request: Request, canvases: Canvases) -> CanvasToolRunService:
    return CanvasToolRunService(canvases.session, request.app.state.settings)


@router.post("", response_model=CanvasToolRunDetail, response_model_exclude_none=True)
def create_run(
    payload: CanvasToolRunCreate, request: Request, response: Response, canvases: Canvases
) -> dict:
    result, fresh = service(request, canvases).create(payload)
    response.status_code = 201 if fresh else 200
    return result


@router.get("", response_model=CanvasToolRunList, response_model_exclude_none=True)
def list_runs(request: Request, canvases: Canvases) -> dict:
    return service(request, canvases).list()


@router.get("/{run_id}", response_model=CanvasToolRunDetail, response_model_exclude_none=True)
def get_run(run_id: Identifier, request: Request, canvases: Canvases) -> dict:
    return service(request, canvases).get(run_id)


@router.post("/{run_id}/claim", response_model=CanvasToolRunRead, response_model_exclude_none=True)
def claim_run(
    run_id: Identifier, payload: CanvasToolClaim, request: Request, canvases: Canvases
) -> dict:
    return service(request, canvases).claim(run_id, payload)


@router.post("/{run_id}/heartbeat", response_model=CanvasToolHeartbeatRead)
def heartbeat_run(
    run_id: Identifier, payload: CanvasToolGuard, request: Request, canvases: Canvases
) -> dict:
    return service(request, canvases).heartbeat(run_id, payload)


@router.post("/{run_id}/release", response_model=CanvasToolReleaseRead)
def release_run(
    run_id: Identifier, payload: CanvasToolGuard, request: Request, canvases: Canvases
) -> dict:
    return service(request, canvases).release(run_id, payload)


@router.post(
    "/{run_id}/submissions/prepare",
    response_model=CanvasToolSubmissionRead,
    response_model_exclude_none=True,
)
def prepare_stage(
    run_id: Identifier, payload: CanvasToolPrepare, request: Request, canvases: Canvases
) -> dict:
    return service(request, canvases).prepare(run_id, payload)


@router.post(
    "/{run_id}/submissions/approve",
    response_model=CanvasToolApprovalsRead,
    response_model_exclude_none=True,
)
def approve_stages(
    run_id: Identifier, payload: CanvasToolApprove, request: Request, canvases: Canvases
) -> dict:
    return service(request, canvases).approve(run_id, payload)


@router.post(
    "/{run_id}/execute", response_model=CanvasRuntimeTaskRead, response_model_exclude_none=True
)
def execute_stage(
    run_id: Identifier,
    payload: CanvasToolExecute,
    request: Request,
    response: Response,
    canvases: Canvases,
) -> dict:
    result, fresh = service(request, canvases).execute(run_id, payload)
    response.status_code = 202 if fresh else 200
    return result
