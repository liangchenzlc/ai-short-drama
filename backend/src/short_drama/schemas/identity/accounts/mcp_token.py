"""MCP token input is validated once; public records never include stored hashes."""

from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from typing import Annotated

from pydantic import BaseModel, Field, StrictBool, field_serializer, field_validator

from short_drama.core.time import utcnow
from short_drama.schemas.base import Identifier, InputModel, ReadModel


class McpTokenCreate(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    workbench_enabled: StrictBool = False
    media_generation_enabled: StrictBool = False
    expires_at: datetime | None = None
    allowed_ips: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def trimmed_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("expires_at")
    @classmethod
    def future_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("有效期须为带时区的 UTC 未来时间")
        normalized = value.astimezone(UTC).replace(tzinfo=None)
        if normalized <= utcnow():
            raise ValueError("有效期须为未来时间")
        return normalized

    @field_validator("allowed_ips")
    @classmethod
    def canonical_ips(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(ip_network(value, strict=False)) if "/" in value else str(ip_address(value))
                for value in values
            )
        )


class McpTokenRead(ReadModel):
    id: Identifier
    name: str
    token_hint: str
    create_key: str
    tool_names: list[str]
    expires_at: datetime | None
    allowed_ips: list[str]
    created_at: datetime
    deleted_at: datetime | None

    @field_serializer("expires_at", "created_at", "deleted_at", when_used="json")
    def utc_time(self, value: datetime | None) -> str | None:
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z") if value else None


class McpTokenCreated(McpTokenRead):
    secret: str | None


class McpToolOption(ReadModel):
    name: str
    description: str
    modes: list[str]
    mutating: bool


class McpTokenOptions(ReadModel):
    connection_url: str
    transport: str = "streamable-http"
    tools: list[McpToolOption]
    capability_groups: dict[str, list[str]]


class McpWriteRequest[T: BaseModel](InputModel):
    request_id: Annotated[str, Field(min_length=1, max_length=64)]
    arguments: T
