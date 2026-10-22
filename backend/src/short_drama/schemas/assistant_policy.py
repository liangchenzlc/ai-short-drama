"""One account media switch; tools can read it but cannot grant generation authority."""

from typing import Literal

from pydantic import StrictBool

from short_drama.schemas.base import InputModel, NonnegativeVersion, ReadModel

MediaKind = Literal["image", "video", "audio"]


class GenerationPolicyPatch(InputModel):
    media_generation_enabled: StrictBool
    policy_version: NonnegativeVersion


class GenerationPolicyRead(ReadModel):
    media_generation_enabled: bool
    policy_version: NonnegativeVersion
    configured_media: list[MediaKind]
    available_media: list[MediaKind]


class ReadGenerationPolicy(InputModel):
    pass
