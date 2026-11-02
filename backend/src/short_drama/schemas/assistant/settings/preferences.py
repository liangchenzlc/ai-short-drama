"""Atomic non-realtime settings for the single creative assistant."""

from typing import Annotated

from pydantic import Field

from short_drama.schemas.assistant.tasks.task import TaskMediaLimits
from short_drama.schemas.base import InputModel, NonnegativeVersion, ReadModel


class AssistantSettingsPatch(InputModel):
    custom_instructions: Annotated[str, Field(max_length=16000)] | None
    model_call_limit: Annotated[int, Field(strict=True, ge=20, le=2000)]
    output_token_limit: Annotated[int, Field(strict=True, ge=100000, le=20000000)]
    subagent_max_concurrency: Annotated[int, Field(strict=True, ge=1, le=3)]
    default_media_limits: TaskMediaLimits
    settings_version: NonnegativeVersion


class AssistantRuntimeLimits(ReadModel):
    model_call_limit: int
    output_token_limit: int
    subagent_max_concurrency: int


class AssistantSettingsRead(ReadModel):
    custom_instructions: str | None
    default_instructions: str
    model_call_limit: int
    output_token_limit: int
    subagent_max_concurrency: int
    default_media_limits: TaskMediaLimits
    settings_version: NonnegativeVersion
    deployment_limits: AssistantRuntimeLimits
