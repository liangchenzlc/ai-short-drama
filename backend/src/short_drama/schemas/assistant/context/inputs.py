"""Project-private conversations with explicitly removable, versioned source context."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.assistant.context.context import SkillSelection
from short_drama.schemas.base import Identifier, InputModel


class AssistantMessageCreate(InputModel):
    content: Annotated[str, Field(min_length=1, max_length=32000)]
    model_config_id: Identifier | None = None
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
