"""原版画布模型目录契约；渠道凭据只作为一次请求的内存输入。"""

from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, SecretStr, model_validator

from .base import InputModel
from .canvas_model_catalog import (
    MAX_CHANNEL_HEADERS,
    MAX_SECRET_BYTES,
    CanvasChannelHeader,
    validate_channel_headers,
)


class CanvasChannelModelsRequest(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    api_key: SecretStr | None = Field(None, alias="apiKey")
    api_format: Literal["openai", "gemini", "claude"] = Field("openai", alias="apiFormat")
    headers: list[CanvasChannelHeader] = Field(default_factory=list, max_length=MAX_CHANNEL_HEADERS)
    channel_id: str | None = Field(None, alias="channelId", min_length=1, max_length=128)
    credential_ref: str | None = Field(None, alias="credentialRef", min_length=1, max_length=160)

    @model_validator(mode="after")
    def bounded_credentials(self):
        if (
            self.api_key is not None
            and len(self.api_key.get_secret_value().encode("utf-8")) > MAX_SECRET_BYTES
        ):
            raise ValueError("channel credential exceeds limit")
        validate_channel_headers(self.headers)
        return self


class CanvasChannelModelsRead(InputModel):
    models: list[dict[str, JsonValue]]


class CanvasPluginCatalogRead(InputModel):
    providers: list[dict[str, JsonValue]]
