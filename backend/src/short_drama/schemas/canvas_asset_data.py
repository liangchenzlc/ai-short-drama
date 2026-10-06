"""BeefTV lib/asset-record.ts 的六类素材内容合同，不伪造缺失媒体元数据。"""

import math
import re
from typing import Annotated, ClassVar

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from .base import InputModel


def finite_number(value: object) -> int | float:
    if type(value) not in {int, float}:
        raise ValueError("素材数字字段必须是有限数字，不能是字符串或布尔值")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("素材数字字段必须是有限数字")
    return value


Number = Annotated[int | float, BeforeValidator(finite_number)]
Nonnegative = Annotated[Number, Field(ge=0)]
Positive = Annotated[Number, Field(gt=0)]


class AssetData(InputModel):
    # The source parser returns only the known fields for each data kind.
    model_config = ConfigDict(
        extra="ignore", strict=True, alias_generator=to_camel, populate_by_name=True
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if isinstance(value, dict):
            for name, field in cls.model_fields.items():
                for key in {name, field.alias}:
                    if key in value and value[key] is None:
                        raise ValueError(f"素材 data.{key} 不能是 null；未知可选字段应省略")
        return value


class TextData(AssetData):
    content: str


class EntityData(AssetData):
    definition: dict[str, JsonValue]


class MediaData(AssetData):
    media_kind: ClassVar[str | None] = None
    storage_key: str | None = None
    bytes: Nonnegative
    mime_type: str

    @field_validator("mime_type")
    @classmethod
    def concrete_mime(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", value):
            raise ValueError("素材 data.mimeType 必须是具体 MIME 类型")
        if (
            cls.media_kind
            and value != "application/octet-stream"
            and not value.startswith(cls.media_kind + "/")
        ):
            raise ValueError(f"素材 data.mimeType 与 {cls.media_kind} 类型不匹配")
        return value


class ImageData(MediaData):
    media_kind = "image"
    data_url: str
    width: Positive
    height: Positive


class VideoData(MediaData):
    media_kind = "video"
    url: str
    width: Nonnegative
    height: Nonnegative
    duration_ms: Nonnegative | None = None
    has_audio: bool | None = None


class AudioData(MediaData):
    media_kind = "audio"
    url: str
    duration_ms: Nonnegative | None = None


class ModelData(MediaData):
    url: str
    file_name: str

    @field_validator("file_name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("素材 data.fileName 不能为空")
        return value


ASSET_DATA_MODELS = {
    "text": TextData,
    "image": ImageData,
    "video": VideoData,
    "audio": AudioData,
    "model": ModelData,
    "entity": EntityData,
}


def parse_asset_data(kind: str, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return (
        ASSET_DATA_MODELS[kind]
        .model_validate(value)
        .model_dump(mode="json", by_alias=True, exclude_none=True)
    )
