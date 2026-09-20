"""Input validation independent of ORM mappings and database sessions."""

import re
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
)

UINT64_MAX = 2**64 - 1
MEDIUMTEXT_MAX_BYTES = 2**24 - 1


def parse_identifier(value: object) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if type(value) is not int or not 1 <= value <= UINT64_MAX:
        raise ValueError("identifier must be a positive unsigned 64-bit decimal integer")
    return value


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain non-whitespace characters")
    return value


def mediumtext(value: str) -> str:
    if len(value.encode("utf-8")) > MEDIUMTEXT_MAX_BYTES:
        raise ValueError("text exceeds the MEDIUMTEXT UTF-8 byte limit")
    return value


Identifier = Annotated[
    int,
    BeforeValidator(parse_identifier),
    PlainSerializer(str, return_type=str, when_used="json"),
]
UInt64 = Annotated[int, Field(strict=True, ge=0, le=UINT64_MAX)]
PositiveUInt64 = Annotated[int, Field(strict=True, ge=1, le=UINT64_MAX)]
PositiveUInt32 = Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)]
MediumText = Annotated[str, AfterValidator(mediumtext)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
