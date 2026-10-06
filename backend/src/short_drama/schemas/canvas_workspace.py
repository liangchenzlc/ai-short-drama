"""Hosted canvas preferences contain selections, never provider credentials."""

import json
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import Identifier, InputModel, NonnegativeVersion
from .canvas import validate_document_json
from .canvas_model_catalog import CanvasModelChannelInput, validate_channels

PREFERENCE_FIELDS = frozenset(
    """model imageModel videoModel textModel audioModel assistantModel audioVoice audioFormat
audioSpeed audioPitch audioVolume audioInstructions videoSeconds vquality videoGenerateAudio
videoWatermark videoArkPrivateAssetUpload systemPrompt quality size transparentBackground count
canvasImageCount taskWorkflowProvider""".split()
)


class CanvasWorkspacePreferencesRequest(InputModel):
    expected_row_version: NonnegativeVersion
    preferences: dict[str, JsonValue]
    channels: list[CanvasModelChannelInput] | None = Field(None, max_length=128)

    @model_validator(mode="after")
    def validate_preferences(self):
        if self.preferences.keys() - PREFERENCE_FIELDS:
            raise ValueError("unsupported canvas model preference")
        validate_document_json(self.preferences)
        if len(json.dumps(self.preferences, ensure_ascii=False).encode()) > 65536:
            raise ValueError("canvas preferences exceed 64 KiB")
        if any(not isinstance(value, str) for value in self.preferences.values()):
            raise ValueError("canvas model preferences require string values")
        if self.channels is not None:
            validate_channels(self.channels)
        return self


class CanvasWorkspacePreferencesRead(InputModel):
    row_version: NonnegativeVersion
    preferences: dict[str, JsonValue]


class CanvasModelRead(InputModel):
    id: Identifier
    name: str
    model_key: str
    provider: str
    service_type: Literal["text", "image", "video", "audio"]
    enabled: bool
    has_api_key: bool


class CanvasWorkspaceModelsRead(CanvasWorkspacePreferencesRead):
    models: list[CanvasModelRead] = Field(default_factory=list)
    channels: list[dict[str, JsonValue]] = Field(default_factory=list)
