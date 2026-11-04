"""Versioned, partial account preferences for the creative assistant."""

from typing import Annotated

from pydantic import Field, field_validator, model_validator

from short_drama.schemas.base import InputModel, NonnegativeVersion, ReadModel


class AssistantSettingsPatch(InputModel):
    custom_instructions: Annotated[str, Field(max_length=16000)] | None = None
    workbench_enabled: Annotated[bool, Field(strict=True)] = None
    media_generation_enabled: Annotated[bool, Field(strict=True)] = None
    long_term_memory_enabled: Annotated[bool, Field(strict=True)] = None
    settings_version: NonnegativeVersion

    @field_validator("custom_instructions")
    @classmethod
    def normalize_instructions(cls, value):
        return value if value and value.strip() else None

    @model_validator(mode="after")
    def require_changes(self):
        if self.model_fields_set == {"settings_version"}:
            raise ValueError("至少修改一个设置项")
        return self


class AssistantRuntimeLimits(ReadModel):
    subagent_max_concurrency: int


class AssistantSettingsRead(ReadModel):
    custom_instructions: str | None
    default_instructions: str
    workbench_enabled: bool
    media_generation_enabled: bool
    long_term_memory_enabled: bool
    settings_version: NonnegativeVersion
