from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt64,
    ReadModel,
    nonblank,
)


class MediaRecycleBinCreate(InputModel):
    shot_id: Identifier
    media_id: Identifier
    model_id: Identifier | None = None
    prompt: MediumText = ""
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)]
    layout: Literal["single", "four", "five", "nine"] | None = None
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] | None = None
    duration: PositiveUInt64 | None = None
    reason: Literal["discarded", "replaced"]

    @model_validator(mode="after")
    def validate_parameter_shape(self):
        image = self.layout is not None and self.aspect is not None and self.duration is None
        video = self.layout is None and self.aspect is None and self.duration is not None
        if not (image or video):
            raise ValueError("provide either image layout/aspect or video duration")
        return self


class MediaRecycleBinRead(ReadModel):
    id: Identifier
    shot_id: Identifier
    media_id: Identifier
    model_id: Identifier | None = None
    prompt: MediumText
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)]
    layout: Literal["single", "four", "five", "nine"] | None = None
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] | None = None
    duration: PositiveUInt64 | None = None
    reason: Literal["discarded", "replaced"]
    created_at: datetime | None = None
    created_by: Identifier | None = None
