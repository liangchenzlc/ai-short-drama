"""宿主模型的公开运行协议；凭据只能出现在独立秘密字段。"""

import json
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .base import InputModel
from .canvas_model_catalog import validate_channel_url, validate_public_json


class ModelRuntimeProfile(InputModel):
    version: Literal[1] = 1
    api_format: Literal["openai", "gemini", "claude"] = "openai"
    protocol: str = Field(min_length=1, max_length=120)
    reference_asset_origin: str | None = Field(None, max_length=2048)
    capability_config: dict[str, JsonValue] | None = None
    default_options: dict[str, JsonValue] | None = None
    logical_capability_spec: dict[str, JsonValue] | None = None
    logical_capability_profiles: list[dict[str, JsonValue]] | None = Field(None, max_length=2000)
    video_capabilities_version: str | None = Field(None, max_length=128)
    concurrency_limit: int | None = Field(None, strict=True, ge=1, le=1024)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError("runtime profile version requires an integer")
        return value

    @field_validator("protocol")
    @classmethod
    def protocol_identity(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("runtime protocol cannot be blank")
        return value

    @model_validator(mode="after")
    def public_profile(self):
        data = self.model_dump(mode="json", exclude_none=True)
        validate_public_json(data)
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("runtime profile exceeds 2 MiB")
        if self.reference_asset_origin:
            validate_channel_url(self.reference_asset_origin)
        capability = self.capability_config or {}
        for kind in ("text", "image", "video"):
            section = capability.get(kind)
            if section is None:
                continue
            if not isinstance(section, dict):
                raise ValueError("model capability requires an object")
            for field in ("streaming", "durationSupported"):
                if field in section and type(section[field]) is not bool:
                    raise ValueError("capability switches require boolean values")
            references = section.get("references")
            if references is None:
                continue
            if not isinstance(references, dict):
                raise ValueError("capability references require an object")
            for field, value in references.items():
                if field.endswith("Supported"):
                    if type(value) is not bool:
                        raise ValueError("reference switches require boolean values")
                elif field.startswith(("min", "max")) or field == "promptMaxChars":
                    if type(value) not in {int, float} or value < 0:
                        raise ValueError("reference limits require nonnegative numbers")
                    if field in {"minImages", "maxImages", "maxVideos", "maxAudios"}:
                        if type(value) is not int or value > 100:
                            raise ValueError("reference counts require integers up to 100")
                    if field.endswith("Bytes") and (type(value) is not int or value > 2**53 - 1):
                        raise ValueError("reference byte limits require safe integers")
                    if field == "promptMaxChars" and (
                        type(value) is not int or not 1 <= value <= 1_000_000
                    ):
                        raise ValueError("prompt limit is outside the supported range")
        return self


class ModelRuntimeHeaderRead(InputModel):
    name: str
    has_value: bool
