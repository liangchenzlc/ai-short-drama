from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    PositiveUInt32,
    ReadModel,
)


class EpisodeAssetCreate(InputModel):
    episode_id: Identifier
    asset_id: Identifier
    position: PositiveUInt32


class EpisodeAssetUpdate(InputModel):
    asset_id: Identifier = None
    position: PositiveUInt32 = None


class EpisodeAssetRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    asset_id: Identifier
    position: PositiveUInt32
    created_at: datetime | None = None
    created_by: Identifier | None = None
