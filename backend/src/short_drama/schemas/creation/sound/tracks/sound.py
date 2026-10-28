"""Absolute millisecond sound edits; no implicit video or timing changes."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion

Milliseconds = Annotated[int, Field(strict=True, ge=0, le=3600000)]


class Subtitle(InputModel):
    id: str | None = Field(default=None, max_length=64)
    anchor_clip_id: str | None = Field(default=None, max_length=64)
    anchor_source_in_ms: Milliseconds | None = None
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


class AudioClip(Music):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    dialogue_id: str | None = Field(default=None, max_length=64)
    loop_offset_ms: Milliseconds = 0
    kind: Literal["original", "dialogue", "music", "effect"] = "music"
    name: str = Field(default="声音", max_length=255)
    duration_ms: Milliseconds
    anchor_clip_id: str | None = Field(default=None, max_length=64)
    anchor_source_in_ms: Milliseconds | None = None
    muted: bool = False
    gain_db: float = Field(default=0, ge=-60, le=24, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_duration(self):
        if self.duration_ms < 1 or (
            not self.loop and self.duration_ms > self.trim_out_ms - self.trim_in_ms + 1
        ):
            raise ValueError("声音片段时长超出来源范围")
        if self.start_ms + self.duration_ms > 3600000:
            raise ValueError("声音时间超出一小时")
        return self


class SubtitleStyle(InputModel):
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    outline_width: float = Field(default=2, ge=0, le=8, allow_inf_nan=False)
    background: bool = False
    background_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    background_opacity: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    alignment: int = Field(default=2, ge=1, le=9, strict=True)
    margin_percent: float = Field(default=5, ge=0, le=30, allow_inf_nan=False)
    bold: bool = False


class SoundTrack(InputModel):
    id: Literal["original", "dialogue", "subtitles", "music", "effect"]
    muted: bool = False
    locked: bool = False


class SoundDocument(InputModel):
    schema_version: Literal[1, 2] = 1
    picture_locked: bool = False
    audio_clips: list[AudioClip] = Field(default_factory=list, max_length=600)
    tracks: list[SoundTrack] = Field(default_factory=list, max_length=5)
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
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
        if len({c.id for c in self.audio_clips}) != len(self.audio_clips):
            raise ValueError("声音片段标识不可重复")
        if len({t.id for t in self.tracks}) != len(self.tracks):
            raise ValueError("声音轨道标识不可重复")
        ids = [s.id for s in self.subtitles if s.id]
        if len(set(ids)) != len(ids):
            raise ValueError("字幕标识不可重复")
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
