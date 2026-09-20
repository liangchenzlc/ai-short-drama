from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt64,
    ReadModel,
    nonblank,
)


class ShotVideoCreate(InputModel):
    episode_id: Identifier
    shot_id: Identifier
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] = "1080p"
    duration: PositiveUInt64
    prompt: MediumText = ""
    media_id: Identifier
    model_id: Identifier | None = None


class ShotVideoUpdate(InputModel):
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] = None
    duration: PositiveUInt64 = None
    prompt: MediumText = None
    media_id: Identifier = None
    model_id: Identifier | None = None


class ShotVideoRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    shot_id: Identifier
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)]
    duration: PositiveUInt64
    prompt: MediumText
    media_id: Identifier
    state: Literal["confirmed"]
    model_id: Identifier | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
