from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    PositiveUInt32,
    ReadModel,
)


class GlobalAssetCreate(InputModel):
    asset_id: Identifier
    position: PositiveUInt32


class GlobalAssetUpdate(InputModel):
    asset_id: Identifier = None
    position: PositiveUInt32 = None


class GlobalAssetRead(ReadModel):
    id: Identifier
    asset_id: Identifier
    position: PositiveUInt32
    created_at: datetime | None = None
    created_by: Identifier | None = None
