"""Closed writing contracts; IDs and concurrency tokens serialize losslessly."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, field_serializer

from .base import Identifier, InputModel, ReadModel


def writing_text(value: str) -> str:
    if len(value.encode("utf-8")) > 1024**2:
        raise ValueError("正文不能超过 1 MiB（UTF-8）")
    return value


WritingText = Annotated[str, AfterValidator(writing_text)]


class WritingVersion(InputModel):
    content_version: Identifier


class NovelSave(WritingVersion):
    content: WritingText


class ScriptSave(NovelSave):
    script_id: Identifier | None


class ScriptSelect(WritingVersion):
    script_id: Identifier


class WritingDocument(ReadModel):
    id: Identifier
    content: str
    updated_at: datetime | None

    @field_serializer("updated_at", when_used="json")
    def utc_time(self, value: datetime | None):
        if value is None:
            return None
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class WritingScript(WritingDocument):
    state: Literal["unconfirmed", "confirmed"]


class WritingRead(ReadModel):
    episode_id: Identifier
    content_version: Identifier
    novel: WritingDocument | None
    editing_script: WritingScript | None
    confirmed_script_id: Identifier | None


class NovelSaved(ReadModel):
    content_version: Identifier
    novel: WritingDocument


class ScriptSaved(ReadModel):
    content_version: Identifier
    script: WritingScript


class ScriptCandidate(ReadModel):
    id: Identifier
    position: int
    state: Literal["unconfirmed", "confirmed"]
    preview: str
    is_editing: bool
    is_confirmed: bool
    generation_id: Identifier | None
    created_at: datetime | None
    updated_at: datetime | None

    @field_serializer("created_at", "updated_at", when_used="json")
    def utc_time(self, value: datetime | None):
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z") if value else None


class ScriptCandidateDetail(ScriptCandidate):
    content: str
