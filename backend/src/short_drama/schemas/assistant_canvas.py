"""Bounded canvas actions; model input never contains a replacement document or media URL."""

import json
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .base import Identifier, InputModel, NonnegativeVersion
from .canvas import CanvasPosition
from .canvas_generation_parameters import CanvasAudioProviderOptions

NodeReference = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=128, pattern=r"^(?:\$[A-Za-z][\w-]{0,47}|[\w:.-]+)$"
    ),
]
TemporaryNodeKey = Annotated[
    str, StringConstraints(min_length=1, max_length=48, pattern=r"^[A-Za-z][\w-]*$")
]
CanvasNodeKind = Literal["text", "image", "video", "audio"]
NodeText = Annotated[str, Field(max_length=32000)]


class AssistantNodeSettings(InputModel):
    size: Annotated[str, Field(max_length=64)] | None = None
    quality: Annotated[str, Field(max_length=64)] | None = None
    seconds: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,3}$")] | None = None
    vquality: Annotated[str, Field(max_length=64)] | None = None
    audioVoice: Annotated[str, Field(max_length=255)] | None = None
    audioFormat: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] | None = None
    audioSpeed: Annotated[str, Field(max_length=16)] | None = None
    audioInstructions: Annotated[str, Field(max_length=4096)] | None = None

    @field_validator("audioSpeed")
    @classmethod
    def native_speed_range(cls, value):
        if value is not None:
            CanvasAudioProviderOptions(speed=float(value))
        return value


class AssistantNodeValues(InputModel):
    type: CanvasNodeKind
    title: Annotated[str, Field(max_length=255)] = ""
    content: NodeText = ""
    prompt: NodeText = ""
    model_config_id: Identifier | None = None
    settings: AssistantNodeSettings = Field(default_factory=AssistantNodeSettings)

    @model_validator(mode="after")
    def text_content_only(self):
        if self.type != "text" and self.content:
            raise ValueError("media nodes cannot receive model-supplied content or result URLs")
        return self


class AssistantNodeUpdate(InputModel):
    title: Annotated[str, Field(max_length=255)] = None
    content: NodeText = None
    prompt: NodeText = None
    model_config_id: Identifier | None = None
    settings: AssistantNodeSettings = None

    @model_validator(mode="after")
    def explicit_values(self):
        if not self.model_fields_set:
            raise ValueError("node update requires a field")
        if any(
            getattr(self, field) is None for field in self.model_fields_set - {"model_config_id"}
        ):
            raise ValueError("node fields cannot be null")
        return self


class CreateAssistantNode(InputModel):
    action: Literal["create_node"]
    key: TemporaryNodeKey
    values: AssistantNodeValues


class UpdateAssistantNode(InputModel):
    action: Literal["update_node"]
    node: NodeReference
    row_version: Identifier
    private_revision: NonnegativeVersion
    patch: AssistantNodeUpdate


class MoveAssistantNode(InputModel):
    action: Literal["move_node"]
    node: NodeReference
    row_version: Identifier
    private_revision: NonnegativeVersion
    position: CanvasPosition


class DeleteAssistantNode(InputModel):
    action: Literal["delete_node"]
    node: NodeReference
    row_version: Identifier
    private_revision: NonnegativeVersion


class ConnectAssistantNodes(InputModel):
    action: Literal["connect_nodes", "disconnect_nodes"]
    source: NodeReference
    target: NodeReference

    @model_validator(mode="after")
    def distinct_endpoints(self):
        if self.source == self.target:
            raise ValueError("self connections are not supported")
        return self


CanvasAction = Annotated[
    CreateAssistantNode
    | UpdateAssistantNode
    | MoveAssistantNode
    | DeleteAssistantNode
    | ConnectAssistantNodes,
    Field(discriminator="action"),
]


class AssistantCanvasPatch(InputModel):
    expected_revision: Identifier
    actions: Annotated[list[CanvasAction], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def bounded_increment(self):
        known, references, created = set(), [], 0
        for action in self.actions:
            if action.action == "create_node":
                if action.key in known:
                    raise ValueError("temporary node keys must be unique")
                known.add(action.key)
                created += 1
            else:
                references = (
                    [action.source, action.target]
                    if action.action in {"connect_nodes", "disconnect_nodes"}
                    else [action.node]
                )
                if any(ref.startswith("$") and ref[1:] not in known for ref in references):
                    raise ValueError("temporary references must identify earlier nodes")
        if created > 50:
            raise ValueError("one patch can create at most 50 nodes")
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode()) > 1048576:
            raise ValueError("canvas increment exceeds 1 MiB")
        return self
