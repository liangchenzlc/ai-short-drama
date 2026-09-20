from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_serializer

from .base import (
    Identifier,
    InputModel,
    MediumText,
    ReadModel,
    nonblank,
)


class ProjectCreate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    synopsis: MediumText = ""
    style: Annotated[str, Field(max_length=255)] = ""
    aspect: Literal["16:9", "9:16"]
    last_opened_at: datetime | None = None


class ProjectUpdate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None
    synopsis: MediumText = None
    style: Annotated[str, Field(max_length=255)] = None
    aspect: Literal["16:9", "9:16"] = None
    last_opened_at: datetime | None = None


class ProjectRead(ReadModel):
    id: Identifier
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    synopsis: MediumText
    style: Annotated[str, Field(max_length=255)]
    aspect: Literal["16:9", "9:16"]
    last_opened_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None

    @field_serializer("last_opened_at", "created_at", "updated_at", when_used="json")
    def serialize_utc(self, value: datetime | None):
        if value is None:
            return None
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat().replace("+00:00", "Z")


class ProjectSummary(ProjectRead):
    episode_count: int
