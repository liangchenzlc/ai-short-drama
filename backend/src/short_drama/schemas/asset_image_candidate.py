from datetime import datetime

from pydantic import Field

from .base import Identifier, InputModel, ReadModel


class AssetImageCandidateCreate(InputModel):
    media_id: Identifier


class AssetImageGeneration(ReadModel):
    generation_id: Identifier
    record_id: Identifier
    source_asset_id: Identifier
    source_row_version: Identifier | None = None
    source_content_hash: str | None = None
    is_stale: bool = False
    stale_reason: str | None = None


class AssetImageCandidateRead(ReadModel):
    id: Identifier
    media_id: Identifier
    url: str
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    created_at: datetime
    generation: AssetImageGeneration | None = None


class AssetConfirm(InputModel):
    row_version: Identifier
    media_id: Identifier
    expected_media_id: Identifier | None
    confirm_shared: bool = False
    acknowledge_stale_source: bool = False
