"""Closed writing contracts; IDs and concurrency tokens serialize losslessly."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_serializer

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, ReadModel


def writing_text(value: str) -> str:
    if len(value.encode("utf-8")) > 1024**2:
        raise ValueError("正文不能超过 1 MiB（UTF-8）")
    return value


WritingText = Annotated[str, AfterValidator(writing_text)]


class WritingVersion(InputModel):
    content_version: Identifier


class StorySettingsSave(InputModel):
    row_version: NonnegativeVersion
    logline: str = Field(max_length=200)
    synopsis: str = Field(max_length=20000)
    genre: str = Field(max_length=100)
    character_bios: str = Field(max_length=20000)


class StorySettingsRead(ReadModel):
    row_version: NonnegativeVersion = 0
    logline: str = ""
    synopsis: str = ""
    genre: str = ""
    character_bios: str = ""


class ScriptSave(WritingVersion):
    content: WritingText
    script_id: Identifier | None
    change_source: Literal["edit", "import"] = "edit"


class WritingDocument(ReadModel):
    id: Identifier
    content: str
    updated_at: datetime | None = None

    @field_serializer("updated_at", when_used="json")
    def utc_time(self, value: datetime | None):
        if value is None:
            return None
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class WritingScript(WritingDocument):
    state: Literal["unconfirmed", "confirmed"]


class WritingRead(ReadModel):
    project_id: Identifier
    content_version: Identifier
    editing_script: WritingScript | None
    confirmed_script_id: Identifier | None
    active_writing_tasks: list["WritingTask"] = []


class WritingTask(ReadModel):
    task_id: Identifier
    target: Literal["script"]
    operation: Literal["script_rewrite"]
    status: Literal["queued", "running"]


class ScriptSaved(ReadModel):
    content_version: Identifier
    script: WritingScript
    confirmed_script_id: Identifier | None


class ScriptRevisionRead(ReadModel):
    id: Identifier
    script_id: Identifier
    before_version: Identifier
    after_version: Identifier
    source: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def utc_time(self, value: datetime):
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class ScriptRevisionDetail(ScriptRevisionRead):
    before_content: str
    after_content: str
