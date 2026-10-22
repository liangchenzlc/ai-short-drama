from typing import Literal

from pydantic import Field, model_validator

from .asset import AssetCreate, AssetRead
from .base import Identifier, InputModel, PositiveUInt32


class AssetLibraryCreate(AssetCreate):
    """Public manual creation; model/media/state are server-owned."""

    model_id: None = Field(default=None, exclude=True)
    media_id: None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def reject_server_owned_fields(cls, values):
        server_owned = {"model_id", "media_id", "state", "row_version"}
        if isinstance(values, dict) and server_owned & set(values):
            raise ValueError("media, model, state, and version are server-owned")
        return values


class LibraryAssetRead(AssetRead):
    position: PositiveUInt32 | None = None


class AssetLibraryScope(InputModel):
    kind: Literal["project"]
    parent_id: Identifier | None = None
    project_id: Identifier | None = None
