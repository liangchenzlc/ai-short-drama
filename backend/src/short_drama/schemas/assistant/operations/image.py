"""Task-independent image identities; no locator or runtime identity is accepted."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, parse_identifier
from short_drama.schemas.media.image_groups.groups import ImagePrimarySet


class ImageTarget(InputModel):
    project_id: Identifier
    target_kind: Literal["asset", "shot"]
    target_id: Identifier


class ReadImageGroup(ImageTarget):
    group_id: Identifier | None = None
    view: Literal["current", "detail", "history"] = "current"
    offset: Annotated[int, Field(ge=0, le=100000)] = 0
    limit: Annotated[int, Field(ge=1, le=10)] = 5
    character_offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class ReadImageContent(InputModel):
    project_id: Identifier
    target_kind: Literal["asset", "shot", "canvas_node"]
    target_id: Annotated[str, Field(min_length=1, max_length=128)]
    canvas_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    group_id: Identifier | None = None
    item_id: Identifier | None = None
    media_id: Identifier
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    detail: Literal["low", "high", "original"] = "low"
    crop: tuple[int, int, int, int] | None = None

    @model_validator(mode="after")
    def concrete_image_identity(self):
        if self.target_kind == "canvas_node":
            if self.canvas_id is None or self.group_id is not None or self.item_id is not None:
                raise ValueError("canvas images require canvas_id and do not use image groups")
        else:
            parse_identifier(self.target_id)
            if self.canvas_id is not None or self.group_id is None or self.item_id is None:
                raise ValueError("asset and shot images require their group and item identities")
        return self


class SetImageGroupPrimary(ImagePrimarySet, ImageTarget):
    group_id: Identifier
