"""画布专用、可冻结的供应商参数；不扩展标准模式输入。"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator

from short_drama.schemas.base import Identifier, InputModel


class CanvasAudioParameters(InputModel):
    mode: Literal["audio"]
    format: Literal["mp3", "wav", "opus", "aac", "flac", "pcm"] = "mp3"
    speed: float = Field(default=1.0, strict=True, ge=0, le=4, allow_inf_nan=False)
    instructions: str = Field(default="", strict=True, max_length=4096)

    @field_validator("speed")
    @classmethod
    def source_speed_or_fallback(cls, value: float) -> float:
        if 0 < value < 0.25:
            raise ValueError("audio speed must be zero or between 0.25 and 4")
        return value


class CanvasAudioProviderOptions(InputModel):
    voice: str | None = Field(default=None, strict=True, max_length=256)
    response_format: Literal["mp3", "wav", "opus", "aac", "flac", "pcm"] | None = None
    speed: float | None = Field(default=None, strict=True, ge=0.25, le=4, allow_inf_nan=False)
    instructions: str | None = Field(default=None, strict=True, max_length=4096)


class CanvasImageParameters(InputModel):
    mode: Literal["image"]
    size: str = Field(
        default="1:1",
        strict=True,
        max_length=64,
        pattern=r"^(?:auto|[124]k|[1-9][0-9]{0,4}:[1-9][0-9]{0,4}|[1-9][0-9]{0,4}x[1-9][0-9]{0,4})?$",
    )
    quality: Literal["auto", "low", "medium", "high", "1k", "2k", "4k", "standard", "hd"] = "auto"
    transparent_background: bool = Field(default=False, strict=True)
    mask_media_id: Identifier | None = None


class CanvasVideoParameters(InputModel):
    mode: Literal["video"]
    generate_audio: bool | None = Field(default=None, strict=True)
    watermark: bool | None = Field(default=None, strict=True)


CANVAS_PARAMETERS = TypeAdapter(
    Annotated[
        CanvasAudioParameters | CanvasImageParameters | CanvasVideoParameters,
        Field(discriminator="mode"),
    ]
)
