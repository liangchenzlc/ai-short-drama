"""Display contracts for durable Agent actions; no private SDK history escapes."""

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.agent import AgentRunStatus, ConversationScope
from short_drama.schemas.agent_context import ModelInputCapabilities, SkillSelection
from short_drama.schemas.base import Identifier, InputModel, ReadModel

TaskKind = Literal[
    "novel", "script", "extract", "storyboard", "asset_patch", "shot_patch", "image", "video"
]


class TaskSpec(InputModel):
    kind: TaskKind
    target_id: Identifier | None = None
    instructions: Annotated[str, Field(min_length=1, max_length=12000)]
    model_config_id: Identifier | None = None
    count: Annotated[int, Field(strict=True, ge=1, le=4)] = 1
    parameters: dict = Field(default_factory=dict)

    @field_validator("instructions")
    @classmethod
    def clean_instructions(cls, value):
        if not value.strip():
            raise ValueError("Task instructions must not be blank")
        return value.strip()

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value):
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 16384:
            raise ValueError("Task parameters are too large")
        return value

    @model_validator(mode="after")
    def check_quantity(self):
        if self.kind not in {"image", "video"} and self.count != 1:
            raise ValueError("Text tasks have a single candidate")
        return self


class MessageCreate(InputModel):
    content: Annotated[str, Field(min_length=1, max_length=32000)]
    mode: Literal["auto", "discuss", "generate"] = "auto"
    expected_scope: ConversationScope | None = None
    model_config_id: Identifier | None = None
    task: TaskSpec | None = None
    attachment_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(default_factory=list)
    skills: Annotated[list[SkillSelection], Field(max_length=8)] = Field(default_factory=list)
    video_audio: Literal["include", "visual_only"] = "include"

    @field_validator("content")
    @classmethod
    def clean_content(cls, value):
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def task_requires_generation(self):
        if self.task is not None and self.mode == "discuss":
            raise ValueError("Discussion does not authorize a creative task")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Attachments must be unique")
        if len({item.id for item in self.skills}) != len(self.skills):
            raise ValueError("Skills must be unique")
        return self


class MessageRead(ReadModel):
    id: Identifier
    seq: int
    role: Literal["user", "assistant"]
    content: str
    references: list[dict]
    artifacts: list[dict]
    created_at: datetime


class PlanProposal(InputModel):
    title: Annotated[str, Field(min_length=1, max_length=120)]
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    steps: Annotated[list[TaskSpec], Field(min_length=1, max_length=12)]


class PlanReviewRead(ReadModel):
    tool_call_id: Identifier
    review_version: Identifier
    review_hash: str
    title: str
    summary: str
    steps: list[dict]


class RunRead(ReadModel):
    id: Identifier
    conversation_id: Identifier
    status: AgentRunStatus
    phase: Literal["model", "tools", "wait"]
    row_version: Identifier
    mode: Literal["auto", "discuss", "single", "workflow"]
    model_config_id: Identifier
    model_name: str
    error: dict | None
    usage: dict
    budget: dict
    review: PlanReviewRead | None
    awaiting_artifact_ids: list[Identifier] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    queue_position: int = 0
    waiting_reason: str | None = None


class ConversationRuntimeState(ReadModel):
    conversation_id: Identifier
    cursor: int
    resume_cursor: int = 0
    active_run: RunRead | None
    queued_runs: list[RunRead]


class MessageAccepted(ReadModel):
    message: MessageRead
    run: RunRead
    cursor: int


class ReviewDecision(InputModel):
    review_version: Identifier
    review_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    decision: Literal["approved", "rejected"]


class RunContinue(InputModel):
    artifact_id: Identifier
    artifact_row_version: Identifier


class ModelVerify(InputModel):
    row_version: Identifier


class AgentModelRead(ReadModel):
    id: Identifier
    name: str
    model_key: str
    row_version: Identifier
    protocol: str | None
    tool_calling: bool
    tool_result_continuation: bool
    streaming: Literal["verified", "not_tested"]
    verified: bool
    preferred: bool = False
    input_capabilities: ModelInputCapabilities = Field(default_factory=ModelInputCapabilities)


class AgentModelsRead(ReadModel):
    items: list[AgentModelRead]
    preferred_id: Identifier | None


class EventRead(ReadModel):
    seq: int
    event_type: str
    run_id: Identifier | None
    payload: dict
    created_at: datetime
