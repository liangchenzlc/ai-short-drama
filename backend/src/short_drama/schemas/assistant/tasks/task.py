"""Global conversation, logical task and durable tool/control display contracts."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.assistant.context.inputs import AssistantMessageCreate
from short_drama.schemas.assistant.conversations.engine import Title
from short_drama.schemas.assistant.operations.runtime import MessageRead, RunRead
from short_drama.schemas.assistant.operations.script import ScriptReference
from short_drama.schemas.base import (
    Identifier,
    InputModel,
    NonnegativeVersion,
    ReadModel,
    parse_identifier,
)
from short_drama.schemas.common import PageResponse

TaskStatus = Literal[
    "queued", "running", "waiting_media", "succeeded", "stopped", "paused_error", "paused_budget"
]
TaskPhase = Literal[
    "deciding",
    "tool_running",
    "waiting_media",
    "settling_result",
    "reconciling",
    "at_boundary",
    "terminal",
]


class ConversationTitle(InputModel):
    title: Title = "新对话"

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value.strip()


class GlobalConversationCreate(ConversationTitle):
    project_id: Identifier | None = None


class GlobalConversationPatch(ConversationTitle):
    row_version: Identifier
    title: Title


class MemoryCleanupSelection(InputModel):
    id: Identifier
    content_version: Identifier


class GlobalConversationDelete(InputModel):
    row_version: Identifier
    clear_derived_memory: bool = False
    memory_selections: Annotated[list[MemoryCleanupSelection], Field(max_length=200)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def selected_memory_versions(self):
        if self.memory_selections and not self.clear_derived_memory:
            raise ValueError("Memory cleanup selections require explicit cleanup")
        if len({item.id for item in self.memory_selections}) != len(self.memory_selections):
            raise ValueError("Memory cleanup selections must be unique")
        return self


class DeletionMemoryRead(ReadModel):
    id: Identifier
    content_version: Identifier
    scope_type: Literal["user", "project"]
    project_id: Identifier | None
    title: str
    content_preview: str
    protection_reasons: list[Literal["user_edited", "independent_sources"]]


class DeletionMemoryPreview(ReadModel):
    conversation_id: Identifier
    row_version: Identifier
    items: list[DeletionMemoryRead]
    total: int
    offset: int
    limit: int


class TaskSubjectReference(InputModel):
    project_id: Identifier
    source_kind: Literal["project", "canvas"]
    source_id: Annotated[str, Field(min_length=1, max_length=128)]
    kind: Literal["asset", "shot", "node", "image_group", "image_slot", "sound_line"]
    id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: Identifier
    content_revision: Identifier | None = None
    checksum: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def matching_subject(self):
        if (self.kind == "node") != (self.source_kind == "canvas"):
            raise ValueError("Subject must belong to the source kind")
        if self.source_kind == "project" and parse_identifier(self.source_id) != self.project_id:
            raise ValueError("Subject source must identify its owning project")
        if self.kind == "sound_line" and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.id):
            raise ValueError("Sound lines require a real saved line identifier")
        if self.kind not in {"node", "sound_line"}:
            parse_identifier(self.id)
        if self.kind != "node" and self.content_revision is not None:
            raise ValueError("Only canvas nodes have a separate content revision")
        return self


class TaskSubjectSelection(InputModel):
    expected_input_revision: NonnegativeVersion
    retain_keys: Annotated[list[str], Field(max_length=16)] = Field(default_factory=list)
    subjects: Annotated[list[TaskSubjectReference], Field(max_length=16)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def bounded_unique_selection(self):
        if len(self.retain_keys) + len(self.subjects) > 16:
            raise ValueError("A task can select at most 16 subjects")
        if any(not key.startswith("subject:") or len(key) != 72 for key in self.retain_keys):
            raise ValueError("Retained subjects require real subject keys")
        if len(set(self.retain_keys)) != len(self.retain_keys):
            raise ValueError("Retained subjects must be unique")
        keys = [(s.project_id, s.source_kind, s.source_id, s.kind, s.id) for s in self.subjects]
        if len(set(keys)) != len(keys):
            raise ValueError("Selected subjects must be unique")
        return self


class TaskSubjectLocation(ReadModel):
    project_id: Identifier
    kind: Literal["asset", "shot", "node", "sound_line"]
    id: str
    canvas_id: str | None = None
    image_group_id: Identifier | None = None
    image_slot_id: Identifier | None = None


class TaskSubjectRead(ReadModel):
    subject_key: str
    snapshot_id: str
    project_id: Identifier
    project_name: str
    source_kind: Literal["project", "canvas"]
    source_id: str
    kind: Literal["asset", "shot", "node", "image_group", "image_slot", "sound_line"]
    id: str
    revision: Identifier
    content_revision: Identifier | None = None
    checksum: str
    name: str
    allowed_fields: list[str]
    source_location: TaskSubjectLocation | None = None


class TaskSubjectStatus(TaskSubjectRead):
    availability: Literal["current", "changed", "unavailable"]
    current_revision: Identifier | None = None
    current_content_revision: Identifier | None = None
    current_checksum: str | None = None


class TaskSubjectMetadata(ReadModel):
    task_id: Identifier
    input_revision: NonnegativeVersion
    applied_input_revision: NonnegativeVersion
    pending: bool
    items: list[TaskSubjectStatus]


class TaskSubjectOption(TaskSubjectReference):
    name: str
    project_name: str
    source_name: str


class TaskSubjectInspect(InputModel):
    subjects: Annotated[list[TaskSubjectReference], Field(min_length=1, max_length=16)]


class TaskExecutionTarget(InputModel):
    mode: Literal["standard", "infinite_canvas"]
    project_id: Identifier
    canvas_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def matching_canvas(self):
        if (self.mode == "infinite_canvas") != (self.canvas_id is not None):
            raise ValueError("Only a canvas target requires an explicit canvas identity")
        return self


class TaskAttachmentSelection(InputModel):
    expected_input_revision: NonnegativeVersion
    retain_ids: Annotated[list[Identifier], Field(max_length=16)]

    @model_validator(mode="after")
    def unique_attachments(self):
        if len(set(self.retain_ids)) != len(self.retain_ids):
            raise ValueError("Retained attachments must be unique")
        return self


class TaskAttachmentRead(ReadModel):
    id: Identifier
    kind: Literal["text", "image", "video", "audio"]
    name: str
    checksum_sha256: str
    available: bool


class TaskAttachmentMetadata(ReadModel):
    task_id: Identifier
    input_revision: NonnegativeVersion
    applied_input_revision: NonnegativeVersion
    pending: bool
    items: list[TaskAttachmentRead]


class TaskMediaLimits(InputModel):
    images: Annotated[int, Field(strict=True, ge=0, le=1000)] | None = None
    videos: Annotated[int, Field(strict=True, ge=0, le=1000)] | None = None
    audio: Annotated[int, Field(strict=True, ge=0, le=1000)] | None = None


class AssistantTaskMessage(AssistantMessageCreate):
    task_id: Identifier | None = None
    new_task: bool = False
    script_reference: ScriptReference | None = None
    subject_selection: TaskSubjectSelection | None = None
    attachment_selection: TaskAttachmentSelection | None = None
    execution_target: TaskExecutionTarget | None = None
    memory_project_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(
        default_factory=list
    )
    media_limits: TaskMediaLimits = Field(default_factory=TaskMediaLimits)

    @model_validator(mode="after")
    def independent_task(self):
        if self.new_task and self.task_id is not None:
            raise ValueError("A new task cannot address an existing task")
        if "subject_selection" in self.model_fields_set and self.subject_selection is None:
            raise ValueError("An explicit selection must provide its revision and desired subjects")
        if "attachment_selection" in self.model_fields_set and self.attachment_selection is None:
            raise ValueError("An attachment selection must provide its revision and retained IDs")
        if len(set(self.memory_project_ids)) != len(self.memory_project_ids):
            raise ValueError("Memory projects must be unique")
        return self


class TaskControlRequest(InputModel):
    kind: Literal["stop", "redirect", "resume"]
    expected_control_seq: NonnegativeVersion | None = None
    content: Annotated[str, Field(min_length=1, max_length=32000)] | None = None
    message_id: Identifier | None = None

    @model_validator(mode="after")
    def required_control_inputs(self):
        if self.kind == "resume" and self.expected_control_seq is None:
            raise ValueError("Resume must identify the current stop or pause version")
        if self.kind == "redirect" and (self.content is None or not self.content.strip()):
            raise ValueError("Redirect requires the new user instruction")
        if self.message_id is not None and (self.kind != "resume" or self.content is not None):
            raise ValueError("Only resume may reuse an accepted message without new content")
        return self


class OperationRead(ReadModel):
    id: Identifier
    task_id: Identifier
    run_id: Identifier
    call_id: Identifier
    tool_name: str
    item_seq: int | None = None
    visibility: Literal["silent", "step", "artifact"] = "step"
    label: str = ""
    tool_version: Identifier
    plan_revision: Identifier
    state_version: Identifier
    phase: TaskPhase
    outcome: Literal["running", "waiting_media", "reconciling", "succeeded", "failed", "conflict"]
    result_refs: list[dict]
    skill_activations: list[dict] = Field(default_factory=list)
    undo_receipts: list[dict] = Field(default_factory=list)
    error: dict | None
    created_at: datetime
    finished_at: datetime | None


class TaskRead(ReadModel):
    id: Identifier
    conversation_id: Identifier
    goal: str
    status: TaskStatus
    phase: TaskPhase
    row_version: Identifier
    plan_revision: Identifier
    input_revision: NonnegativeVersion
    applied_input_revision: NonnegativeVersion
    control_seq: NonnegativeVersion
    applied_control_seq: NonnegativeVersion
    control_state: Literal["none", "stop_pending", "redirect_pending", "delete_pending"]
    interruptions: dict = Field(
        default_factory=lambda: {"stop": True, "redirect": False, "resume": False}
    )
    execution_target: TaskExecutionTarget | None
    memory_project_ids: list[Identifier]
    selected_subjects: list[TaskSubjectRead]
    source_context: dict | None
    skills: list[dict]
    skill_activations: list[dict] = Field(default_factory=list)
    media_limits: TaskMediaLimits
    media_usage: dict
    active_operation: OperationRead | None
    error: dict | None
    created_at: datetime
    updated_at: datetime


class TaskMessageAccepted(ReadModel):
    message: MessageRead
    task: TaskRead
    run: RunRead
    cursor: int


class TaskControlAccepted(ReadModel):
    control_id: Identifier
    task: TaskRead
    run: RunRead
    cursor: int


class TaskRuntimeState(ReadModel):
    conversation_id: Identifier
    snapshot_cursor: int
    resume_cursor: int
    messages: PageResponse[MessageRead]
    runs: list[RunRead]
    tasks: list[TaskRead]
    operations: list[OperationRead]
    inputs: list[dict]
    has_more_tasks: bool
    has_more_operations: bool


class OperationDetailRead(OperationRead):
    result: dict | None


class OperationPage(ReadModel):
    items: list[OperationRead]
    has_more: bool
    next_after_id: Identifier | None
    snapshot_cursor: int
