from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from .ai_generation import Aspect
from .base import Identifier, InputModel, nonblank


class MediaAssetRename(InputModel):
    name: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    row_version: Identifier


class MediaAssetTarget(InputModel):
    type: Literal["shot_image", "shot_video", "asset_image"]
    id: Identifier


class ApplyParameters(InputModel):
    layout: Literal["single", "four", "five", "nine"] | None = None
    aspect: Aspect | None = None
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] | None = None
    duration: int | None = Field(default=None, strict=True, ge=1)


class MediaAssetApply(InputModel):
    target: MediaAssetTarget
    expected_media_id: Identifier | None
    parameters: ApplyParameters = Field(default_factory=ApplyParameters)
    confirm_shared: bool = False
