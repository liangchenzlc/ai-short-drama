"""Absolute millisecond sound edits; no implicit video or timing changes."""

from typing import Annotated

from pydantic import Field, model_validator

from .base import Identifier, InputModel, NonnegativeVersion

Milliseconds = Annotated[int, Field(strict=True, ge=0, le=3600000)]


class Subtitle(InputModel):
    start_ms: Milliseconds
    end_ms: Milliseconds
    text: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid(self):
        if self.end_ms <= self.start_ms or not self.text.strip():
            raise ValueError("字幕须有内容且结束时间晚于开始时间")
        if any(ord(c) < 32 and c != "\n" for c in self.text):
            raise ValueError("字幕包含不可见控制字符")
        return self


class Dialogue(InputModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    character: str = Field(default="旁白", min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4096)
    voice: str = Field(default="", max_length=128)
    config_id: Identifier | None = None
    start_ms: Milliseconds = 0
    media_id: Identifier | None = None
    adopted_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Music(InputModel):
    media_id: Identifier
    start_ms: Milliseconds = 0
    trim_in_ms: Milliseconds = 0
    trim_out_ms: Milliseconds
    volume: float = Field(default=0.2, ge=0, le=2, allow_inf_nan=False)
    loop: bool = False
    fade_in_ms: Milliseconds = 500
    fade_out_ms: Milliseconds = 500
    ducking: bool = True

    @model_validator(mode="after")
    def valid(self):
        if self.trim_out_ms <= self.trim_in_ms:
            raise ValueError("配乐裁剪终点必须晚于起点")
        return self


class SoundDocument(InputModel):
    native_ducking: list[Subtitle] = Field(default_factory=list, max_length=300)
    dialogue: list[Dialogue] = Field(default_factory=list, max_length=300)
    subtitles: list[Subtitle] = Field(default_factory=list, max_length=2000)
    music: Music | None = None
    original_volume: float = Field(default=1, ge=0, le=2, allow_inf_nan=False)
    dialogue_volume: float = Field(default=1, ge=0, le=2, allow_inf_nan=False)
    burn_subtitles: bool = False
    font_size: int = Field(default=24, strict=True, ge=12, le=72)

    @model_validator(mode="after")
    def valid(self):
        if len({d.id for d in self.dialogue}) != len(self.dialogue):
            raise ValueError("台词标识不可重复")
        for left, right in zip(self.subtitles, self.subtitles[1:], strict=False):
            if left.end_ms > right.start_ms:
                raise ValueError("字幕必须按时间排序且不能重叠")
        return self


class SoundEdit(InputModel):
    row_version: NonnegativeVersion
    timeline_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=1, max_length=128)
    document: SoundDocument
    reviewed: bool = False


class SoundAdopt(InputModel):
    row_version: Identifier
    line_id: str
    media_id: Identifier


class VoiceDefaultsEdit(InputModel):
    row_version: NonnegativeVersion
    voices: dict[str, str] = Field(max_length=300)

    @model_validator(mode="after")
    def valid(self):
        if any(
            not k.strip() or len(k) > 100 or not v.strip() or len(v) > 128
            for k, v in self.voices.items()
        ):
            raise ValueError("角色名和音色必须非空且在长度限制内")
        return self
