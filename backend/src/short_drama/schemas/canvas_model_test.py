"""本人显式模型测试；不依赖项目或画布节点。"""

from typing import Literal

from pydantic import Field, model_validator

from .base import Identifier, parse_identifier
from .canvas_generation import CanvasGenerationProtocol
from .canvas_model_catalog import CanvasModelChannelInput
from .canvas_task_runtime import CanvasTextOptions, Kind, OperationId, Prompt


class CanvasModelTestChannel(CanvasModelChannelInput):
    @model_validator(mode="after")
    def channel_invariants(self):
        if not self.source_key.startswith("host-"):
            return super().channel_invariants()
        identifier = self.source_key.removeprefix("host-")
        parse_identifier(identifier)
        if not identifier.startswith(tuple("123456789")):
            raise ValueError("invalid host model identity")
        if self.scope not in {"user", "system"} or self.pinned:
            raise ValueError("invalid host model scope")
        return self.validate_channel_fields()


class CanvasModelTestCreate(CanvasGenerationProtocol):
    channel: CanvasModelTestChannel
    model: str = Field(min_length=1, max_length=255)
    mode: Kind
    prompt: Prompt
    config: dict[str, str | bool | int | float | None] = Field(default_factory=dict)
    text_options: CanvasTextOptions = Field(default_factory=lambda: CanvasTextOptions(stream=False))
    client_operation_id: OperationId

    @model_validator(mode="after")
    def selected_profile(self):
        profiles = [item for item in self.channel.model_profiles if item.model == self.model]
        if (
            self.model not in self.channel.models
            or len(profiles) != 1
            or profiles[0].capability != self.mode
        ):
            raise ValueError("test requires the selected model capability profile")
        if self.text_options.stream or self.text_options.thinking:
            raise ValueError("model connection tests require nonstreaming output")
        return self


class CanvasModelTestRead(CanvasGenerationProtocol):
    id: Identifier
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    result: dict | None = None
    error: str | None = None
    error_code: str | None = None
    can_cancel: bool
