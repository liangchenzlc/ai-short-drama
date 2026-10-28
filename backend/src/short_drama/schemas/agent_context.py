"""Bounded, explicitly selected Agent input contracts."""

from typing import Annotated, Literal

from pydantic import Field

from short_drama.schemas.agent_skill import SkillPackagePatch
from short_drama.schemas.agent_skill import SkillRead as SkillRead
from short_drama.schemas.base import Identifier, InputModel, ReadModel

SkillPatch = SkillPackagePatch


class SkillSelection(InputModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    content_version: Identifier


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
    context_window_tokens: Annotated[int, Field(strict=True, ge=16384, le=1048576)] | None = None


class ModelInputCapabilities(ReadModel):
    text: Literal[True] = True
    image: bool = False
    audio: bool = False
    video: Literal["sampled_frames", "unsupported"] = "unsupported"
    evidence: Literal["declared", "model_family", "text_only", "runtime"] = "runtime"
    context_window_tokens: int = 65536
    context_window_evidence: Literal["declared", "platform_default"] = "platform_default"
