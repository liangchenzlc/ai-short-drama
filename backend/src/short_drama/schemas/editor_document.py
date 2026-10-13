"""受限 Quill Delta：只移除一个文档结尾标记，保留用户换行。"""

from typing import Annotated

from pydantic import AfterValidator, Field, StrictBool, StrictInt, model_validator

from .base import InputModel


class EditorAttributes(InputModel):
    header: Annotated[StrictInt, Field(ge=1, le=3)] | None = None
    bold: StrictBool | None = None
    italic: StrictBool | None = None
    blockquote: StrictBool | None = None


class EditorOperation(InputModel):
    insert: str
    attributes: EditorAttributes | None = None


class EditorDelta(InputModel):
    ops: list[EditorOperation] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def document_end(self):
        if not self.ops[-1].insert.endswith("\n"):
            raise ValueError("Delta 必须包含 Quill 文档结尾换行")
        text = "".join(op.insert for op in self.ops)
        if len(text.encode("utf-8")) > 1024**2 + 1:
            raise ValueError("正文不能超过 1 MiB（UTF-8）")
        return self


def validate_editor_document(value: dict) -> dict:
    EditorDelta.model_validate(value)
    return value


EditorDocument = Annotated[dict, AfterValidator(validate_editor_document)]


def editor_plain_text(document: dict) -> str:
    return "".join(op["insert"] for op in document["ops"])[:-1]
