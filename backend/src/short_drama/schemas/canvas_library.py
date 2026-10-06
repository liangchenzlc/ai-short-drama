import re
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic.alias_generators import to_camel

from .base import Identifier, InputModel, parse_identifier
from .canvas import validate_document_json
from .canvas_asset_data import parse_asset_data


class CanvasLibraryDocument(InputModel):
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "image", "video", "audio", "model", "entity"]
    title: str = Field(max_length=255)
    cover_url: str = ""
    tags: list[str] = Field(default_factory=list)
    folder_id: str = Field(default="", max_length=80)
    category: str = Field(default="", max_length=32)
    status: Literal["draft", "review", "confirmed", "archived"] = "confirmed"
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    data: dict[str, JsonValue]

    @field_validator("id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("素材 ID 不能为空")
        return value

    @field_validator("kind", mode="before")
    @classmethod
    def trimmed_kind(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_content(self):
        validate_document_json(self.model_dump(mode="json", by_alias=True))
        extra = self.__pydantic_extra__ or {}
        for key in ("primaryVersionId", "source", "note", "arkAssetId"):
            if key in extra and not isinstance(extra[key], str):
                raise ValueError(f"素材字段 {key} 必须是字符串")
        if "portraitCertified" in extra and type(extra["portraitCertified"]) is not bool:
            raise ValueError("素材字段 portraitCertified 必须是布尔值")
        for key in ("primaryVersionId", "arkAssetId"):
            if key in extra:
                value = extra[key].strip()
                if value:
                    extra[key] = value
                else:
                    extra.pop(key)
        category = self.category.strip().lower()
        category = {
            "wardrobe": "prop",
            "weapon": "prop",
            "accessory": "prop",
            "style": "material",
        }.get(category, category)
        if not category and "category" not in self.model_fields_set:
            category = (
                "character"
                if self.kind == "entity"
                else ("other" if self.kind == "text" else "material")
            )
        if category not in {"character", "environment", "prop", "material", "other"}:
            raise ValueError("素材 category 无效")
        self.category = category
        self.data = parse_asset_data(self.kind, self.data)
        if self.kind in {"image", "video", "audio", "model"}:
            key = self.data.get("storageKey")
            if not isinstance(key, str) or not re.fullmatch(r"resource:[1-9][0-9]*", key):
                raise ValueError("media asset requires a durable resource")
            parse_identifier(key.removeprefix("resource:"))
        return self


class CanvasLibraryPut(InputModel):
    asset: CanvasLibraryDocument


class CanvasLibrarySummary(InputModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    id: str
    folder_id: str = ""
    kind: str
    category: str
    status: str
    title: str
    created_at: str
    updated_at: str


class CanvasLibraryPutRead(InputModel):
    asset: CanvasLibrarySummary


class CanvasLibraryDeleteRead(InputModel):
    id: str


class CanvasLibraryGetRead(InputModel):
    asset: CanvasLibraryDocument


class CanvasLibraryListRead(InputModel):
    assets: list[CanvasLibrarySummary]


class CanvasLibraryBatch(InputModel):
    ids: list[str] = Field(max_length=100)


class CanvasLibraryBatchRead(InputModel):
    assets: list[CanvasLibraryDocument]


class CanvasLibraryFilter(InputModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=40, ge=1, le=120)
    kind: str = ""
    category: str = ""
    folder_id: str | None = None
    uncategorized: bool = False
    status: str = ""
    q: str = Field(default="", max_length=512)
    favorite: bool = False
    recent: bool = False
    project: str = ""
    generated: bool = False


class CanvasLibraryPageRead(InputModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    assets: list[CanvasLibraryDocument]
    page: int
    page_size: int
    total: int
    has_more: bool
    kind_counts: dict[str, int]
    category_counts: dict[str, int]
    folder_counts: dict[str, int]
    favorite_total: int
    recent_total: int
    project_counts: dict[str, int]
    generated_total: int
    generated_kind_counts: dict[str, int]


class CanvasLibraryFolderWrite(InputModel):
    name: str = Field(max_length=1000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 40:
            raise ValueError("素材分类名称去除首尾空格后须为 1 至 40 个字符")
        return value


class CanvasLibraryFolderRead(InputModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    id: Identifier
    name: str
    position: int
    created_at: str
    updated_at: str


class CanvasLibraryFolderEnvelope(InputModel):
    folder: CanvasLibraryFolderRead


class CanvasLibraryFoldersRead(InputModel):
    folders: list[CanvasLibraryFolderRead]


class CanvasLibraryMove(InputModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    asset_ids: list[str] = Field(max_length=1000)
    folder_id: str = Field(default="", max_length=80)

    @field_validator("asset_ids")
    @classmethod
    def valid_ids(cls, values: list[str]) -> list[str]:
        result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not result or len(result) > 200 or any(len(value) > 80 for value in result):
            raise ValueError("一次须移动 1 至 200 个有效素材")
        return result
