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


class ShotImageCreate(InputModel):
    episode_id: Identifier
    shot_id: Identifier
    layout: Literal["single", "four", "five", "nine"] = "single"
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"]
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] = "2K"
    prompt: MediumText = ""
    media_id: Identifier
    model_id: Identifier | None = None


class ShotImageUpdate(InputModel):
    layout: Literal["single", "four", "five", "nine"] = None
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] = None
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] = None
    prompt: MediumText = None
    media_id: Identifier = None
    model_id: Identifier | None = None


class ShotImageRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    shot_id: Identifier
    layout: Literal["single", "four", "five", "nine"]
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"]
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)]
    prompt: MediumText
    media_id: Identifier
    state: Literal["confirmed"]
    model_id: Identifier | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
