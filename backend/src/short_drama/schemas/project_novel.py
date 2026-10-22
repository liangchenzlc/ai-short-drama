from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    MediumText,
    ReadModel,
)


class ProjectNovelCreate(InputModel):
    project_id: Identifier
    content: MediumText = ""


class ProjectNovelUpdate(InputModel):
    content: MediumText = None


class ProjectNovelRead(ReadModel):
    id: Identifier
    project_id: Identifier
    content: MediumText
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
