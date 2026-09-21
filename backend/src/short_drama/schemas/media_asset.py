from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

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
    expected_row_version: Identifier | None = None
    expected_context_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    acknowledge_stale_source: bool = False

    @model_validator(mode="after")
    def require_context(self):
        if self.target.type in {"shot_image", "asset_image"} and self.expected_row_version is None:
            raise ValueError("Image adoption requires target row version")
        if self.target.type == "shot_image" and self.expected_context_hash is None:
            raise ValueError("Shot image adoption requires context hash")
        return self
