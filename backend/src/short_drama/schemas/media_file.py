from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, Field, model_validator

from .base import (
    Identifier,
    InputModel,
    PositiveUInt32,
    PositiveUInt64,
    ReadModel,
    UInt64,
    nonblank,
)


class MediaFileCreate(InputModel):
    format_code: Annotated[
        str,
        Field(
            max_length=127, pattern=r"^(?:demo:image|(?:image|video)/[a-z0-9][a-z0-9!#$&^_.+-]*)$"
        ),
    ]
    storage_locator: Annotated[str, Field(max_length=700), AfterValidator(nonblank)]
    original_name: Annotated[str, Field(max_length=255)] = ""
    byte_size: UInt64 | None = None
    width: PositiveUInt32 | None = None
    height: PositiveUInt32 | None = None
    duration_ms: PositiveUInt64 | None = None
    checksum_sha256: (
        Annotated[str, Field(max_length=64, min_length=64, pattern=r"^[0-9a-fA-F]{64}$")] | None
    ) = None

    @model_validator(mode="after")
    def validate_media_duration(self):
        if self.duration_ms is not None and not self.format_code.startswith("video/"):
            raise ValueError("only video media may have duration_ms")
        return self


class MediaFileUpdate(InputModel):
    original_name: Annotated[str, Field(max_length=255)] = None
    byte_size: UInt64 | None = None
    width: PositiveUInt32 | None = None
    height: PositiveUInt32 | None = None
    duration_ms: PositiveUInt64 | None = None
    checksum_sha256: (
        Annotated[str, Field(max_length=64, min_length=64, pattern=r"^[0-9a-fA-F]{64}$")] | None
    ) = None


class MediaFileRead(ReadModel):
    id: Identifier
    format_code: Annotated[
        str,
        Field(
            max_length=127, pattern=r"^(?:demo:image|(?:image|video)/[a-z0-9][a-z0-9!#$&^_.+-]*)$"
        ),
    ]
    storage_locator: Annotated[str, Field(max_length=700), AfterValidator(nonblank)]
    original_name: Annotated[str, Field(max_length=255)]
    byte_size: UInt64 | None = None
    width: PositiveUInt32 | None = None
    height: PositiveUInt32 | None = None
    duration_ms: PositiveUInt64 | None = None
    checksum_sha256: (
        Annotated[str, Field(max_length=64, min_length=64, pattern=r"^[0-9a-fA-F]{64}$")] | None
    ) = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
