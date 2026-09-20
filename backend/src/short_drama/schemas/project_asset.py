from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    PositiveUInt32,
    ReadModel,
)


class ProjectAssetCreate(InputModel):
    project_id: Identifier
    asset_id: Identifier
    position: PositiveUInt32


class ProjectAssetUpdate(InputModel):
    asset_id: Identifier = None
    position: PositiveUInt32 = None


class ProjectAssetRead(ReadModel):
    id: Identifier
    project_id: Identifier
    asset_id: Identifier
    position: PositiveUInt32
    created_at: datetime | None = None
    created_by: Identifier | None = None
