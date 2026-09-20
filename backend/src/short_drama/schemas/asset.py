from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from .base import (
    Identifier,
    InputModel,
    MediumText,
    ReadModel,
    nonblank,
)


class AssetCreate(InputModel):
    kind: Literal["character", "scene", "prop"]
    name: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    label: Annotated[str, Field(max_length=120)] = ""
    description: MediumText = ""
    prompt: MediumText = ""
    model_id: Identifier | None = None
    media_id: Identifier | None = None


class AssetUpdate(InputModel):
    kind: Literal["character", "scene", "prop"] = None
    name: Annotated[str, Field(max_length=255), AfterValidator(nonblank)] = None
    label: Annotated[str, Field(max_length=120)] = None
    description: MediumText = None
    prompt: MediumText = None
    model_id: Identifier | None = None
    media_id: Identifier | None = None


class AssetRead(ReadModel):
    id: Identifier
    kind: Literal["character", "scene", "prop"]
    name: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    label: Annotated[str, Field(max_length=120)]
    description: MediumText
    prompt: MediumText
    model_id: Identifier | None = None
    media_id: Identifier | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
