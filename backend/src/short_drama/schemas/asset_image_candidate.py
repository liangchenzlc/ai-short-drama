from datetime import datetime

from pydantic import Field

from .base import Identifier, InputModel, ReadModel


class AssetImageCandidateCreate(InputModel):
    media_id: Identifier


class AssetImageCandidateRead(ReadModel):
    id: Identifier
    media_id: Identifier
    url: str
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    created_at: datetime


class AssetConfirm(InputModel):
    row_version: Identifier
    media_id: Identifier
    expected_media_id: Identifier | None
    confirm_shared: bool = False
