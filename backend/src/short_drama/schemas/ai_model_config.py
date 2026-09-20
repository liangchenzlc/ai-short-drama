from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, SecretStr

from .base import (
    Identifier,
    InputModel,
    ReadModel,
    nonblank,
)


class AIModelConfigCreate(InputModel):
    service_type: Literal["text", "image", "video"]
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    model_key: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    provider: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    base_url: Annotated[str, Field(max_length=2048)] = ""
    apikey: SecretStr | None = None
    enabled: Literal[0, 1] = 1


class AIModelConfigUpdate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None
    model_key: Annotated[str, Field(max_length=255), AfterValidator(nonblank)] = None
    provider: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None
    base_url: Annotated[str, Field(max_length=2048)] = None
    apikey: SecretStr | None = None
    enabled: Literal[0, 1] = None
    row_version: Identifier


class AIModelConfigRead(ReadModel):
    id: Identifier
    service_type: Literal["text", "image", "video"]
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    model_key: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    provider: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    base_url: Annotated[str, Field(max_length=2048)]
    enabled: Literal[0, 1]
    is_deleted: Literal[0, 1]
    is_default: Literal[0, 1]
    row_version: Identifier
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None
    default_service_type: Annotated[str, Field(max_length=16)] | None = None
    has_api_key: bool = False


class AIModelConfigDefault(InputModel):
    row_version: Identifier
