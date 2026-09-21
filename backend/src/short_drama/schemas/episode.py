from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_serializer

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
    editing_script_id: Identifier | None = None
    content_version: Identifier = 1
    storyboard_version: Identifier = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_utc(self, value: datetime | None):
        if value is None:
            return None
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat().replace("+00:00", "Z")


class EpisodeDetail(EpisodeRead):
    episode_number: int
