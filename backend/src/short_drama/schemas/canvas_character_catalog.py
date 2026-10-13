"""画布只读角色目录专用合同；媒体 ID 不表示画布资源 ID。"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .asset_version import RepresentationRole
from .base import Identifier, PositiveUInt32, ReadModel, UInt64
from .canvas import CanvasKey
from .canvas_resource import CanvasResourceRead


class CanvasCharacterRepresentationRead(ReadModel):
    reference_id: Identifier
    role: RepresentationRole
    ordinal: Annotated[int, Field(strict=True, ge=0)]
    media_id: Identifier
    format_code: str
    mime_type: str | None
    original_name: str
    byte_size: UInt64 | None
    width: PositiveUInt32 | None
    height: PositiveUInt32 | None


class CanvasCharacterVoiceSampleRead(ReadModel):
    binding_version: Identifier
    media_id: Identifier
    checksum_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    duration_ms: Annotated[int, Field(strict=True, ge=3000, le=7500)]
    resource: CanvasResourceRead


class CanvasCharacterCatalogItemRead(ReadModel):
    asset_id: Identifier
    asset_row_version: Identifier
    current_version_id: Identifier
    version_id: Identifier
    version: Identifier
    position: PositiveUInt32
    name: str
    label: str
    description: str
    prompt: str
    tags: list[str]
    definition: dict[str, JsonValue]
    definition_origin: Literal["legacy_current", "structured"]
    representations: list[CanvasCharacterRepresentationRead]
    voice_status: Literal["missing", "ready", "unavailable"] = "unavailable"
    voice_sample: CanvasCharacterVoiceSampleRead | None = None


class CanvasCharacterCatalogRead(ReadModel):
    canvas_key: CanvasKey
    canvas_row_version: Identifier
    source_project_id: Identifier
    items: list[CanvasCharacterCatalogItemRead]


class CanvasCharacterCatalogDetailRead(ReadModel):
    canvas_key: CanvasKey
    canvas_row_version: Identifier
    source_project_id: Identifier
    item: CanvasCharacterCatalogItemRead
