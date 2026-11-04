from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from short_drama.schemas.base import (
    Identifier,
    InputModel,
    MediumText,
    ReadModel,
    nonblank,
)


def trimmed(value: str) -> str:
    return value.strip()


Name = Annotated[str, Field(max_length=255), AfterValidator(nonblank), AfterValidator(trimmed)]
Label = Annotated[str, Field(max_length=120)]
SceneTime = Annotated[str, Field(max_length=60)]


class AssetImageRead(ReadModel):
    media_id: Identifier
    url: str
    width: int | None = None
    height: int | None = None


class AssetCreate(InputModel):
    kind: Literal["character", "scene", "prop"] = Field(
        description="素材类型：人物角色 character、场景 scene 或道具 prop。"
    )
    name: Name = Field(description="在当前项目内识别该素材的名称。")
    label: Label = Field(default="", description="可选的短标签/称谓。")
    description: MediumText = Field(
        default="", description="素材的稳定设定说明，不是图片生成提示词。"
    )
    prompt: MediumText = Field(default="", description="用于后续素材图片生成的视觉提示词。")
    tags: list[Annotated[str, Field(max_length=40)]] = Field(
        default_factory=list, max_length=20, description="最多 20 个用于筛选的短标签。"
    )
    scene_time: SceneTime = Field(default="", description="场景发生的时间标记；仅 scene 类型使用。")
    media_id: Identifier | None = Field(
        default=None, description="媒体身份由生成/采用流程管理；助手创建素材时省略此字段。"
    )

    @field_validator("label", "scene_time")
    @classmethod
    def strip_short_text(cls, value):
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("tags must not be blank")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def validate_scene_time(self):
        if self.kind != "scene" and self.scene_time:
            raise ValueError("scene_time is only allowed for scene assets")
        return self


class AssetUpdate(InputModel):
    row_version: Identifier = None
    kind: Literal["character", "scene", "prop"] = None
    name: Name = None
    label: Label = None
    description: MediumText = None
    prompt: MediumText = None
    tags: list[Annotated[str, Field(max_length=40)]] = Field(default=None, max_length=20)
    scene_time: SceneTime = None
    model_id: Identifier | None = None
    media_id: Identifier | None = None

    @field_validator("label", "scene_time")
    @classmethod
    def strip_short_text(cls, value):
        return value.strip() if value is not None else value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        if values is None:
            return values
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("tags must not be blank")
            if value not in result:
                result.append(value)
        return result


class AssetPatch(InputModel):
    row_version: Identifier = Field(description="目标素材当前 row_version；来自最近一次素材读取。")
    kind: Literal["character", "scene", "prop"] = Field(
        default=None, description="新的素材类别；仅在明确要改变类别时提供。"
    )
    name: Name = Field(default=None, description="要更新的素材名称；省略表示保持原值。")
    label: Label = Field(default=None, description="要更新的短标签/称谓。")
    description: MediumText = Field(default=None, description="要更新的稳定素材设定说明。")
    prompt: MediumText = Field(default=None, description="要更新的后续图片生成视觉提示词。")
    tags: list[Annotated[str, Field(max_length=40)]] = Field(
        default=None, max_length=20, description="替换为新的完整标签列表；不是追加语义。"
    )
    scene_time: SceneTime = Field(
        default=None, description="要更新的场景时间标记；仅适用于 scene。"
    )

    @field_validator("label", "scene_time")
    @classmethod
    def strip_short_text(cls, value):
        return value.strip() if value is not None else value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        if values is None:
            return values
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("tags must not be blank")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="before")
    @classmethod
    def reject_empty_patch(cls, values):
        if isinstance(values, dict):
            changed = set(values) - {"row_version"}
            if not changed:
                raise ValueError("provide at least one field to update")
        return values


class AssetRead(ReadModel):
    id: Identifier
    kind: Literal["character", "scene", "prop"]
    name: Name
    label: Label
    description: MediumText
    prompt: MediumText
    tags: list[str] = Field(default_factory=list)
    scene_time: SceneTime = ""
    state: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    row_version: Identifier = 1
    media_id: Identifier | None = None
    image: AssetImageRead | None = None
    reference_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssetRecordRead(ReadModel):
    """Internal row DTO retained for legacy services; public APIs use AssetRead."""

    id: Identifier
    kind: Literal["character", "scene", "prop"]
    name: Name
    label: Label
    description: MediumText
    prompt: MediumText
    model_id: Identifier | None = None
    media_id: Identifier | None = None
    row_version: Identifier = 1
    state: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    tags: list[str] = Field(default_factory=list)
    scene_time: SceneTime = ""
    creation_key: str | None = None
    creation_hash: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
