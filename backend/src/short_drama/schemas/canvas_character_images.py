"""表示槽位经实际资源解析后才提供稳定资源身份；不输出存储定位。"""

from typing import Annotated

from pydantic import Field

from .asset_version import RepresentationRole
from .base import Identifier, ReadModel
from .canvas import CanvasKey
from .canvas_resource import CanvasResourceRead


class CanvasCharacterImageRead(ReadModel):
    reference_id: Identifier
    role: RepresentationRole
    ordinal: Annotated[int, Field(strict=True, ge=0)]
    media_id: Identifier
    resource_key: str
    file_path: str
    requires_canvas_copy: bool
    resource: CanvasResourceRead


class CanvasCharacterImagesRead(ReadModel):
    canvas_key: CanvasKey
    canvas_row_version: Identifier
    source_project_id: Identifier
    asset_id: Identifier
    version_id: Identifier
    images: list[CanvasCharacterImageRead]
