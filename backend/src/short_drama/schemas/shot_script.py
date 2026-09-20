from datetime import datetime

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


class ShotScriptUpdate(InputModel):
    position: PositiveUInt32 = None
    script: MediumText = None


class ShotScriptRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    position: PositiveUInt32
    script: MediumText
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
