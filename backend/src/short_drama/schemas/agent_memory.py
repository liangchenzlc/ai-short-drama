"""Explicit memory scopes and version-bound edits; all public identities remain strings."""

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, ReadModel

MemoryScopeType = Literal["user", "conversation", "project"]
MemoryKind = Literal["preference", "correction", "setting", "decision", "reference"]
MemoryText = Annotated[str, Field(min_length=1, max_length=12000)]
Labels = Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=16)]


def normalize_memory_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


class MemoryCreate(InputModel):
    scope_type: MemoryScopeType
    conversation_id: Identifier | None = None
    project_id: Identifier | None = None
    kind: MemoryKind
    memory_key: Annotated[str, Field(min_length=1, max_length=160)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content: MemoryText
    aliases: Labels = Field(default_factory=list)
    tags: Labels = Field(default_factory=list)
    pinned: bool = False
    source_message_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(
        default_factory=list
    )
    source_conversation_id: Identifier | None = None

    @field_validator("memory_key")
    @classmethod
    def normalize_key(cls, value):
        result = normalize_memory_key(value)
        if not result or len(result) > 160 or "\x00" in result:
            raise ValueError("Memory key is empty or invalid")
        return result

    @field_validator("title", "content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Memory text cannot be blank")
        return value.strip()

    @field_validator("aliases", "tags")
    @classmethod
    def normalize_labels(cls, values):
        result = list(dict.fromkeys(normalize_memory_key(value) for value in values))
        if any(not value or "\x00" in value for value in result):
            raise ValueError("Memory labels cannot be blank")
        return result

    @model_validator(mode="after")
    def scope_fields(self):
        valid = {
            "user": self.conversation_id is None and self.project_id is None,
            "conversation": self.conversation_id is not None and self.project_id is None,
            "project": self.project_id is not None and self.conversation_id is None,
        }
        if not valid[self.scope_type]:
            raise ValueError("Memory scope fields do not match")
        if bool(self.source_message_ids) != bool(self.source_conversation_id):
            raise ValueError("Memory message sources require their conversation")
        if len(set(self.source_message_ids)) != len(self.source_message_ids):
            raise ValueError("Memory message sources must be unique")
        return self


class MemoryPatch(InputModel):
    content_version: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    content: MemoryText | None = None
    aliases: Labels | None = None
    tags: Labels | None = None
    pinned: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set - {"content_version"}:
            raise ValueError("Memory update is empty")
        for name in self.model_fields_set - {"content_version"}:
            value = getattr(self, name)
            if value is None or isinstance(value, str) and (not value.strip() or "\x00" in value):
                raise ValueError("Memory update has an empty field")
        return self


class MemoryVersion(InputModel):
    content_version: Identifier


class MemoryRead(ReadModel):
    id: Identifier
    scope_type: MemoryScopeType
    conversation_id: Identifier | None
    project_id: Identifier | None
    kind: MemoryKind
    memory_key: str
    title: str
    content: str
    aliases: list[str]
    tags: list[str]
    pinned: bool
    state: Literal["active", "forgotten", "superseded"]
    origin: Literal["explicit", "extracted"]
    content_version: Identifier
    sources: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SummaryFact(InputModel):
    kind: Literal["user_fact", "proposal", "completed", "pending"]
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    source_message_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(
        default_factory=list
    )
    prior_fact_indexes: Annotated[
        list[Annotated[int, Field(strict=True, ge=0, le=23)]], Field(max_length=24)
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def real_unique_sources(self):
        if not self.source_message_ids and not self.prior_fact_indexes:
            raise ValueError("Summary fact requires a source")
        for values in (self.source_message_ids, self.prior_fact_indexes):
            if len(set(values)) != len(values):
                raise ValueError("Summary sources cannot repeat")
        return self


class MemoryExtraction(InputModel):
    kind: MemoryKind
    memory_key: Annotated[str, Field(min_length=1, max_length=160)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=2000)]
    aliases: Labels = Field(default_factory=list)
    tags: Labels = Field(default_factory=list)
    source_message_ids: Annotated[list[Identifier], Field(min_length=1, max_length=16)]


class MemoryMaintenanceResult(InputModel):
    facts: Annotated[list[SummaryFact], Field(max_length=24)] = Field(default_factory=list)
    memories: Annotated[list[MemoryExtraction], Field(max_length=12)] = Field(default_factory=list)


class TaskConstraintFact(InputModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    source_message_ids: Annotated[list[Identifier], Field(max_length=128)] = Field(
        default_factory=list
    )
    prior_fact_indexes: Annotated[
        list[Annotated[int, Field(strict=True, ge=0, le=23)]], Field(max_length=24)
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def real_unique_sources(self):
        if not self.source_message_ids and not self.prior_fact_indexes:
            raise ValueError("Task constraint requires a source")
        if any(
            len(set(values)) != len(values)
            for values in (self.source_message_ids, self.prior_fact_indexes)
        ):
            raise ValueError("Task constraint sources cannot repeat")
        return self


class TaskConstraintResult(InputModel):
    facts: Annotated[list[TaskConstraintFact], Field(max_length=24)] = Field(default_factory=list)


class MemorySummaryFactRead(ReadModel):
    kind: Literal["user_fact", "proposal", "completed", "pending"]
    text: str
    source_message_ids: list[Identifier]
    subject_keys: list[str]
    prior_fact_indexes: list[int] = Field(default_factory=list)
    source_summary_id: Identifier | None = None


class MemorySummaryRead(ReadModel):
    id: Identifier
    base_summary_id: Identifier | None = None
    from_seq: int
    to_seq: int
    facts: list[MemorySummaryFactRead]
    token_estimate: int
    created_at: datetime


class MemoryJobRead(ReadModel):
    id: Identifier
    kind: Literal["summary", "extract", "task_state", "purge"]
    status: Literal["pending", "running", "succeeded", "failed", "stale", "cancelled", "abandoned"]
    provider_state: Literal["not_started", "sending", "received", "unknown"]
    message_version: Identifier
    from_seq: int
    to_seq: int
    attempts: int
    error: dict | None
    usage: dict
    created_at: datetime
    finished_at: datetime | None
    retry_job_id: Identifier | None = None


class MemoryJobRetry(InputModel):
    message_version: Identifier


class MemoryContextRead(ReadModel):
    conversation_id: Identifier
    covers_until_seq: int
    summary_generation: NonnegativeVersion
    summary: MemorySummaryRead | None
    jobs: list[MemoryJobRead]


class MemoryClarificationChoice(InputModel):
    content_version: Identifier
    choice: Literal["keep_current", "use_candidate"]


class MemoryClarificationRead(ReadModel):
    index: int
    memory_id: Identifier
    content_version: Identifier
    candidate_base_version: Identifier
    title: str
    current_content: str | None
    candidate_content: str | None
    source_message_ids: list[Identifier]
    status: Literal["pending", "resolved", "changed", "unavailable"]
    choice: Literal["keep_current", "use_candidate"] | None


class MemoryReviewJobRead(ReadModel):
    id: Identifier
    conversation_id: Identifier
    from_seq: int
    to_seq: int
    clarifications: list[MemoryClarificationRead]
