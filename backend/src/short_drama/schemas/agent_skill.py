"""Bounded, non-executable method packages and versioned read-only resources."""

import base64
import io
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import Field, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, ReadModel

MAX_SKILL_BYTES = 65536
MAX_RESOURCE_BYTES = 131072
MAX_PACKAGE_BYTES = 1048576
MAX_PACKAGE_FILES = 32
TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
SCRIPT_SUFFIXES = {".py", ".js", ".ts", ".sh", ".ps1", ".sql"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SkillMode = Literal["standard", "infinite_canvas"]
ResourceKind = Literal["reference", "template", "script", "image"]


def resource_path(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    parts = value.split("/")
    if (
        not value
        or len(value.encode("utf-8")) > 255
        or len(parts) > 6
        or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts)
        or any(ord(character) < 32 or character in "\\:%?#" for character in value)
        or PurePosixPath(value).suffix.lower()
        not in TEXT_SUFFIXES | SCRIPT_SUFFIXES | IMAGE_SUFFIXES
        or value.casefold() == "skill.md"
    ):
        raise ValueError("Resource path must be a bounded relative path")
    return value


def bounded_text(value: str, maximum: int) -> str:
    if "\x00" in value or len(value.encode("utf-8")) > maximum:
        raise ValueError("Text exceeds its UTF-8 byte limit or contains NUL")
    return value


def writable_resource(path: str, kind: str) -> None:
    """Keep legacy script reads possible while rejecting every new script write."""
    if kind == "script" or "scripts" in path.casefold().split("/"):
        raise ValueError(f"暂不支持脚本：{path}")
    if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES | IMAGE_SUFFIXES:
        raise ValueError(f"暂不支持此文件：{path}")
    if any(part.casefold() == "skill.md" for part in path.split("/")):
        raise ValueError("SKILL.md 只能作为根主文件")


class SkillResourceInput(InputModel):
    path: Annotated[str, Field(min_length=1, max_length=255)]
    kind: ResourceKind = "reference"
    content: Annotated[str, Field(max_length=4 * ((MAX_RESOURCE_BYTES + 2) // 3))]
    encoding: Literal["utf-8", "base64"] = "utf-8"

    _path = field_validator("path")(resource_path)

    def resource_bytes(self) -> bytes:
        return (
            base64.b64decode(self.content, validate=True)
            if self.encoding == "base64"
            else self.content.encode()
        )

    @model_validator(mode="after")
    def validate_content(self):
        writable_resource(self.path, self.kind)
        suffix = PurePosixPath(self.path).suffix.lower()
        if self.kind == "image":
            if self.encoding != "base64" or suffix not in IMAGE_SUFFIXES:
                raise ValueError("Image resources require base64 transport and an image path")
            data = self.resource_bytes()
            if not data or len(data) > MAX_RESOURCE_BYTES:
                raise ValueError("Image exceeds resource size limit")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format not in {"PNG", "JPEG", "WEBP"}
                        or image.width * image.height > 40000000
                    ):
                        raise ValueError("Unsupported image resource")
                    image.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
                raise ValueError("Invalid image resource") from error
        else:
            if self.encoding != "utf-8" or suffix in IMAGE_SUFFIXES:
                raise ValueError("Text resources require UTF-8 transport")
            bounded_text(self.content, MAX_RESOURCE_BYTES)
        return self


class SkillMetadata(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    slug: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=16)
    ] = Field(default_factory=list)
    modes: Annotated[list[SkillMode], Field(min_length=1, max_length=2)] = Field(
        default_factory=lambda: ["standard", "infinite_canvas"]
    )
    exclusions: Annotated[str, Field(max_length=2000)] = ""
    required_tools: Annotated[list[str], Field(max_length=24)] = Field(default_factory=list)
    source: Annotated[str, Field(max_length=4000)] = ""

    @field_validator("name", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name and description must not be blank")
        return bounded_text(value.strip(), 8000)

    @field_validator("tags", "modes", "required_tools")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Skill metadata values must be unique")
        if any(not item.strip() or "\x00" in item for item in value):
            raise ValueError("Skill metadata must contain nonblank text")
        return value

    @field_validator("required_tools")
    @classmethod
    def tool_names(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", item) for item in value):
            raise ValueError("Invalid tool declaration")
        return value

    @field_validator("exclusions", "source")
    @classmethod
    def metadata_text(cls, value: str) -> str:
        return bounded_text(value, 16000)


class SkillCreate(SkillMetadata):
    instructions: Annotated[str, Field(min_length=1, max_length=MAX_SKILL_BYTES)]
    resources: Annotated[list[SkillResourceInput], Field(max_length=MAX_PACKAGE_FILES - 1)] = Field(
        default_factory=list
    )
    enabled: bool = True

    @field_validator("instructions")
    @classmethod
    def instruction_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Skill instructions must not be blank")
        return bounded_text(value.strip(), MAX_SKILL_BYTES)

    @model_validator(mode="after")
    def bounded_package(self):
        paths = [item.path.casefold() for item in self.resources]
        if len(paths) != len(set(paths)):
            raise ValueError("Resource paths must be unique on all supported platforms")
        if any(path.startswith(other + "/") for path in paths for other in paths):
            raise ValueError("A resource cannot also be a directory")
        if (
            len(self.instructions.encode("utf-8"))
            + sum(len(item.resource_bytes()) for item in self.resources)
            > MAX_PACKAGE_BYTES
        ):
            raise ValueError("Skill package exceeds 1 MiB")
        return self


class SkillPackagePatch(InputModel):
    row_version: Identifier
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    slug: (
        Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
        | None
    ) = None
    description: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    instructions: Annotated[str, Field(min_length=1, max_length=MAX_SKILL_BYTES)] | None = None
    tags: list[str] | None = None
    modes: list[SkillMode] | None = None
    exclusions: str | None = None
    required_tools: list[str] | None = None
    source: str | None = None
    resources: list[SkillResourceInput] | None = None
    enabled: bool | None = None

    @field_validator("name", "instructions", "description")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if value is None or not value.strip():
            raise ValueError("Skill content must not be blank")
        return bounded_text(value.strip(), MAX_SKILL_BYTES)

    @model_validator(mode="after")
    def change_required(self):
        fields = self.model_fields_set - {"row_version"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("Supply a non-null skill change")
        defaults = dict(name="method", slug="method", description="Method", instructions="Method")
        defaults.update({field: getattr(self, field) for field in fields})
        SkillCreate.model_validate(defaults)
        return self


class SkillSettingsPatch(InputModel):
    row_version: NonnegativeVersion
    enabled: bool


class SkillResourceRead(ReadModel):
    path: str
    kind: ResourceKind
    byte_size: int
    checksum_sha256: str


class SkillSummary(ReadModel):
    id: str
    name: str
    filename: str | None
    builtin: bool
    content_version: Identifier
    row_version: Identifier | None
    enabled: bool
    settings_version: NonnegativeVersion = 0
    slug: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    modes: list[SkillMode] = Field(default_factory=lambda: ["standard", "infinite_canvas"])
    exclusions: str = ""
    required_tools: list[str] = Field(default_factory=list)
    source: str = ""
    compatible: bool = True
    issues: list[str] = Field(default_factory=list)
    checksum_sha256: str
    package_checksum: str = ""
    resource_count: int = 0
    updated_at: datetime | None = None


class SkillRead(SkillSummary):
    instructions: str
    resources: list[SkillResourceRead] = Field(default_factory=list)


class SkillCopy(InputModel):
    content_version: Identifier
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None

    @field_validator("name")
    @classmethod
    def display_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Copy name must not be blank")
        return value.strip() if value is not None else None


class SkillImportDraft(InputModel):
    name: str
    slug: str
    description: str = ""
    instructions: str
    tags: list[str] = Field(default_factory=list)
    modes: list[SkillMode] = Field(default_factory=lambda: ["standard", "infinite_canvas"])
    exclusions: str = ""
    required_tools: list[str] = Field(default_factory=list)
    source: str = ""
    resources: list[SkillResourceInput] = Field(default_factory=list)
    enabled: bool = True


class SkillImportPreview(ReadModel):
    filename: str
    draft: SkillImportDraft
    compatible: bool
    issues: list[str]
    files: list[dict]
    byte_size: int
    tags_provided: bool = False
    mode_issues: dict[str, list[str]] = Field(default_factory=dict)


class SkillResourceContent(SkillResourceRead):
    skill_id: str
    content_version: Identifier
    offset: int
    content: str
    next_offset: int | None
    resource_ref: dict | None = None
    encoding: Literal["utf-8", "base64"] = "utf-8"
