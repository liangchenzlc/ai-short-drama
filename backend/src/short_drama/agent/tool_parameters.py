"""Pure tool names and input schemas, independent of registries and executors."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.schemas.agent_memory import MemoryKind
from short_drama.schemas.assistant_canvas import AssistantCanvasPatch
from short_drama.schemas.assistant_standard import StandardPatch
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.canvas import SourceKey
from short_drama.schemas.image_group import ImagePrimarySet

CANVAS_TOOL = "apply_canvas_patch"


class ApplyCanvasPatch(InputModel):
    request_message_id: Identifier
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    patch: AssistantCanvasPatch


IMAGE_READ_TOOL = "read_image_group"


IMAGE_CONTENT_TOOL = "read_image_content"


IMAGE_PRIMARY_TOOL = "set_image_group_primary"


SubjectKey = Annotated[str, Field(pattern=r"^subject:[a-f0-9]{64}$")]


class ReadImageGroup(InputModel):
    subject_key: SubjectKey | None = None
    operation_id: Identifier | None = None
    view: Literal["current", "detail", "history"] = "current"
    group_id: Identifier | None = None
    offset: Annotated[int, Field(ge=0, le=100000)] = 0
    limit: Annotated[int, Field(ge=1, le=10)] = 5
    character_offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000

    @model_validator(mode="after")
    def one_source(self):
        if (self.subject_key is None) == (self.operation_id is None):
            raise ValueError("Select exactly one subject or task media operation")
        if self.operation_id is not None and self.group_id is None:
            raise ValueError("A generated result requires its exact image group")
        return self


class ReadImageContent(InputModel):
    subject_key: SubjectKey | None = None
    operation_id: Identifier | None = None
    group_id: Identifier | None = None
    item_id: Identifier | None = None
    media_id: Identifier

    @model_validator(mode="after")
    def one_source(self):
        if (self.subject_key is None) == (self.operation_id is None):
            raise ValueError("Select exactly one subject or task media operation")
        if (self.group_id is None) != (self.item_id is None):
            raise ValueError("An image group and slot must be provided together")
        if self.subject_key is not None and self.group_id is None:
            raise ValueError("A selected subject requires its exact image group and slot")
        return self


class SetImageGroupPrimary(ImagePrimarySet):
    request_message_id: Identifier
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    subject_key: SubjectKey
    group_id: Identifier


MEDIA_TOOL = "submit_generation_batch"


MEDIA_STATUS_TOOL = "get_generation_status"


class MemoryRequest(InputModel):
    request_message_id: Identifier
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class RememberMemory(MemoryRequest):
    kind: MemoryKind
    memory_key: Annotated[str, Field(min_length=1, max_length=160)]
    title: Annotated[str, Field(min_length=1, max_length=200)]


class ChangeMemory(MemoryRequest):
    memory_id: Identifier
    content_version: Identifier


MEMORY_WRITE_TOOLS = {
    "remember_memory": (RememberMemory, "remember"),
    "update_memory": (ChangeMemory, "update"),
    "forget_memory": (ChangeMemory, "forget"),
    "restore_memory": (ChangeMemory, "restore"),
}


STANDARD_TOOL = "apply_standard_patch"


STANDARD_READ_TOOL = "read_task_results"


class ApplyStandardPatch(InputModel):
    request_message_id: Identifier
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    patch: StandardPatch


class ReadTaskResults(InputModel):
    operation_id: Identifier
    ids: Annotated[list[SourceKey], Field(max_length=50)] = Field(default_factory=list)
    character_offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    character_limit: Annotated[int, Field(ge=1, le=6000)] = 4000
