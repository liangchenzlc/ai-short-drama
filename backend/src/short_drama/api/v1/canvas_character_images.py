"""专用只读图片 resolver；请求必须声明从目录读取的当前版本。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas import CanvasKey
from short_drama.schemas.canvas_character_images import CanvasCharacterImagesRead
from short_drama.service.canvas_character_image_service import (
    CanvasCharacterImageService,
)

router = APIRouter(tags=["Canvas character images"])
PATH = "/canvas-runtime/canvas-projects/{source_key}/character-catalog/{asset_id}/image-resources"
SourceKey = Annotated[CanvasKey, Path()]
AssetId = Annotated[Identifier, Path()]
ExpectedVersion = Annotated[Identifier, Query()]


@router.get(PATH, response_model=CanvasCharacterImagesRead)
def get_canvas_character_images(
    source_key: SourceKey,
    asset_id: AssetId,
    expected_version_id: ExpectedVersion,
    request: Request,
    service: Canvases,
    version_policy: Annotated[Literal["current", "pinned"], Query()] = "current",
) -> CanvasCharacterImagesRead:
    return CanvasCharacterImageService(service.session, request.app.state.settings).get(
        source_key, asset_id, expected_version_id, version_policy=version_policy
    )
