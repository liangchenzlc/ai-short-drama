from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, SecretStr, model_validator

from .base import (
    Identifier,
    InputModel,
    ReadModel,
    nonblank,
)
from .canvas_model_catalog import MAX_SECRET_BYTES, CanvasChannelHeader, validate_channel_headers
from .model_runtime_profile import ModelRuntimeHeaderRead, ModelRuntimeProfile


def validate_runtime_secret(value: SecretStr | None):
    if value is not None and len(value.get_secret_value().encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError("runtime secret key exceeds byte limit")
    return value


RuntimeSecret = Annotated[SecretStr | None, AfterValidator(validate_runtime_secret)]


class AIModelConfigCreate(InputModel):
    service_type: Literal["text", "image", "video", "audio"]
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    model_key: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    provider: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    base_url: Annotated[str, Field(max_length=2048)] = ""
    apikey: SecretStr | None = None
    enabled: Literal[0, 1] = 1
    runtime_profile: ModelRuntimeProfile | None = None
    secret_key: RuntimeSecret = None
    headers: list[CanvasChannelHeader] | None = None

    @model_validator(mode="after")
    def validate_runtime_secrets(self):
        if self.headers is not None:
            validate_channel_headers(self.headers)
        return self


class AIModelConfigUpdate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None
    model_key: Annotated[str, Field(max_length=255), AfterValidator(nonblank)] = None
    provider: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None
    base_url: Annotated[str, Field(max_length=2048)] = None
    apikey: SecretStr | None = None
    enabled: Literal[0, 1] = None
    row_version: Identifier
    runtime_profile: ModelRuntimeProfile | None = None
    secret_key: RuntimeSecret = None
    headers: list[CanvasChannelHeader] | None = None

    @model_validator(mode="after")
    def validate_runtime_secrets(self):
        if self.headers is not None:
            validate_channel_headers(self.headers)
        return self


class AIModelConfigRead(ReadModel):
    owner_user_id: Identifier | None = None
    id: Identifier
    service_type: Literal["text", "image", "video", "audio"]
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
    runtime_profile: ModelRuntimeProfile | None = None
    has_secret_key: bool = False
    headers: list[ModelRuntimeHeaderRead] = Field(default_factory=list)
    credential_source: Literal["manual", "beefapi"] = "manual"


class AIModelConfigDefault(InputModel):
    row_version: Identifier
