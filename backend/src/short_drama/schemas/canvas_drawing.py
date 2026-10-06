"""保留原版绘图字段；数据库版本对外始终是十进制字符串。"""

import json
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .base import UINT64_MAX, Identifier, NonnegativeVersion
from .canvas import SourceKey, SourceModel, validate_document_json


class CanvasDrawingRender(SourceModel):
    resource_id: Identifier | None = Field(default=None, alias="resourceId")
    page_id: str = Field(default="", alias="pageId", max_length=128)
    width: Annotated[int, Field(strict=True, ge=0)] = 0
    height: Annotated[int, Field(strict=True, ge=0)] = 0
    mime_type: str = Field(default="", alias="mimeType", max_length=128)
    background: Literal["white", ""] = ""
    storage_key: str = Field(default="", alias="storageKey", max_length=128)

    @field_validator("resource_id", mode="before")
    @classmethod
    def empty_resource(cls, value):
        return None if value == "" else value


class CanvasDrawingWrite(SourceModel):
    drawing_id: SourceKey = Field(alias="drawingId")
    engine: Literal["excalidraw"] = "excalidraw"
    revision: NonnegativeVersion
    snapshot: JsonValue = Field(default_factory=dict)
    shape_count: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)] = Field(
        default=0, alias="shapeCount"
    )
    page_count: Annotated[int, Field(strict=True)] = Field(default=1, alias="pageCount")
    preview_resource_id: Identifier | None = Field(default=None, alias="previewResourceId")
    render: CanvasDrawingRender | None = None

    @field_validator("preview_resource_id", mode="before")
    @classmethod
    def empty_preview(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.revision == UINT64_MAX:
            raise ValueError("drawing version is exhausted")
        self.page_count = 1
        validate_document_json(self.snapshot)
        if len(json.dumps(self.snapshot, ensure_ascii=False).encode("utf-8")) > 32 * 1024**2:
            raise ValueError("drawing snapshot exceeds 32 MiB")
        if isinstance(self.snapshot, dict) and isinstance(self.snapshot.get("files"), dict):
            for file in self.snapshot["files"].values():
                if isinstance(file, dict):
                    url = file.get("dataURL")
                    if isinstance(url, str) and url.strip().lower().startswith("blob:"):
                        raise ValueError("drawing image bytes must be embedded or persisted")
        return self


class CanvasDrawingRequest(SourceModel):
    drawing: CanvasDrawingWrite


class CanvasDrawingRead(SourceModel):
    drawing_id: SourceKey = Field(alias="drawingId")
    engine: Literal["excalidraw"]
    revision: Identifier
    snapshot: JsonValue = None
    shape_count: int = Field(alias="shapeCount")
    page_count: Literal[1] = Field(alias="pageCount")
    preview_resource_id: Identifier | None = Field(default=None, alias="previewResourceId")
    render: CanvasDrawingRender | None = None
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class CanvasDrawingEnvelope(SourceModel):
    drawing: CanvasDrawingRead


class CanvasDrawingList(SourceModel):
    drawings: list[CanvasDrawingRead]


class CanvasDrawingDelete(SourceModel):
    id: SourceKey
    revision: Identifier
