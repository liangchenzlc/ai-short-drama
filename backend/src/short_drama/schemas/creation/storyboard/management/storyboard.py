from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel
from short_drama.schemas.creation.storyboard.management.script import (
    ShotDetails,
    ShotSizeEnum,
    ShotTitle,
)


class ShotImageSettings(InputModel):
    resolution: Literal["1K", "2K", "4K"] = Field(
        default="2K", description="下一次分镜图片生成的分辨率预设。"
    )
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] | None = Field(
        default=None, description="下一次图片生成的画幅比例；null 表示使用模型默认比例。"
    )
    layout: Literal["single", "four", "five", "nine"] = Field(
        default="single", description="生成图片组布局预设；不是分镜正文中的镜头数量。"
    )


class ShotVideoSettings(InputModel):
    resolution: Literal["480p", "720p", "1080p"] = Field(
        default="720p", description="下一次分镜视频生成的分辨率预设。"
    )
    duration_ms: int | None = Field(
        default=None,
        strict=True,
        ge=1000,
        le=3600000,
        multiple_of=1000,
        description="下一次视频生成时长，单位毫秒且必须整秒；null 表示沿用模型/请求默认值。",
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
    title: ShotTitle = ""
    shot_size: ShotSizeEnum | None = None
    script: ShotScriptText = ""
    details: ShotDetails = Field(default_factory=dict)
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
    row_version: Identifier = Field(description="目标镜头当前 row_version；来自最近一次分镜读取。")
    title: ShotTitle = Field(default=None, description="分场/镜头标题；省略表示不修改。")
    shot_size: ShotSizeEnum | None = Field(
        default=None, description="镜头景别枚举；显式 null 可清除景别。"
    )
    script: ShotScriptText = Field(
        default=None, description="分镜镜头/分场正文；不是 ProjectScript 整篇剧本。"
    )
    details: ShotDetails = Field(default=None, description="机位、运镜、对白设想和声音设想。")
    duration_ms: int = Field(
        default=None, strict=True, ge=1000, le=10000, description="镜头建议时长，单位毫秒。"
    )
    asset_ids: list[Identifier] = Field(
        default=None,
        max_length=100,
        description="替换完整素材关联集合；助手结构化写入须改用单独 set_shot_assets 动作。",
    )
    image_settings: ShotImageSettings = Field(
        default=None, description="替换下一次图片生成设置，提供时须含完整设置对象。"
    )
    video_prompt: Annotated[
        str, Field(max_length=16000, description="下一次分镜视频生成的补充提示词。")
    ] = None
    video_settings: ShotVideoSettings = Field(default=None, description="替换下一次视频生成设置。")

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


class StoryboardDelete(InputModel):
    row_version: Identifier
    storyboard_version: Identifier


class StoryboardDeleteResult(ReadModel):
    deleted_id: Identifier
    storyboard_version: Identifier


class StoryboardGeneratedShot(InputModel):
    title: ShotTitle = ""
    shot_size: ShotSizeEnum | None = None
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
    shot_size: ShotSizeEnum | None = None
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
    aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] | None = None
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
    title: ShotTitle = ""
    shot_size: ShotSizeEnum | None = None
    script: str
    details: ShotDetails = Field(default_factory=dict)
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
    project_id: Identifier
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
