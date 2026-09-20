from datetime import datetime

from .base import (
    Identifier,
    InputModel,
    MediumText,
    ReadModel,
)


class EpisodeNovelCreate(InputModel):
    episode_id: Identifier
    content: MediumText = ""


class EpisodeNovelUpdate(InputModel):
    content: MediumText = None


class EpisodeNovelRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    content: MediumText
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
