"""固定源 TimelineProject v2 与语义渲染计划；UI 扩展字段由边界忽略。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from pydantic.alias_generators import to_camel

from short_drama.schemas.canvas.document.canvas import CanvasKey

TimelineInteger = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]


class TimelineInput(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, alias_generator=to_camel)

    @model_validator(mode="before")
    @classmethod
    def null_scalars_keep_go_defaults(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        value = dict(raw)
        for name, field in cls.model_fields.items():
            key = field.alias or name
            if (
                key in value
                and value[key] is None
                and (
                    field.default_factory is not None
                    or (not field.is_required() and field.default is not None)
                )
            ):
                del value[key]
        return value


class TimelineTrack(TimelineInput):
    id: str = ""
    kind: str = ""
    visible: bool | None = None
    muted: bool = False


class TimelineDirectMedia(TimelineInput):
    id: str = ""
    kind: str = ""
    storage_key: str = ""


class TimelineClip(TimelineInput):
    id: str = ""
    kind: str = ""
    node_id: str = ""
    track_id: str = ""
    start_ms: TimelineInteger = 0
    duration_ms: TimelineInteger = 0
    source_start_ms: TimelineInteger = 0
    source_duration_ms: TimelineInteger = 0
    volume: float = Field(default=1, allow_inf_nan=False)
    fade_in_ms: TimelineInteger = 0
    fade_out_ms: TimelineInteger = 0
    text: str = ""
    direct_media: TimelineDirectMedia | None = None


class TimelineProject(TimelineInput):
    version: TimelineInteger = 0
    tracks: list[TimelineTrack] = Field(default_factory=list)
    clips: list[TimelineClip] = Field(default_factory=list)
    duration_ms: TimelineInteger = 0


class TimelineSource(TimelineInput):
    id: str = ""
    kind: str = ""
    has_audio: bool | None = None
    has_video: bool | None = None
    duration_ms: TimelineInteger = 0


class TimelineOptions(TimelineInput):
    width: TimelineInteger = 0
    height: TimelineInteger = 0
    fps: TimelineInteger = 0
    sample_rate: TimelineInteger = 0
    burn_subtitles: bool | None = None


class CanvasTimelinePlanRequest(TimelineInput):
    timeline: TimelineProject = Field(default_factory=TimelineProject)
    sources: list[TimelineSource] | None = None
    options: TimelineOptions = Field(default_factory=TimelineOptions)


class CanvasTimelineRenderRequest(TimelineInput):
    project_id: CanvasKey
    client_operation_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    timeline: TimelineProject
    options: TimelineOptions = Field(default_factory=TimelineOptions)


class TimelineOutput(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, strict=True)


class TimelinePlanOutput(TimelineOutput):
    width: int
    height: int
    fps: int
    sample_rate: int
    burn_subtitles: bool


class TimelineSegment(TimelineOutput):
    kind: Literal["video", "image", "gap"]
    clip_id: str = ""
    source_id: str = ""
    start_ms: int
    duration_ms: int
    source_start_ms: int = 0
    volume: float = 1
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    muted: bool = False
    has_audio: bool = False

    @model_serializer(mode="wrap")
    def omit_go_optional_fields(self, handler):
        value = handler(self)
        for field in (
            "clip_id",
            "source_id",
            "source_start_ms",
            "fade_in_ms",
            "fade_out_ms",
            "muted",
        ):
            if not getattr(self, field):
                value.pop(field, None)
                value.pop(to_camel(field), None)
        return value


class TimelineAudioClip(TimelineOutput):
    clip_id: str
    source_id: str
    start_ms: int
    duration_ms: int
    source_start_ms: int
    volume: float
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    muted: bool = False

    @model_serializer(mode="wrap")
    def omit_go_optional_fields(self, handler):
        value = handler(self)
        for field in ("fade_in_ms", "fade_out_ms", "muted"):
            if not getattr(self, field):
                value.pop(field, None)
                value.pop(to_camel(field), None)
        return value


class TimelineSubtitle(TimelineOutput):
    clip_id: str
    start_ms: int
    duration_ms: int
    text: str


class CanvasTimelinePlanRead(TimelineOutput):
    version: Literal[1] = 1
    output: TimelinePlanOutput
    duration_ms: int
    segments: list[TimelineSegment]
    audio: list[TimelineAudioClip] | None = None
    subtitles: list[TimelineSubtitle] | None = None
    subtitle_srt: str = ""

    @model_serializer(mode="wrap")
    def omit_empty_srt(self, handler):
        value = handler(self)
        if not self.subtitle_srt:
            value.pop("subtitle_srt", None)
            value.pop("subtitleSrt", None)
        return value
