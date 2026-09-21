from typing import Literal

from pydantic import model_validator

from .base import Identifier, InputModel, ReadModel


class StoryboardApply(InputModel):
    mode: Literal["append", "replace"]
    content_version: Identifier
    storyboard_version: Identifier
    confirm_replace: bool = False

    @model_validator(mode="after")
    def explicit_replace(self):
        if self.mode == "replace" and not self.confirm_replace:
            raise ValueError("Replacing shots requires explicit confirmation")
        return self


class StoryboardApplied(ReadModel):
    generation_id: Identifier
    mode: Literal["append", "replace"]
    shot_ids: list[Identifier]
    storyboard_version: Identifier
    already_applied: bool
