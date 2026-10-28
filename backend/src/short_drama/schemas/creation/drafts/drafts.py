"""按本人、分集、稳定对象和用途隔离的生成草稿合同。"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel
from short_drama.schemas.generations.admission.generation import Aspect

DraftPurpose = Literal["asset_image", "storyboard_image", "storyboard_video"]


def draft_prompt_limit(purpose: DraftPurpose) -> int:
    return 16000 if purpose == "storyboard_video" else 4000


class DraftImageSettings(InputModel):
    aspect: Aspect | None = None
    resolution: Literal["1K", "2K", "4K"] | None = None
    count: int = Field(default=1, strict=True, ge=1, le=4)

    @field_validator("count")
    @classmethod
    def supported_count(cls, value):
        if value not in (1, 2, 4):
            raise ValueError("图片张数仅支持 1、2、4")
        return value


class DraftVideoSettings(InputModel):
    aspect: Aspect | None = None
    resolution: Literal["480p", "720p", "1080p"] | None = None
    duration_ms: int | None = Field(
        default=None, strict=True, ge=1000, le=3600000, multiple_of=1000
    )


class UserCreationDraftCreate(InputModel):
    object_id: Identifier
    purpose: DraftPurpose
    model_config_id: Identifier | None = None
    input_text: Annotated[str, Field(max_length=16000)] = ""
    image_settings: DraftImageSettings | None = None
    video_settings: DraftVideoSettings | None = None
    reference_media_ids: list[Identifier] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def prompt_limit(self):
        maximum = draft_prompt_limit(self.purpose)
        if len(self.input_text) > maximum:
            raise ValueError(f"当前生成用途的输入不能超过 {maximum} 字符")
        return self


class UserCreationDraftUpdate(InputModel):
    row_version: Identifier
    model_config_id: Identifier | None = None
    input_text: Annotated[str, Field(max_length=16000)] = None
    image_settings: DraftImageSettings | None = None
    video_settings: DraftVideoSettings | None = None
    reference_media_ids: list[Identifier] = Field(default=None, max_length=16)


class DraftReferenceImage(ReadModel):
    media_id: Identifier
    url: str | None = None
    width: int | None = None
    height: int | None = None


class UserCreationDraftRead(ReadModel):
    id: Identifier
    user_id: Identifier
    project_id: Identifier
    object_id: Identifier
    purpose: DraftPurpose
    model_config_id: Identifier | None
    input_text: str
    image_settings: DraftImageSettings | None
    video_settings: DraftVideoSettings | None
    reference_media_ids: list[Identifier]
    reference_images: list[DraftReferenceImage] = Field(default_factory=list)
    draft_version: Identifier
    source_version: Identifier | None
    row_version: Identifier
    created_at: datetime | None
    updated_at: datetime | None

    @field_serializer("created_at", "updated_at", when_used="json")
    def utc_time(self, value):
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z") if value else None
