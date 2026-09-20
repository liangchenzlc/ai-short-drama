from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt32,
    ReadModel,
    nonblank,
)


class EpisodeCreate(InputModel):
    project_id: Identifier
    position: PositiveUInt32
    title: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    synopsis: MediumText = ""
    aspect: Literal["16:9", "9:16"]
    style: Annotated[str, Field(max_length=255)] = ""


class EpisodeUpdate(InputModel):
    position: PositiveUInt32 = None
    title: Annotated[str, Field(max_length=255), AfterValidator(nonblank)] = None
    synopsis: MediumText = None
    aspect: Literal["16:9", "9:16"] = None
    style: Annotated[str, Field(max_length=255)] = None


class EpisodeRead(ReadModel):
    id: Identifier
    project_id: Identifier
    position: PositiveUInt32
    title: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    synopsis: MediumText
    aspect: Literal["16:9", "9:16"]
    style: Annotated[str, Field(max_length=255)]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
