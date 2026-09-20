from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    ReadModel,
)


class NovelScriptRecordCreate(InputModel):
    novel_id: Identifier
    script_id: Identifier
    batch_id: Identifier
    model_id: Identifier | None = None


class NovelScriptRecordRead(ReadModel):
    id: Identifier
    novel_id: Identifier
    script_id: Identifier
    batch_id: Identifier
    model_id: Identifier | None = None
    created_at: datetime | None = None
    created_by: Identifier | None = None
