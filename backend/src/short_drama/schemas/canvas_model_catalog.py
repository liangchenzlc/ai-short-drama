"""原渠道目录合同；凭据只允许出现在显式秘密字段中。"""

import json
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, ConfigDict, Field, JsonValue, SecretStr, model_validator

from .base import Identifier, InputModel, nonblank

TextKey = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(nonblank)]
ModelKey = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(nonblank)]
SECRET_KEYS = frozenset({"apikey", "secretkey", "authorization", "cookie", "password", "token"})
MAX_SECRET_BYTES = 16384
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CHANNEL_HEADERS = 32
MAX_HEADER_VALUE_BYTES = 4096
MAX_HEADER_TOTAL_BYTES = 16 * 1024


def validate_channel_url(value: str) -> None:
    if not value:
        return
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or parts.netloc.endswith(":")
            or parts.port is not None
            and not 1 <= parts.port <= 65535
        ):
            raise ValueError
        parts.hostname.encode("idna")
    except (ValueError, UnicodeError):
        raise ValueError(
            "channel URL requires a valid HTTP origin/path without credentials"
        ) from None


def validate_public_json(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if re.sub(r"[^a-z]", "", key.lower()) in SECRET_KEYS:
                raise ValueError("credentials require explicit secret fields")
            validate_public_json(item)
    elif isinstance(value, list):
        for item in value:
            validate_public_json(item)
    elif isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("catalog numbers must be finite")


class CanvasChannelHeader(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    value: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def safe_header(self):
        self.name = self.name.strip()
        self.value = SecretStr(self.value.get_secret_value().strip())
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self.name):
            raise ValueError("invalid channel header name")
        self.name = "-".join(part.capitalize() for part in self.name.split("-"))
        if self.name.lower() in {
            "authorization",
            "host",
            "cookie",
            "set-cookie",
            "content-length",
            "content-type",
            "accept",
            "transfer-encoding",
            "connection",
            "proxy-connection",
            "keep-alive",
            "proxy-authorization",
            "proxy-authenticate",
            "upgrade",
            "te",
            "trailer",
            "forwarded",
            "x-goog-api-key",
        } or self.name.lower().startswith(("x-canvas-", "x-forwarded-")):
            raise ValueError("unsafe channel header")
        if any(
            (ord(char) < 32 and char != "\t") or ord(char) == 127
            for char in self.value.get_secret_value()
        ):
            raise ValueError("invalid channel header value")
        if len(self.value.get_secret_value().encode("utf-8")) > MAX_HEADER_VALUE_BYTES:
            raise ValueError("channel header exceeds secret byte limit")
        return self


def validate_channel_headers(headers: list[CanvasChannelHeader]) -> None:
    if len(headers) > MAX_CHANNEL_HEADERS:
        raise ValueError("too many channel headers")
    names = [item.name.lower() for item in headers]
    if len(set(names)) != len(names):
        raise ValueError("duplicate channel header")
    total = sum(
        len(item.name.encode("ascii")) + len(item.value.get_secret_value().encode("utf-8"))
        for item in headers
    )
    if total > MAX_HEADER_TOTAL_BYTES:
        raise ValueError("channel headers exceed 16 KiB")


class CanvasModelProfile(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: ModelKey
    display_name: str | None = Field(None, alias="displayName", max_length=120)
    description: str | None = Field(None, max_length=65536)
    icon: str | None = Field(None, max_length=2048)
    capability: Literal["text", "image", "video", "audio", ""]
    protocol: str | None = Field(None, max_length=120)
    capability_config: dict[str, JsonValue] | None = Field(None, alias="capabilityConfig")
    video_capabilities_version: str | None = Field(
        None, alias="videoCapabilitiesVersion", max_length=128
    )
    logical_model_id: Identifier | None = Field(None, alias="logicalModelId")
    logical_capability_spec: dict[str, JsonValue] | None = Field(
        None, alias="logicalCapabilitySpec"
    )
    logical_capability_profiles: list[dict[str, JsonValue]] | None = Field(
        None, alias="logicalCapabilityProfiles"
    )
    default_options: dict[str, JsonValue] | None = Field(None, alias="defaultOptions")

    @model_validator(mode="after")
    def public_fields(self):
        validate_public_json(self.model_dump(mode="json", by_alias=True))
        return self


class CanvasModelChannelInput(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source_key: TextKey = Field(alias="id")
    name: Annotated[str, Field(min_length=1, max_length=120), AfterValidator(nonblank)]
    base_url: str = Field(alias="baseUrl", max_length=2048)
    api_key: SecretStr | None = Field(None, alias="apiKey")
    secret_key: SecretStr | None = Field(None, alias="secretKey")
    public_alias: str | None = Field(None, alias="publicAlias", max_length=128)
    sort_order: int | None = Field(None, alias="sortOrder", ge=-2147483648, le=2147483647)
    reference_asset_origin: str | None = Field(None, alias="referenceAssetOrigin", max_length=2048)
    headers: list[CanvasChannelHeader] = Field(default_factory=list, max_length=MAX_CHANNEL_HEADERS)
    api_format: Literal["openai", "gemini", "claude"] = Field("openai", alias="apiFormat")
    interface_type: str | None = Field(None, alias="interfaceType", max_length=120)
    models: list[ModelKey] = Field(default_factory=list, max_length=2000)
    model_aliases: dict[str, str] | None = Field(None, alias="modelAliases")
    scope: Literal["user", "system"] = "user"
    enabled: bool = True
    pinned: bool = False
    preset_version: int | None = Field(None, alias="presetVersion", ge=0)
    credential_ref: str | None = Field(None, alias="credentialRef", max_length=160)
    has_api_key: bool = Field(False, alias="hasApiKey")
    has_secret_key: bool = Field(False, alias="hasSecretKey")
    concurrency_limit: int | None = Field(None, alias="concurrencyLimit", ge=1, le=1024)
    model_profiles: list[CanvasModelProfile] = Field(
        default_factory=list, alias="modelProfiles", max_length=2000
    )
    clear_credentials: list[Literal["apiKey", "secretKey", "headers"]] = Field(
        default_factory=list, alias="clearCredentials"
    )

    @model_validator(mode="after")
    def channel_invariants(self):
        if self.source_key.startswith("host-"):
            raise ValueError("host channels are managed by the host model configuration")
        if self.source_key == "beefapi":
            if (
                self.scope != "user"
                or not self.pinned
                or self.credential_ref not in {None, "beefapi-enterprise"}
                or self.clear_credentials
                or any(
                    secret and secret.get_secret_value()
                    for secret in (self.api_key, self.secret_key)
                )
            ):
                raise ValueError("BeefAPI is a server-managed redacted channel")
            return self.validate_channel_fields()
        if self.scope != "user" or self.pinned:
            raise ValueError("a custom channel cannot impersonate a managed channel")
        if len(self.models) > 512 or len(self.model_profiles) > 512:
            raise ValueError("custom channels support up to 512 models")
        if self.credential_ref == "beefapi-enterprise" or any(
            not item.capability for item in self.model_profiles
        ):
            raise ValueError("a custom channel requires explicit generation capabilities")
        return self.validate_channel_fields()

    def validate_channel_fields(self):
        for field, url in (
            ("base_url", self.base_url),
            ("reference_asset_origin", self.reference_asset_origin),
        ):
            if url is not None:
                if (
                    field == "base_url"
                    and self.source_key.startswith("host-")
                    and url
                    == f"/api/v1/canvas-runtime/ai/models/{self.source_key.removeprefix('host-')}"
                ):
                    continue
                validate_channel_url(url)
        for secret in (self.api_key, self.secret_key):
            if (
                secret is not None
                and len(secret.get_secret_value().encode("utf-8")) > MAX_SECRET_BYTES
            ):
                raise ValueError("channel credential exceeds secret byte limit")
        profiles = [item.model for item in self.model_profiles]
        if len(set(self.models)) != len(self.models) or len(set(profiles)) != len(profiles):
            raise ValueError("duplicate channel model")
        if not set(self.models).issubset(profiles):
            raise ValueError("every selected model requires one explicit capability profile")
        validate_channel_headers(self.headers)
        return self


def validate_channels(channels: list[CanvasModelChannelInput]) -> None:
    if len({channel.source_key for channel in channels}) != len(channels):
        raise ValueError("duplicate channel identity")
    raw = []
    for channel in channels:
        item = channel.model_dump(mode="json", by_alias=True)
        item["apiKey"] = channel.api_key.get_secret_value() if channel.api_key else None
        item["secretKey"] = channel.secret_key.get_secret_value() if channel.secret_key else None
        item["headers"] = [
            {"name": header.name, "value": header.value.get_secret_value()}
            for header in channel.headers
        ]
        raw.append(item)
    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("model catalog exceeds 2 MiB")
