from typing import Annotated, Literal

from pydantic import Field

from short_drama.schemas.assistant.memory.memory import MemoryKind
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.canvas.document.canvas import SourceKey


class ReadTaskResults(InputModel):
    operation_id: Identifier
    ids: Annotated[list[SourceKey], Field(max_length=50)] = Field(default_factory=list)
    character_offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class DescribeTarget(InputModel):
    model_offset: Annotated[int, Field(ge=0, le=100000)] = 0
    model_limit: Annotated[int, Field(ge=1, le=20)] = 10
    result_before_id: Identifier | None = None
    result_limit: Annotated[int, Field(ge=1, le=20)] = 10


class ReadSelectedSubjects(InputModel):
    ids: Annotated[list[str], Field(max_length=16)] = Field(default_factory=list)
    character_offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class ReadReceipt(InputModel):
    operation_id: Identifier


class MemoryCatalog(InputModel):
    offset: Annotated[int, Field(ge=0, le=100000)] = 0
    limit: Annotated[int, Field(ge=1, le=50)] = 30


class SearchMemory(InputModel):
    query: Annotated[str, Field(min_length=1, max_length=120)]
    limit: Annotated[int, Field(ge=1, le=20)] = 12


class ReadMemory(InputModel):
    ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]
    versions: dict[str, Identifier] = Field(default_factory=dict)
    offset: Annotated[int, Field(ge=0, le=12000)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class ReadHistory(InputModel):
    from_seq: Annotated[int, Field(ge=1)] = 1
    to_seq: Annotated[int, Field(ge=1)] | None = None
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    character_offset: Annotated[int, Field(ge=0, le=60000)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class ReadResource(InputModel):
    ref_id: Annotated[str, Field(min_length=1, max_length=240)]
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    detail: Literal["low", "high", "original"] = "low"
    crop: tuple[int, int, int, int] | None = None
    offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class LoadSkill(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    content_version: Identifier
    purpose: Annotated[str, Field(min_length=1, max_length=240)]


class ReleaseSkill(InputModel):
    activation_id: Identifier


class RememberMemory(InputModel):
    scope_type: Literal["user", "conversation", "project"] = "conversation"
    kind: MemoryKind = "setting"
    memory_key: Annotated[str, Field(min_length=1, max_length=120)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=12000)]
    source_message_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]


class ChangeMemory(InputModel):
    memory_id: Identifier
    content_version: Identifier
    content: Annotated[str, Field(min_length=1, max_length=12000)] | None = None
    source_message_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]


SubjectKey = Annotated[str, Field(pattern=r"^subject:[a-f0-9]{64}$")]
