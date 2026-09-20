from typing import Annotated

from pydantic import Field, SecretStr

from .base import Identifier, InputModel, ReadModel


class ModelDiscoveryRequest(InputModel):
    base_url: Annotated[str, Field(min_length=1, max_length=2048)]
    apikey: SecretStr | None = None
    config_id: Identifier | None = None


class DiscoveredModel(ReadModel):
    id: str


class ModelDiscoveryRead(ReadModel):
    items: list[DiscoveredModel]
    truncated: bool = False
