"""本人风格库；不依赖画布的全局工作区模式。"""

from typing import Annotated

from fastapi import APIRouter, Path

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_style import (
    CanvasPersonalStyleResult,
    CanvasPersonalStylesRead,
    CanvasStyleActionRead,
    CanvasStyleCreate,
    CanvasStyleFavorite,
    CanvasStylePreferencesRead,
    CanvasStylePreferenceWrite,
    CanvasStyleUpdate,
    CanvasStyleVersion,
)
from short_drama.service.canvas_style_service import CanvasStyleService

router = APIRouter(prefix="/canvas-runtime/style-profiles", tags=["Canvas styles"])
StyleId = Annotated[Identifier, Path()]


@router.get("", response_model=CanvasPersonalStylesRead)
def list_personal_styles(service: Canvases):
    return CanvasStyleService(service.session).list_personal()


@router.post("", response_model=CanvasPersonalStyleResult)
def create_personal_style(payload: CanvasStyleCreate, service: Canvases):
    return CanvasStyleService(service.session).create_personal(payload)


@router.get("/preferences", response_model=CanvasStylePreferencesRead)
def read_style_preferences(service: Canvases):
    return CanvasStyleService(service.session).preferences()


@router.post("/preferences", response_model=CanvasStylePreferencesRead)
def write_style_preference(payload: CanvasStylePreferenceWrite, service: Canvases):
    return CanvasStyleService(service.session).write_preference(payload)


@router.patch("/{style_id}", response_model=CanvasPersonalStyleResult)
def update_personal_style(style_id: StyleId, payload: CanvasStyleUpdate, service: Canvases):
    return CanvasStyleService(service.session).update_personal(style_id, payload)


@router.patch("/{style_id}/favorite", response_model=CanvasStyleActionRead)
def favorite_personal_style(style_id: StyleId, payload: CanvasStyleFavorite, service: Canvases):
    return CanvasStyleService(service.session).personal_action(style_id, "favorite", payload)


@router.post("/{style_id}/use", response_model=CanvasStyleActionRead)
def use_personal_style(style_id: StyleId, payload: CanvasStyleVersion, service: Canvases):
    return CanvasStyleService(service.session).personal_action(style_id, "use", payload)


@router.delete("/{style_id}", response_model=CanvasStyleActionRead)
def delete_personal_style(style_id: StyleId, payload: CanvasStyleVersion, service: Canvases):
    return CanvasStyleService(service.session).personal_action(style_id, "delete", payload)
