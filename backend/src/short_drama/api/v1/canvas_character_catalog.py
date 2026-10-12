"""专用只读角色合同；标准角色接口与源 ProjectDetail 保持独立。"""

from typing import Annotated

from fastapi import APIRouter, Path

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas import CanvasKey
from short_drama.schemas.canvas_character_catalog import (
    CanvasCharacterCatalogDetailRead,
    CanvasCharacterCatalogRead,
)
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService

router = APIRouter(tags=["Canvas character catalog"])
PATH = "/canvas-runtime/canvas-projects/{source_key}/character-catalog"
SourceKey = Annotated[CanvasKey, Path()]
AssetId = Annotated[Identifier, Path()]


@router.get(PATH, response_model=CanvasCharacterCatalogRead)
def list_canvas_characters(source_key: SourceKey, service: Canvases):
    return CanvasCharacterCatalogService(service.session).list(source_key)


@router.get(PATH + "/{asset_id}", response_model=CanvasCharacterCatalogDetailRead)
def get_canvas_character(source_key: SourceKey, asset_id: AssetId, service: Canvases):
    return CanvasCharacterCatalogService(service.session).get(source_key, asset_id)
