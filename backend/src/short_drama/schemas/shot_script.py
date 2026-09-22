from datetime import datetime

from pydantic import Field

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt32,
    ReadModel,
)


class ShotScriptCreate(InputModel):
    episode_id: Identifier
    position: PositiveUInt32
    script: MediumText = ""
    duration_ms: int = Field(default=3000, strict=True, ge=1000, le=10000)


class ShotScriptUpdate(InputModel):
    position: PositiveUInt32 = None
    script: MediumText = None
    duration_ms: int = Field(default=None, strict=True, ge=1000, le=10000)
    row_version: Identifier = None


class ShotScriptRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    position: PositiveUInt32
    script: MediumText
    duration_ms: int = 3000
    source_excerpt: MediumText = ""
    row_version: Identifier = 1
    image_settings: dict | None = None
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
