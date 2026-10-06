"""仅由画布准入事务生成的文字图片引用，不扩大公共 TextInput。"""

from typing import Literal

from pydantic import Field

from .base import Identifier, InputModel


class CanvasTextReference(InputModel):
    media_id: Identifier
    mime_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    byte_size: int = Field(strict=True, gt=0)


class CanvasTextReferences(InputModel):
    mode: Literal["text"]
    references: list[CanvasTextReference] = Field(min_length=1, max_length=16)
