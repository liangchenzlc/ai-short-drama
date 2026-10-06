from typing import Annotated

from fastapi import APIRouter, Path

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas import CanvasKey, SourceKey
from short_drama.schemas.canvas_drawing import (
    CanvasDrawingDelete,
    CanvasDrawingEnvelope,
    CanvasDrawingList,
    CanvasDrawingRequest,
)
from short_drama.service.canvas_drawing_service import CanvasDrawingService

router = APIRouter(
    prefix="/canvas-runtime/canvas-projects/{canvas_key}/drawings", tags=["Canvas drawings"]
)
CanvasPath = Annotated[CanvasKey, Path()]
DrawingPath = Annotated[SourceKey, Path()]


@router.get("", response_model=CanvasDrawingList, response_model_exclude_none=True)
def list_drawings(canvas_key: CanvasPath, service: Canvases):
    return CanvasDrawingService(service.session).list(canvas_key)


@router.get(
    "/{drawing_key}", response_model=CanvasDrawingEnvelope, response_model_exclude_none=True
)
def get_drawing(canvas_key: CanvasPath, drawing_key: DrawingPath, service: Canvases):
    return CanvasDrawingService(service.session).get(canvas_key, drawing_key)


@router.get(
    "/{drawing_key}/versions/{revision}",
    response_model=CanvasDrawingEnvelope,
    response_model_exclude_none=True,
)
def get_drawing_version(
    canvas_key: CanvasPath,
    drawing_key: DrawingPath,
    revision: Annotated[Identifier, Path()],
    service: Canvases,
):
    return CanvasDrawingService(service.session).get(canvas_key, drawing_key, revision)


@router.put(
    "/{drawing_key}", response_model=CanvasDrawingEnvelope, response_model_exclude_none=True
)
def put_drawing(
    canvas_key: CanvasPath,
    drawing_key: DrawingPath,
    payload: CanvasDrawingRequest,
    service: Canvases,
):
    return CanvasDrawingService(service.session).put(canvas_key, drawing_key, payload.drawing)


@router.delete("/{drawing_key}", response_model=CanvasDrawingDelete)
def delete_drawing(canvas_key: CanvasPath, drawing_key: DrawingPath, service: Canvases):
    return CanvasDrawingService(service.session).delete(canvas_key, drawing_key)
