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


class ProjectCreate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    synopsis: MediumText = ""
    style: Annotated[str, Field(max_length=255)] = ""
    aspect: Literal["16:9", "9:16"]
    target_ms: Annotated[int, Field(strict=True, ge=1000, le=3600000)]
    last_opened_at: datetime | None = None


class ProjectUpdate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None
    synopsis: MediumText = None
    style: Annotated[str, Field(max_length=255)] = None
    aspect: Literal["16:9", "9:16"] = None
    target_ms: Annotated[int, Field(strict=True, ge=1000, le=3600000)] = None
    last_opened_at: datetime | None = None


class ProjectRead(ReadModel):
    id: Identifier
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    synopsis: MediumText
    style: Annotated[str, Field(max_length=255)]
    aspect: Literal["16:9", "9:16"]
    target_ms: Annotated[int, Field(strict=True, ge=1000, le=3600000)]
    last_opened_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
