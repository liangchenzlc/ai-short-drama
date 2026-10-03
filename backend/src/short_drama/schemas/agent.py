"""Public Agent contracts. Private runtime history is deliberately not a read DTO."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel

AgentRunStatus = Literal[
    "queued", "running", "waiting_generation", "waiting_review", "succeeded", "failed", "cancelled"
]
Title = Annotated[str, Field(min_length=1, max_length=120)]


class ConversationCreate(InputModel):
    project_id: Identifier
    episode_id: Identifier
    title: Title = "新对话"

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class ConversationPatch(InputModel):
    row_version: Annotated[int, Field(strict=True, ge=1, le=2**64 - 1)]
    title: Title | None = None
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("Title must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def require_change(self):
        changes = self.model_fields_set - {"row_version"}
        if not changes or ("archived" in changes and self.archived is None):
            raise ValueError("Supply a title or archived state")
        return self


class ConversationRead(ReadModel):
    id: Identifier
    project_id: Identifier
    episode_id: Identifier
    title: str
    row_version: int
    archived: bool
    created_at: datetime
    updated_at: datetime
    last_run_status: AgentRunStatus | None = None


class AgentStatus(ReadModel):
    enabled: bool
    schema_ready: bool
