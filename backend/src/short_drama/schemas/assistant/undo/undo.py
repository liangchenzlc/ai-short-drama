"""A preview is not consent: clients submit an exact version and explicit item selection."""

from datetime import datetime
from typing import Annotated

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel

UndoKey = Annotated[str, Field(min_length=1, max_length=240)]


class BatchUndoRequest(InputModel):
    preview_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    selected_keys: Annotated[list[UndoKey], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique_selection(self):
        if len(set(self.selected_keys)) != len(self.selected_keys):
            raise ValueError("撤销范围不能重复")
        return self


class BatchUndoItem(ReadModel):
    key: str
    kind: str
    id: str
    name: str
    allowed: bool
    reason: str | None = None
    requires: list[str] = Field(default_factory=list)


class BatchUndoRead(ReadModel):
    id: Identifier
    operation_id: Identifier
    selected_keys: list[str]
    result_refs: list[dict]
    created_at: datetime


class BatchUndoPreview(ReadModel):
    operation_id: Identifier
    preview_hash: str
    items: list[BatchUndoItem]
    receipts: list[BatchUndoRead]
