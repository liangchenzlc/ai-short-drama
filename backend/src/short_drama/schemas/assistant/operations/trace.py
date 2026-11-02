"""Bounded, storage-independent execution display contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from short_drama.schemas.base import Identifier, ReadModel

TRACE_PREVIEW_BYTES = 16 * 1024


class ExecutionTarget(ReadModel):
    kind: str
    id: str | None = None
    display_name: str
    version: Identifier | None = None
    path: str | None = None
    offset: int | None = None
    length: int | None = None


class ExecutionDisplay(ReadModel):
    action: str
    target: ExecutionTarget
    title: str


class TraceResource(ReadModel):
    resource_id: Identifier
    name: str
    kind: str
    byte_size: int | None = None
    total_bytes: int | None = None
    truncated: bool = False
    available: bool = True


class TraceChannel(ReadModel):
    preview: str = ""
    offset: int = 0
    total_bytes: int = 0
    truncated: bool = False


class TraceBlock(ExecutionDisplay):
    block_id: Identifier
    block_version: Identifier
    run_id: Identifier
    execution_scope_id: Identifier
    parent_call_id: Identifier | None = None
    tool_call_id: Identifier | None = None
    subtask_id: Identifier | None = None
    model_call_id: Identifier | None = None
    operation_id: Identifier | None = None
    tool_name: str | None = None
    kind: Literal["reasoning", "tool", "skill", "subtask", "sandbox", "wait"]
    source: str
    visibility: Literal["silent", "step", "artifact"] = "step"
    status: str = "running"
    started_seq: int = 0
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: dict | None = None
    channels: dict[str, TraceChannel] = Field(default_factory=dict)
    resources: list[TraceResource] = Field(default_factory=list)
    result_refs: list[dict] = Field(default_factory=list)


class TraceDelta(ReadModel):
    block_id: Identifier
    block_version: Identifier
    execution_scope_id: Identifier
    parent_call_id: Identifier | None = None
    tool_call_id: Identifier | None = None
    subtask_id: Identifier | None = None
    source: str
    channel: str
    offset: int
    delta: str
    created_at: datetime


class TracePage(ReadModel):
    items: list[TraceBlock]
    has_more: bool
    next_before_seq: int | None
    snapshot_cursor: int


class TraceResourceRead(ReadModel):
    resource_id: Identifier
    name: str
    content: str | None = None
    offset: int | None = None
    length: int | None = None
    url: str | None = None
