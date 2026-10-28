"""标准成片的原子编辑契约，时间与来源均由服务端重新校验。"""

from typing import Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, ReadModel
from short_drama.schemas.creation.assembly.document.assembly import AssemblyClipEdit
from short_drama.schemas.creation.sound.tracks.sound import SoundDocument


class FrameTransform(InputModel):
    fit: Literal["contain", "cover"] = "contain"
    scale: float = Field(default=1, ge=1, le=5, allow_inf_nan=False)
    x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)


class EditorClip(AssemblyClipEdit):
    media_id: Identifier | None = None
    source_type: Literal["shot", "video", "image"] = "shot"
    display_duration_ms: int | None = Field(default=None, ge=34, le=3600000, strict=True)
    transform: FrameTransform = Field(default_factory=FrameTransform)


class AssemblyEditorEdit(InputModel):
    schema_version: Literal[2] = 2
    row_version: Identifier
    sound_row_version: NonnegativeVersion
    request_id: str = Field(min_length=1, max_length=128)
    resolution: Literal["720p", "1080p"]
    aspect: Literal["auto", "16:9", "9:16"] = "auto"
    clips: list[EditorClip] = Field(max_length=300)
    sound: SoundDocument

    @model_validator(mode="after")
    def unique_clips(self):
        if len({clip.id for clip in self.clips}) != len(self.clips):
            raise ValueError("画面片段标识不能重复")
        return self


class MaterialImport(InputModel):
    media_id: Identifier


class TranscriptionCreate(InputModel):
    row_version: Identifier
    clip_id: str | None = Field(default=None, max_length=64)
    language: str = Field(default="", max_length=32, pattern=r"^[a-zA-Z-]*$")
    model_config_id: Identifier | None = None


class AudioAnalysisCreate(InputModel):
    media_id: Identifier
    trim_in_ms: int = Field(default=0, ge=0, le=3600000, strict=True)
    trim_out_ms: int | None = Field(default=None, ge=1, le=3600000, strict=True)


class AssemblyMaterialRead(ReadModel):
    media_id: Identifier
    name: str
    kind: Literal["image", "video", "audio"]
    duration_ms: int | None
    width: int | None
    height: int | None
    url: str
    poster: str | None
    has_audio: bool


class AssemblyMediaJobRead(ReadModel):
    id: Identifier
    kind: Literal["transcription", "audio_analysis"]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    stage: str
    progress: int
    can_cancel: bool
    can_retry: bool
    error: dict | None
    result: dict | None
