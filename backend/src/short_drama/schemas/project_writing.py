"""Closed writing contracts; IDs and concurrency tokens serialize losslessly."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_serializer, model_validator

from .base import Identifier, InputModel, NonnegativeVersion, ReadModel
from .editor_document import EditorDocument, editor_plain_text


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


class NovelSave(WritingVersion):
    content: WritingText


class OutlineSave(NovelSave):
    pass


class ScriptSave(NovelSave):
    script_id: Identifier | None
    editor_document: EditorDocument | None = None
    change_source: Literal["edit", "import"] = "edit"

    @model_validator(mode="after")
    def matching_editor_text(self):
        if (
            self.editor_document is not None
            and editor_plain_text(self.editor_document) != self.content
        ):
            raise ValueError("Delta 纯文本必须与 content 一致")
        return self


class ScriptSelect(WritingVersion):
    script_id: Identifier


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
    editor_document: EditorDocument | None = None


class WritingRead(ReadModel):
    project_id: Identifier
    content_version: Identifier
    novel: WritingDocument | None
    outline: WritingDocument | None
    editing_script: WritingScript | None
    confirmed_script_id: Identifier | None
    active_writing_tasks: list["WritingTask"] = []


class WritingTask(ReadModel):
    task_id: Identifier
    target: Literal["outline", "script"]
    operation: Literal["story_outline", "script_generate", "script_rewrite"]
    status: Literal["queued", "running"]


class NovelSaved(ReadModel):
    content_version: Identifier
    novel: WritingDocument


class OutlineSaved(ReadModel):
    content_version: Identifier
    outline: WritingDocument


class ScriptSaved(ReadModel):
    content_version: Identifier
    script: WritingScript
    confirmed_script_id: Identifier | None


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
