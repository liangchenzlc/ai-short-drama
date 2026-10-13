from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt32,
    ReadModel,
)

ShotSizeEnum = Literal[
    "full_shot", "medium_shot", "close_up", "extreme_close_up", "wide_shot", "two_shot"
]
ShotTitle = Annotated[str, Field(max_length=255)]


class ShotScriptCreate(InputModel):
    episode_id: Identifier
    position: PositiveUInt32
    script: MediumText = ""
    duration_ms: int = Field(default=3000, strict=True, ge=1000, le=10000)
    title: ShotTitle = ""
    shot_size: ShotSizeEnum | None = None


class ShotScriptUpdate(InputModel):
    position: PositiveUInt32 = None
    script: MediumText = None
    duration_ms: int = Field(default=None, strict=True, ge=1000, le=10000)
    title: ShotTitle = None
    shot_size: ShotSizeEnum | None = None
    row_version: Identifier = None


class ShotScriptRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    position: PositiveUInt32
    script: MediumText
    duration_ms: int = 3000
    title: ShotTitle = ""
    shot_size: ShotSizeEnum | None = None
    source_excerpt: MediumText = ""
    row_version: Identifier = 1
    image_settings: dict | None = None
    video_prompt: MediumText = ""
    video_settings: dict | None = None
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
