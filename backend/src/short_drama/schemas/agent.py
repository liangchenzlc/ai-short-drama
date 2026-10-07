"""Public Agent contracts. Private runtime history is deliberately not a read DTO."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel

AgentRunStatus = Literal[
    "queued", "running", "waiting_generation", "waiting_review", "succeeded", "failed", "cancelled"
]
Title = Annotated[str, Field(min_length=1, max_length=120)]
ConversationStage = Literal["source", "assets", "storyboard"]
ConversationSubject = Literal["episode", "asset", "shot"]
ConversationTask = Literal[
    "writing", "extraction", "planning", "batch", "creation", "image", "video"
]
SCOPE_TASKS = {
    ("source", "episode"): {"writing"},
    ("assets", "episode"): {"extraction", "batch"},
    ("assets", "asset"): {"creation", "image"},
    ("storyboard", "episode"): {"planning", "batch"},
    ("storyboard", "shot"): {"creation", "image", "video"},
}


class ConversationScope(InputModel):
    stage: ConversationStage
    subject_type: ConversationSubject
    subject_id: Identifier
    task_type: ConversationTask | None = None

    @model_validator(mode="after")
    def valid_combination(self):
        tasks = SCOPE_TASKS.get((self.stage, self.subject_type))
        if tasks is None or self.task_type is not None and self.task_type not in tasks:
            raise ValueError("Conversation stage, subject and task do not match")
        return self


class ConversationCreate(InputModel):
    project_id: Identifier
    episode_id: Identifier
    title: Title = "新对话"
    stage: ConversationStage | None = None
    subject_type: ConversationSubject | None = None
    subject_id: Identifier | None = None
    task_type: ConversationTask | None = None

    @model_validator(mode="after")
    def complete_scope(self):
        values = (self.stage, self.subject_type, self.subject_id, self.task_type)
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("Supply the complete conversation scope")
        ConversationScope.model_validate(
            self.model_dump(include={"stage", "subject_type", "subject_id", "task_type"})
        )
        if self.subject_type == "episode" and self.subject_id != self.episode_id:
            raise ValueError("Episode subject must match this episode")
        return self

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class ConversationPatch(InputModel):
    row_version: Identifier
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
    episode_id: Identifier | None
    title: str
    row_version: Identifier
    archived: bool
    created_at: datetime
    updated_at: datetime
    last_run_status: AgentRunStatus | None = None
    last_message_preview: str = ""
    stage: ConversationStage | None = None
    subject_type: ConversationSubject | None = None
    subject_id: Identifier | None = None
    task_type: ConversationTask | None = None
    scope_version: Literal[0, 1, 2] = 0


class AgentStatus(ReadModel):
    enabled: bool
    schema_ready: bool
