"""完整画风快照合同；内部 revision 为语义版本，row_version 用十进制字符串。"""

import json
from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel

from .base import Identifier, InputModel, NonnegativeVersion
from .canvas_model_catalog import validate_public_json

Nonblank = Annotated[StrictStr, Field(min_length=1)]


class StyleModel(InputModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)

    @model_serializer(mode="wrap")
    def omit_absent_optional_fields(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class StyleParameters(StyleModel):
    sampler: StrictStr | None = None
    steps: Annotated[StrictInt, Field(ge=1, le=200)] | None = None
    cfg: Annotated[float, Field(strict=True, ge=0, le=50, allow_inf_nan=False)] | None = None
    size: StrictStr | None = None


class StyleLicense(StyleModel):
    commercial: StrictBool | None = None
    note: StrictStr | None = None
    source: StrictStr | None = None
    captured_at: StrictStr | None = None


class StyleAsset(StyleModel):
    id: Nonblank
    kind: Literal["lora", "template", "reference", "prompt"]
    provider: Nonblank
    title: Nonblank
    enabled: StrictBool | None = None
    source_id: StrictStr | None = None
    source_url: StrictStr | None = None
    model: StrictStr | None = None
    version: StrictStr | None = None
    base_models: list[StrictStr] | None = Field(default=None, max_length=20)
    trigger_words: list[StrictStr] | None = Field(default=None, max_length=50)
    prompt_fragment: StrictStr | None = None
    weight: Annotated[float, Field(strict=True, ge=0, le=2, allow_inf_nan=False)] | None = None
    parameters: StyleParameters | None = None
    reference_urls: list[StrictStr] | None = Field(default=None, max_length=20)
    reference_resource_ids: list[Identifier] | None = Field(default=None, max_length=20)
    status: Literal["draft", "validated", "unavailable"]
    license: StyleLicense | None = None

    @model_validator(mode="after")
    def complete(self):
        if not all(value.strip() for value in (self.id, self.provider, self.title)):
            raise ValueError("执行资产的 ID、平台和名称不能为空")
        if self.status == "validated":
            if self.kind == "lora" and not (self.source_id or self.source_url):
                raise ValueError("已验证 LoRA 缺少来源")
            if self.kind in {"prompt", "template"} and not (
                self.prompt_fragment or self.trigger_words
            ):
                raise ValueError("已验证提示词资产缺少内容")
            if self.kind == "reference" and not (
                self.reference_urls or self.reference_resource_ids
            ):
                raise ValueError("已验证参考资产缺少图片")
        return self


class CanvasStyleSnapshot(StyleModel):
    schema_version: Literal[1]
    preset_id: Nonblank
    title: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    description: Annotated[StrictStr, Field(max_length=500)]
    tags: list[StrictStr] = Field(max_length=20)
    prompt: Nonblank
    negative_prompt: Annotated[StrictStr, Field(max_length=64 * 1024)] | None = None
    cover_url: Annotated[StrictStr, Field(max_length=4096)] | None = None
    source_profile_id: StrictStr | None = None
    selection: dict[StrictStr, StrictStr] | None = Field(default=None, max_length=20)
    assets: list[StyleAsset] = Field(max_length=20)
    execution_policy: Literal["compatible-fallback", "strict-assets"] | None = None
    source: Literal["builtin", "user", "external"]
    revision: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError("schemaVersion 必须为整数 1")
        return value

    @model_validator(mode="after")
    def complete(self):
        if not all(value.strip() for value in (self.preset_id, self.title, self.prompt)):
            raise ValueError("画风名称、身份和 Prompt 不能为空")
        if self.selection and any(
            not key.strip() or len(key) > 64 or len(value) > 200
            for key, value in self.selection.items()
        ):
            raise ValueError("画风辅助标注格式无效")
        if len({asset.id for asset in self.assets}) != len(self.assets):
            raise ValueError("画风执行资产 ID 不能重复")
        if self.cover_url and not self.cover_url.lower().startswith(
            ("http://", "https://", "/", "data:image/")
        ):
            raise ValueError("画风封面地址无效")
        value = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        validate_public_json(value)
        if len(json.dumps(value, ensure_ascii=False).encode()) > 256 * 1024:
            raise ValueError("画风快照超过 256 KiB")
        return self


class CanvasStyleCreate(InputModel):
    profile_json: str = Field(alias="profileJson", min_length=1, max_length=256 * 1024)
    import_key: str | None = Field(default=None, min_length=1, max_length=128)


class CanvasStyleUpdate(InputModel):
    profile_json: str = Field(alias="profileJson", min_length=1, max_length=256 * 1024)
    expected_row_version: Identifier


class CanvasStyleVersion(InputModel):
    expected_row_version: Identifier


class CanvasStyleFavorite(CanvasStyleVersion):
    favorite: StrictBool


class CanvasSourceStyleWrite(InputModel):
    profile: CanvasStyleSnapshot
    expected_row_version: NonnegativeVersion


class CanvasSourceStyleRead(InputModel):
    profile: CanvasStyleSnapshot
    row_version: Identifier


class CanvasPersonalStyleRead(StyleModel):
    id: Identifier
    user_id: Identifier
    name: str
    description: str
    cover_url: str
    tags_json: str
    profile_json: str
    favorite: bool
    last_used_at: str | None = None
    revision: int
    row_version: Identifier
    created_at: str
    updated_at: str


class CanvasPersonalStylesRead(InputModel):
    profiles: list[CanvasPersonalStyleRead]


class CanvasPersonalStyleResult(InputModel):
    profile: CanvasPersonalStyleRead


class CanvasStyleActionRead(StyleModel):
    id: Identifier
    row_version: Identifier
    favorite: bool


class CanvasStylePreferencesRead(InputModel):
    favorites: list[str]
    recent: list[str]


class CanvasStylePreferenceWrite(InputModel):
    preset_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    action: Literal["favorite", "use"]
    favorite: StrictBool | None = None

    @model_validator(mode="after")
    def required_favorite(self):
        if not self.preset_id.strip() or (self.action == "favorite" and self.favorite is None):
            raise ValueError("收藏操作须指定风格与收藏状态")
        return self
