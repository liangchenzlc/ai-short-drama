"""Bounded, explicitly selected Agent input contracts."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel


class SkillSelection(InputModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    content_version: Identifier


class SkillRead(ReadModel):
    id: str
    name: str
    filename: str | None
    builtin: bool
    content_version: Identifier
    row_version: Identifier | None
    enabled: bool
    instructions: str
    checksum_sha256: str


class SkillPatch(InputModel):
    row_version: Identifier
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    instructions: Annotated[str, Field(min_length=1, max_length=65536)] | None = None
    enabled: bool | None = None

    @field_validator("name", "instructions")
    @classmethod
    def nonempty(cls, value):
        if value is None or not value.strip() or "\x00" in value:
            raise ValueError("Skill content must not be blank")
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("Skill content exceeds 64 KiB")
        return value.strip()

    @model_validator(mode="after")
    def change_required(self):
        if not self.model_fields_set - {"row_version"}:
            raise ValueError("Supply a skill change")
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("Enabled must be a boolean")
        return self


class SkillDelete(InputModel):
    row_version: Identifier


class AttachmentReference(InputModel):
    source_type: Literal["media", "asset"]
    source_id: Identifier


class AttachmentRead(ReadModel):
    id: Identifier
    kind: Literal["text", "image", "video", "audio"]
    name: str
    mime_type: str
    byte_size: int
    media_id: Identifier | None
    url: str | None
    text_preview: str | None
    metadata: dict
    checksum_sha256: str
    pending: bool


class ModelInputsPatch(InputModel):
    row_version: Identifier
    image: bool
    audio: bool


class ModelInputCapabilities(ReadModel):
    text: Literal[True] = True
    image: bool = False
    audio: bool = False
    video: Literal["sampled_frames", "unsupported"] = "unsupported"
    evidence: Literal["declared", "model_family", "text_only", "runtime"] = "runtime"
