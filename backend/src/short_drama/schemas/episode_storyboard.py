from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from .base import Identifier, InputModel, ReadModel


class ShotImageSettings(InputModel):
    resolution: Literal["1K", "2K", "4K"] = "2K"
    aspect: Literal["inherit", "16:9", "9:16", "1:1", "4:3", "3:4"] = "inherit"
    layout: Literal["single", "four", "five", "nine"] = "single"


class ShotVideoSettings(InputModel):
    resolution: Literal["480p", "720p", "1080p"] = "720p"
    duration_ms: int | None = Field(
        default=None, strict=True, ge=1000, le=3600000, multiple_of=1000
    )


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
    video_prompt: Annotated[str, Field(max_length=16000)] = None
    video_settings: ShotVideoSettings = None

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


class StoryboardResultAsset(ReadModel):
    id: Identifier
    kind: Literal["character", "prop", "scene"] | None
    name: str
    available: bool
    snapshot_missing: bool


class StoryboardResultShot(ReadModel):
    position: int = Field(ge=1)
    title: str
    script: str
    duration_ms: int = Field(ge=1000, le=10000)
    source_excerpt: str
    story_beat: str
    asset_ids: list[Identifier]
    assets: list[StoryboardResultAsset]


class StoryboardResultAdoption(ReadModel):
    mode: Literal["append", "replace"]
    shot_ids: list[Identifier] = Field(default_factory=list)
    applied_at: datetime | None = None
    storyboard_version: Identifier | None = None


class StoryboardResultPage(ReadModel):
    generation_id: Identifier
    items: list[StoryboardResultShot]
    total: int = Field(ge=0)
    total_duration_ms: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    applied: StoryboardResultAdoption | None


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


class ShotVideoRead(ReadModel):
    media_id: Identifier
    media_asset_id: Identifier | None = None
    url: str | None = None
    resolution: str
    duration_ms: int
    is_stale: bool
    first_frame_media_id: Identifier | None = None


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
    video_prompt: str = ""
    video_default_prompt: str = ""
    video_system_prompt: str = ""
    video_settings: ShotVideoSettings = Field(default_factory=ShotVideoSettings)
    video_context_hash: str = ""
    native_speech: dict | None = None
    video: ShotVideoRead | None = None
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


class StoryboardMove(InputModel):
    storyboard_version: Identifier
    direction: Literal[-1, 1]
