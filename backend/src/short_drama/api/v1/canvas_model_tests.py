from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_model_test import CanvasModelTestCreate, CanvasModelTestRead
from short_drama.service.canvas_model_test_service import CanvasModelTestService

router = APIRouter(prefix="/canvas-runtime/model-tests", tags=["Canvas model tests"])


@router.post("", response_model=CanvasModelTestRead, response_model_exclude_none=True)
def create_model_test(
    payload: CanvasModelTestCreate,
    request: Request,
    response: Response,
    service: Canvases,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
):
    value, fresh = CanvasModelTestService(service.session, request.app.state.settings).create(
        payload, key
    )
    response.status_code = 202 if fresh else 200
    return value


@router.get("/{test_id}", response_model=CanvasModelTestRead, response_model_exclude_none=True)
def get_model_test(test_id: Identifier, request: Request, service: Canvases):
    return CanvasModelTestService(service.session, request.app.state.settings).detail(test_id)


@router.post(
    "/{test_id}/cancel", response_model=CanvasModelTestRead, response_model_exclude_none=True
)
def cancel_model_test(test_id: Identifier, request: Request, service: Canvases):
    return CanvasModelTestService(service.session, request.app.state.settings).cancel(test_id)
