from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    ReadModel,
)


class ScriptShotRecordCreate(InputModel):
    script_id: Identifier
    shot_id: Identifier
    batch_id: Identifier
    model_id: Identifier | None = None


class ScriptShotRecordRead(ReadModel):
    id: Identifier
    script_id: Identifier
    shot_id: Identifier
    batch_id: Identifier
    model_id: Identifier | None = None
    created_at: datetime | None = None
    created_by: Identifier | None = None
