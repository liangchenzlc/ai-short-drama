from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from .base import Identifier, InputModel, ReadModel


class ShotImageSettings(InputModel):
    resolution: Literal["1K", "2K", "4K"] = "2K"
    aspect: Literal["inherit", "16:9", "9:16", "1:1", "4:3", "3:4"] = "inherit"
    layout: Literal["single", "four", "five", "nine"] = "single"


def shot_script_text(value: str) -> str:
    if len(value.encode("utf-8")) > 32 * 1024:
        raise ValueError("shot script exceeds 32 KiB UTF-8")
    return value


ShotScriptText = Annotated[str, AfterValidator(shot_script_text)]


def _require_complete_image_settings(data):
    if isinstance(data, dict) and "image_settings" in data:
        settings = data["image_settings"]
        if not isinstance(settings, dict) or set(settings) != {"resolution", "aspect", "layout"}:
            raise ValueError("image_settings must contain resolution, aspect, and layout")
    return data


class StoryboardCreate(InputModel):
    storyboard_version: Identifier
    script: ShotScriptText = ""
    duration_ms: int = Field(default=3000, strict=True, ge=1000, le=10000)
    asset_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    image_settings: ShotImageSettings = Field(default_factory=ShotImageSettings)

    _complete_settings = model_validator(mode="before")(_require_complete_image_settings)

    @field_validator("asset_ids")
    @classmethod
    def unique_assets(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("asset_ids must not contain duplicates")
        return value


class StoryboardUpdate(InputModel):
    row_version: Identifier
    script: ShotScriptText = None
    duration_ms: int = Field(default=None, strict=True, ge=1000, le=10000)
    asset_ids: list[Identifier] = Field(default=None, max_length=100)
    image_settings: ShotImageSettings = None

    _complete_settings = model_validator(mode="before")(_require_complete_image_settings)

    @field_validator("asset_ids")
    @classmethod
    def unique_assets(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("asset_ids must not contain duplicates")
        return value


class StoryboardOrder(InputModel):
    storyboard_version: Identifier
    shot_ids: list[Identifier] = Field(max_length=500)

    @field_validator("shot_ids")
    @classmethod
    def unique_shots(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("shot_ids must not contain duplicates")
        return value


class StoryboardGeneratedShot(InputModel):
    title: str = ""
    source_excerpt: Annotated[str, Field(max_length=8000)] = ""
    story_beat: Annotated[str, Field(max_length=8000)] = ""
    script: ShotScriptText
    duration_ms: int = Field(default=3000, strict=True, ge=1000, le=10000)
    asset_ids: list[Identifier] = Field(default_factory=list, max_length=100)

    @field_validator("asset_ids")
    @classmethod
    def generated_unique_assets(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("asset_ids must not contain duplicates")
        return value


class ShotImageRead(ReadModel):
    media_id: Identifier
    media_asset_id: Identifier | None = None
    url: str | None = None
    width: int | None = None
    height: int | None = None
    layout: Literal["single", "four", "five", "nine"]
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"]
    resolution: str
    is_stale: bool


class StoryboardShotRead(ReadModel):
    id: Identifier
    position: int
    script: str
    duration_ms: int
    source_excerpt: str
    row_version: Identifier
    asset_ids: list[Identifier]
    image_settings: ShotImageSettings
    context_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    image: ShotImageRead | None
    deleted_at: datetime | None


class StoryboardMutation(ReadModel):
    shot: StoryboardShotRead
    storyboard_version: Identifier


class StoryboardList(ReadModel):
    episode_id: Identifier
    storyboard_version: Identifier
    items: list[StoryboardShotRead]
    total: int
    offset: int
    limit: int


class StoryboardOrderResult(ReadModel):
    storyboard_version: Identifier
    ordered_ids: list[Identifier]
