from datetime import datetime
from typing import Literal

from .base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt32,
    ReadModel,
)


class EpisodeScriptCreate(InputModel):
    episode_id: Identifier
    position: PositiveUInt32
    content: MediumText = ""


class EpisodeScriptUpdate(InputModel):
    position: PositiveUInt32 = None
    content: MediumText = None


class EpisodeScriptRead(ReadModel):
    id: Identifier
    episode_id: Identifier
    position: PositiveUInt32
    content: MediumText
    state: Literal["unconfirmed", "confirmed"]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
    confirmed_episode_id: Identifier | None = None
