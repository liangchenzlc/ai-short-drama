"""保留 BeefTV 文件夹接口的 camelCase 与不透明客户端 ID。"""

from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from .base import Identifier, InputModel, parse_identifier

FolderKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class CanvasFolderWrite(InputModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    id: FolderKey | None = None
    name: str = "未命名文件夹"
    cover_resource_id: Identifier | None = None
    created_at: str | None = Field(default=None, max_length=64)
    updated_at: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        value = value.strip() or "未命名文件夹"
        if len(value) > 80:
            raise ValueError("文件夹名称过长")
        return value

    @field_validator("cover_resource_id", mode="before")
    @classmethod
    def optional_resource(cls, value):
        if isinstance(value, str):
            value = value.strip()
        return parse_identifier(value) if value not in (None, "") else None


class CanvasFolderPut(InputModel):
    folder: CanvasFolderWrite


class CanvasFolderRead(InputModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    id: FolderKey
    name: str
    cover_resource_id: Identifier | None = None
    created_at: str
    updated_at: str


class CanvasFolderEnvelope(InputModel):
    folder: CanvasFolderRead


class CanvasFoldersRead(InputModel):
    folders: list[CanvasFolderRead]


class CanvasFolderDeleted(InputModel):
    id: FolderKey
