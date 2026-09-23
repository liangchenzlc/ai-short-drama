"""Strict model-output contract; never guesses IDs or partially accepts a batch."""

import json
import re
from typing import Annotated

from pydantic import AfterValidator, AliasChoices, Field, model_validator

from .base import Identifier, InputModel, nonblank


def shot_text(value):
    nonblank(value)
    if len(value.encode("utf-8")) > 32768:
        raise ValueError("Shot exceeds 32 KiB")
    return value


class GeneratedShot(InputModel):
    title: Annotated[str, Field(max_length=255), AfterValidator(nonblank)]
    source_excerpt: Annotated[str, Field(max_length=8000), AfterValidator(nonblank)]
    story_beat: Annotated[str, Field(max_length=8000), AfterValidator(nonblank)]
    script: Annotated[str, AfterValidator(shot_text)] = Field(
        validation_alias=AliasChoices("script", "visual_script")
    )
    duration_ms: int = Field(strict=True, ge=1000, le=10000)
    asset_ids: list[Identifier] = Field(max_length=50)

    @model_validator(mode="after")
    def unique_assets(self):
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("Duplicate asset references")
        return self


class StoryboardResult(InputModel):
    shots: list[GeneratedShot] = Field(min_length=1, max_length=100)


def parse_storyboard_result(content: str, allowed_asset_ids: set[int], source_content: str) -> dict:
    if len(content.encode("utf-8")) > 1048576:
        raise ValueError("Structured output exceeds 1 MiB")
    stripped = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", stripped)
    if fence:
        stripped = fence.group(1)
    result = StoryboardResult.model_validate(json.loads(stripped))
    if any(set(shot.asset_ids) - allowed_asset_ids for shot in result.shots):
        raise ValueError("unknown_asset_reference")
    previous_position = 0
    for shot in result.shots:
        position = source_content.find(shot.source_excerpt, previous_position)
        if position < 0:
            raise ValueError("unverified_or_reordered_source_excerpt")
        previous_position = position
    return result.model_dump(mode="json")
