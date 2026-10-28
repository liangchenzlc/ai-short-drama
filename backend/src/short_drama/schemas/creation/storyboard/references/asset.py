from datetime import datetime

from short_drama.schemas.base import (
    Identifier,
    InputModel,
    ReadModel,
)


class ShotAssetCreate(InputModel):
    project_id: Identifier
    asset_id: Identifier
    shot_id: Identifier


class ShotAssetUpdate(InputModel):
    asset_id: Identifier = None


class ShotAssetRead(ReadModel):
    id: Identifier
    project_id: Identifier
    asset_id: Identifier
    shot_id: Identifier
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
