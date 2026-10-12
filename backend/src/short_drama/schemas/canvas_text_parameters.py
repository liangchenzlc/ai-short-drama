"""仅由画布准入事务生成的文字媒体引用，不扩大公共 TextInput。"""

from typing import Literal

from pydantic import Field, model_validator

from .base import Identifier, InputModel


class CanvasTextReference(InputModel):
    media_id: Identifier
    mime_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    byte_size: int = Field(strict=True, gt=0)


class CanvasTextVideoReference(InputModel):
    media_id: Identifier
    mime_type: Literal["video/mp4", "video/webm", "video/quicktime"]
    byte_size: int = Field(strict=True, gt=0)
    duration_ms: int = Field(strict=True, gt=0)


class CanvasTextReferences(InputModel):
    mode: Literal["text"]
    references: list[CanvasTextReference] = Field(default_factory=list, max_length=16)
    videos: list[CanvasTextVideoReference] | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def has_saved_media(self):
        if not self.references and not self.videos:
            raise ValueError("Canvas text references must contain saved media")
        return self
