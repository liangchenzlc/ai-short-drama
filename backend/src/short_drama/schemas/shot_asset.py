from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    ReadModel,
)


class ShotAssetCreate(InputModel):
    episode_id: Identifier
    asset_id: Identifier
    shot_id: Identifier


class ShotAssetUpdate(InputModel):
    asset_id: Identifier = None


class ShotAssetRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    asset_id: Identifier
    shot_id: Identifier
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
