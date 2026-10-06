"""原版任务中心的闭合请求与响应；模型凭据只能从服务器读取。"""

import math
import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, model_validator

from .ai_generation import Prompt, TextMessage
from .base import Identifier
from .canvas import CanvasKey, SourceKey
from .canvas_generation import CanvasGenerationProtocol

Kind = Literal["text", "image", "video", "audio"]
OperationId = Annotated[str, Field(pattern=r"^[A-Za-z0-9:_-]{8,128}$")]


class CanvasTaskMetadata(CanvasGenerationProtocol):
    model_config = ConfigDict(
        extra="allow",
        alias_generator=CanvasGenerationProtocol.model_config["alias_generator"],
        populate_by_name=True,
    )
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    node_id: SourceKey
    source_node_id: SourceKey | None = None
    client_operation_id: OperationId
    retry_of: Identifier | None = None
    attempt_group_id: str | None = Field(default=None, min_length=1, max_length=128)


class CanvasTaskReference(CanvasGenerationProtocol):
    id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    type: str | None = Field(default=None, max_length=128)
    storage_key: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=4096)
    data_url: Literal[""] | None = None
    bytes: int | None = Field(default=None, strict=True, gt=0)
    width: int | None = Field(default=None, strict=True, gt=0)
    height: int | None = Field(default=None, strict=True, gt=0)
    duration_ms: int | None = Field(default=None, strict=True, gt=0)


class CanvasTextOptions(CanvasGenerationProtocol):
    stream: bool = True
    thinking: bool = False


class CanvasTaskInput(CanvasGenerationProtocol):
    mode: Kind
    prompt: Prompt
    config: dict[str, str | bool | int | float | None] = Field(default_factory=dict)
    capability_options: dict[str, JsonValue] | None = None
    text_history: list[TextMessage] = Field(default_factory=list, max_length=99)
    text_options: CanvasTextOptions = Field(default_factory=CanvasTextOptions)
    reference_images: list[CanvasTaskReference] = Field(default_factory=list, max_length=30)
    reference_videos: list[CanvasTaskReference] = Field(default_factory=list, max_length=16)
    reference_audios: list[CanvasTaskReference] = Field(default_factory=list, max_length=16)
    mask: CanvasTaskReference | None = None
    metadata: CanvasTaskMetadata

    @model_validator(mode="after")
    def reference_limit_by_mode(self):
        if self.mode != "video" and len(self.reference_images) > 16:
            raise ValueError("Non-video canvas input supports at most 16 reference images")
        return self


class CanvasRuntimeTaskCreate(CanvasGenerationProtocol):
    project_id: CanvasKey
    type: Literal["canvas_text", "canvas_image", "canvas_video", "canvas_audio"]
    operation: str = Field(min_length=1, max_length=64)
    prompt: Prompt
    model: str = Field(default="", max_length=512)
    logical_model_id: Identifier
    provider: str | None = Field(default=None, max_length=64)
    input: CanvasTaskInput

    @model_validator(mode="after")
    def coherent_input(self):
        if self.type != f"canvas_{self.input.mode}" or self.prompt != self.input.prompt:
            raise ValueError("Task type, mode and prompt must agree")
        pending = [self.input.metadata.model_dump(mode="python"), self.input.capability_options]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if any(
                    re.sub(r"[^a-z]", "", key.lower())
                    in {
                        "apikey",
                        "secretkey",
                        "baseurl",
                        "credential",
                        "credentialref",
                        "authorization",
                        "headers",
                    }
                    for key in value
                ):
                    raise ValueError(
                        "Canvas task input cannot contain client credentials or transport"
                    )
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Canvas task values must be finite")
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in self.input.config.values()
        ):
            raise ValueError("Canvas task values must be finite")
        return self


class CanvasRuntimeTaskOutput(CanvasGenerationProtocol):
    output_index: int
    media_type: Literal["image", "video", "audio"]
    materialized_asset_id: str | None = None


class CanvasRuntimeTaskRead(CanvasGenerationProtocol):
    id: Identifier
    project_id: CanvasKey | None = None
    type: str
    operation: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    stage: str
    prompt: str
    provider: str | None = None
    model: str | None = None
    client_operation_id: str
    retry_of: Identifier | None = None
    attempt_group_id: str | None = None
    client_context: dict[str, JsonValue]
    input_json: str
    result_json: str | None = None
    result_state: Literal["NOT_AVAILABLE", "PENDING_MATERIALIZATION", "READY"]
    outputs: list[CanvasRuntimeTaskOutput] = Field(default_factory=list)
    preview_url: str | None = None
    preview_kind: Literal["image", "video"] | None = None
    text_draft: str | None = None
    text_draft_sequence: int | None = Field(default=None, ge=0, le=4096)
    error: str | None = None
    error_code: str | None = None
    provider_request_id: str | None = None
    can_retry: bool
    can_resume: bool
    can_cancel: bool
    attempts: int
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None


class CanvasRuntimeTaskLog(CanvasGenerationProtocol):
    summary: str
    level: Literal["info", "warn", "error"]
    created_at: str


class CanvasTaskTextDeltaRead(CanvasGenerationProtocol):
    id: Identifier
    task_id: Identifier
    sequence: int = Field(ge=1, le=4096)
    content: str
    byte_count: int = Field(ge=1, le=65536)
    created_at: str
    expires_at: str


class CanvasTaskTextReplayRead(CanvasGenerationProtocol):
    deltas: list[CanvasTaskTextDeltaRead]
    text_draft: str
    final_text: str
    complete: bool
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    stage: str
    progress: int = Field(ge=0, le=100)
    error: str | None = None
