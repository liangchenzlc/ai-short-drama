from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from .base import (
    Identifier,
    InputModel,
    MediumText,
    ReadModel,
    nonblank,
)


def trimmed(value: str) -> str:
    return value.strip()


Name = Annotated[str, Field(max_length=255), AfterValidator(nonblank), AfterValidator(trimmed)]
Label = Annotated[str, Field(max_length=120)]
SceneTime = Annotated[str, Field(max_length=60)]


class AssetImageRead(ReadModel):
    media_id: Identifier
    url: str
    width: int | None = None
    height: int | None = None


class AssetCreate(InputModel):
    kind: Literal["character", "scene", "prop"]
    name: Name
    label: Label = ""
    description: MediumText = ""
    prompt: MediumText = ""
    tags: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=20)
    scene_time: SceneTime = ""
    media_id: Identifier | None = None

    @field_validator("label", "scene_time")
    @classmethod
    def strip_short_text(cls, value):
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("tags must not be blank")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def validate_scene_time(self):
        if self.kind != "scene" and self.scene_time:
            raise ValueError("scene_time is only allowed for scene assets")
        return self


class AssetUpdate(InputModel):
    row_version: Identifier = None
    kind: Literal["character", "scene", "prop"] = None
    name: Name = None
    label: Label = None
    description: MediumText = None
    prompt: MediumText = None
    tags: list[Annotated[str, Field(max_length=40)]] = Field(default=None, max_length=20)
    scene_time: SceneTime = None
    model_id: Identifier | None = None
    media_id: Identifier | None = None

    @field_validator("label", "scene_time")
    @classmethod
    def strip_short_text(cls, value):
        return value.strip() if value is not None else value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        if values is None:
            return values
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("tags must not be blank")
            if value not in result:
                result.append(value)
        return result


class AssetPatch(InputModel):
    row_version: Identifier
    name: Name = None
    label: Label = None
    description: MediumText = None
    prompt: MediumText = None
    tags: list[Annotated[str, Field(max_length=40)]] = Field(default=None, max_length=20)
    scene_time: SceneTime = None
    confirm_shared: bool = False

    @field_validator("label", "scene_time")
    @classmethod
    def strip_short_text(cls, value):
        return value.strip() if value is not None else value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        if values is None:
            return values
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("tags must not be blank")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="before")
    @classmethod
    def reject_kind_and_empty_patch(cls, values):
        if isinstance(values, dict):
            if "kind" in values:
                raise ValueError("asset kind cannot be changed")
            changed = set(values) - {"row_version", "confirm_shared"}
            if not changed:
                raise ValueError("provide at least one field to update")
        return values


class AssetRead(ReadModel):
    id: Identifier
    kind: Literal["character", "scene", "prop"]
    name: Name
    label: Label
    description: MediumText
    prompt: MediumText
    tags: list[str] = Field(default_factory=list)
    scene_time: SceneTime = ""
    state: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    row_version: Identifier = 1
    media_id: Identifier | None = None
    image: AssetImageRead | None = None
    reference_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssetRecordRead(ReadModel):
    """Internal row DTO retained for legacy services; public APIs use AssetRead."""

    id: Identifier
    kind: Literal["character", "scene", "prop"]
    name: Name
    label: Label
    description: MediumText
    prompt: MediumText
    model_id: Identifier | None = None
    media_id: Identifier | None = None
    row_version: Identifier = 1
    state: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    tags: list[str] = Field(default_factory=list)
    scene_time: SceneTime = ""
    creation_key: str | None = None
    creation_hash: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
