from datetime import datetime
from typing import Literal

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt32,
    ReadModel,
)
from .editor_document import EditorDocument


class ProjectScriptCreate(InputModel):
    project_id: Identifier
    position: PositiveUInt32
    content: MediumText = ""


class ProjectScriptUpdate(InputModel):
    position: PositiveUInt32 = None
    content: MediumText = None


class ProjectScriptRead(ReadModel):
    published_at: datetime | None = None
    id: Identifier
    project_id: Identifier
    position: PositiveUInt32
    content: MediumText
    editor_document: EditorDocument | None = None
    state: Literal["unconfirmed", "confirmed"]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
    confirmed_project_id: Identifier | None = None
