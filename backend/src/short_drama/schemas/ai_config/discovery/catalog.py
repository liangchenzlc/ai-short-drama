from typing import Annotated, Literal

from pydantic import Field, SecretStr

from short_drama.schemas.base import Identifier, InputModel, ReadModel


class ModelDiscoveryRequest(InputModel):
    base_url: Annotated[str, Field(min_length=1, max_length=2048)]
    apikey: SecretStr | None = None
    config_id: Identifier | None = None
    service_type: Literal["text", "image", "video", "audio"] = "text"
    interface_type: Annotated[str, Field(min_length=1, max_length=120)] = "auto"


class DiscoveredModel(ReadModel):
    id: str


class ModelDiscoveryRead(ReadModel):
    items: list[DiscoveredModel]
    truncated: bool = False
    source: Literal["provider", "builtin"] = "provider"
    status: Literal["available", "unavailable"] = "available"
