"""One account media switch; tools can read it but cannot grant generation authority."""

from typing import Literal

from short_drama.schemas.base import InputModel, NonnegativeVersion, ReadModel

MediaKind = Literal["image", "video", "audio"]


class GenerationPolicyRead(ReadModel):
    workbench_enabled: bool
    long_term_memory_enabled: bool
    media_generation_enabled: bool
    settings_version: NonnegativeVersion
    configured_media: list[MediaKind]
    available_media: list[MediaKind]


class ReadGenerationPolicy(InputModel):
    pass
