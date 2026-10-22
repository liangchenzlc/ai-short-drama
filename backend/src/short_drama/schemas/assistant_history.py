"""Versioned, idempotent pure-discussion editing; no business execution input."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel


class DiscussionRevisionRequest(InputModel):
    kind: Literal["edit", "regenerate"]
    row_version: Identifier
    content: Annotated[str, Field(min_length=1, max_length=32000)] | None = None

    @model_validator(mode="after")
    def validate_edit(self):
        if self.kind == "edit":
            if not self.content or not self.content.strip():
                raise ValueError("修订正文不能为空")
            self.content = self.content.strip()
        elif self.content is not None:
            raise ValueError("重新回答不能同时修改输入")
        return self


class DiscussionEligibility(ReadModel):
    allowed: bool
    code: str | None = None
    reason: str | None = None
    row_version: Identifier
    current_content: str


class DiscussionRevisionRead(ReadModel):
    id: Identifier
    kind: Literal["edit", "regenerate"]
    row_version: Identifier
    content: str
    source_message_id: Identifier
    source_version: Identifier
    run_id: Identifier
    created_at: datetime
