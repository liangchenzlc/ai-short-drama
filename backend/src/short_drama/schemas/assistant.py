"""Project-private conversations with explicitly removable, versioned source context."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.agent import Title
from short_drama.schemas.agent_context import SkillSelection
from short_drama.schemas.base import Identifier, InputModel, parse_identifier


class AssistantConversationCreate(InputModel):
    project_id: Identifier
    title: Title = "新对话"

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value.strip()


class AssistantSelectedObject(InputModel):
    kind: Literal["asset", "shot", "node"]
    id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: Identifier | None = None

    @model_validator(mode="after")
    def stable_identifier(self):
        if self.kind != "node":
            parse_identifier(self.id)
        return self


class AssistantSourceContext(InputModel):
    kind: Literal["episode", "canvas"]
    id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: Identifier
    include_document: bool = True
    stage: Literal["source", "assets", "storyboard", "assembly"] | None = None
    storyboard_revision: Identifier | None = None
    selected: Annotated[list[AssistantSelectedObject], Field(max_length=16)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def matching_source(self):
        if self.kind == "episode":
            parse_identifier(self.id)
        if any((item.kind == "node") != (self.kind == "canvas") for item in self.selected):
            raise ValueError("Selected objects must belong to the source kind")
        if len({(item.kind, item.id) for item in self.selected}) != len(self.selected):
            raise ValueError("Selected objects must be unique")
        if self.kind == "canvas" and (
            self.stage is not None or self.storyboard_revision is not None
        ):
            raise ValueError("Canvas context does not have episode stages")
        return self


class AssistantMessageCreate(InputModel):
    content: Annotated[str, Field(min_length=1, max_length=32000)]
    model_config_id: Identifier | None = None
    context: AssistantSourceContext | None = None
    attachment_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(default_factory=list)
    skills: Annotated[list[SkillSelection], Field(max_length=8)] = Field(default_factory=list)
    video_audio: Literal["include", "visual_only"] = "include"

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def unique_inputs(self):
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Attachments must be unique")
        if len({item.id for item in self.skills}) != len(self.skills):
            raise ValueError("Skills must be unique")
        return self
