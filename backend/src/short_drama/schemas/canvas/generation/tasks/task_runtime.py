"""原版任务中心的闭合请求与响应；模型凭据只能从服务器读取。"""

import math
import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, model_validator

from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas.document.canvas import CanvasKey, SourceKey
from short_drama.schemas.canvas.document.storyboard.storyboard import (
    CanvasStoryboardAsset,
    CanvasStoryboardCharacter,
    CanvasStoryboardStyle,
)
from short_drama.schemas.canvas.generation.generation import CanvasGenerationProtocol
from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubExecution
from short_drama.schemas.canvas.tools.tool_contracts import CanvasToolRequests
from short_drama.schemas.generations.admission.generation import Prompt, TextMessage

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

    @model_validator(mode="before")
    @classmethod
    def reject_retired_bindings(cls, value):
        retired = {
            "resolvedCharacterVersions",
            "resolved_character_versions",
            "resolvedCharacterVoices",
            "resolved_character_voices",
        }
        if isinstance(value, dict) and retired.intersection(value):
            raise ValueError("Standard character bindings are retired")
        return value


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
    stream: bool = Field(default=True, strict=True)
    thinking: bool = Field(default=False, strict=True)


class CanvasTaskInput(CanvasGenerationProtocol):
    mode: Kind
    prompt: Prompt
    config: dict[str, str | bool | int | float | None] = Field(default_factory=dict)
    capability_options: dict[str, JsonValue] | None = None
    text_history: list[TextMessage] = Field(default_factory=list, max_length=99)
    text_options: CanvasTextOptions = Field(default_factory=CanvasTextOptions)
    agent_requests: CanvasToolRequests | None = None
    reference_images: list[CanvasTaskReference] = Field(default_factory=list, max_length=30)
    reference_videos: list[CanvasTaskReference] = Field(default_factory=list, max_length=16)
    reference_audios: list[CanvasTaskReference] = Field(default_factory=list, max_length=16)
    mask: CanvasTaskReference | None = None
    metadata: CanvasTaskMetadata
    execution: RunningHubExecution | None = None
    canvas_assets: list[CanvasStoryboardAsset] | None = Field(default=None, max_length=60)
    requirements: str | None = Field(default=None, max_length=8000)
    project_style: CanvasStoryboardStyle | None = None
    characters: list[CanvasStoryboardCharacter] | None = Field(default=None, max_length=60)
    shot_duration_seconds: int | None = Field(default=None, strict=True, ge=0, le=60)
    shot_count: int | None = Field(default=None, strict=True, ge=0, le=100)

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
        storyboard_fields = (
            self.input.canvas_assets,
            self.input.requirements,
            self.input.project_style,
            self.input.characters,
            self.input.shot_duration_seconds,
            self.input.shot_count,
        )
        if self.operation == "storyboard":
            if (
                self.input.mode != "text"
                or any(value is None for value in storyboard_fields)
                or self.input.metadata.source_node_id != self.input.metadata.node_id
                or self.input.agent_requests
                or self.input.text_history
                or self.input.reference_images
                or self.input.reference_videos
                or self.input.reference_audios
                or self.input.mask
                or self.input.execution
                or self.input.capability_options
                or self.input.text_options.thinking
                or self.provider
            ):
                raise ValueError("Storyboard requires one saved script and complete context")
        elif any(value is not None for value in storyboard_fields):
            raise ValueError("Storyboard context is only valid for storyboard tasks")
        if self.input.execution is not None and (
            self.provider != "runninghub"
            or self.input.execution.interface_type != f"runninghub-workflow-{self.input.mode}"
            or self.input.agent_requests is not None
        ):
            raise ValueError("Workflow selection must agree with the provider and mode")
        if self.input.agent_requests is not None:
            inputs = self.input
            canonical = inputs.agent_requests.canonical
            metadata = inputs.metadata.model_dump(by_alias=True)
            name = canonical.tools[0].function.name
            if (
                inputs.mode != "text"
                or self.operation != "text"
                or inputs.text_history
                or inputs.mask
                or inputs.reference_videos
                or inputs.reference_audios
                or inputs.capability_options
                or self.provider
                or inputs.metadata.source_node_id not in {None, inputs.metadata.node_id}
                or inputs.config.keys() - {"systemPrompt"}
                or (
                    "systemPrompt" in inputs.config
                    and inputs.config["systemPrompt"] != canonical.system_prompt
                )
            ):
                raise ValueError("Editor tool task requires a single saved text-stage scope")
            if (name == "optimize_prompt" and metadata.get("source") != "prompt-optimizer") or (
                name != "optimize_prompt"
                and (metadata.get("source") != "art-critique" or metadata.get("stage") != name)
            ):
                raise ValueError("Editor tool source and stage must match the fixed tool")
            if [item.storage_key for item in inputs.reference_images] != canonical.image_keys():
                raise ValueError("Editor tool references must match the canonical image order")
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
                        "runninghubuploadapikey",
                        "runninghubwalletapikey",
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
