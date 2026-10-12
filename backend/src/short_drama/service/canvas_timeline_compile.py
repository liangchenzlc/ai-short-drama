"""移植固定源 internal/editing：纯编译，不读取资源、不创建任务或访问数据库。"""

from collections.abc import Sequence
from typing import NoReturn

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_timeline import (
    CanvasTimelinePlanRead,
    CanvasTimelinePlanRequest,
    TimelineAudioClip,
    TimelineClip,
    TimelineOptions,
    TimelinePlanOutput,
    TimelineSegment,
    TimelineSource,
    TimelineSubtitle,
    TimelineTrack,
)

MAX_DURATION_MS = 12 * 60 * 60 * 1000
DURATION_TOLERANCE_MS = 100
# Go unicode.IsSpace 不包含 Python strip 额外移除的 U+001C–001F。
GO_WHITESPACE = (
    " \t\n\v\f\r\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
ERROR_MESSAGES = {
    "no_media": "时间线没有可渲染的媒体片段",
    "overlap": "服务端渲染暂不支持重叠的视频或图片片段",
    "negative_time": "片段时间不能为负数",
    "missing_source": "找不到素材",
    "missing_media_ref": "片段缺少有效媒体引用",
    "insufficient_duration": "素材时长不足，请调整裁剪范围",
    "missing_audio_track": "独立音频素材没有可用音轨",
    "missing_video_track": "素材缺少所需音视频轨",
    "invalid_clip": "片段时间无效",
    "invalid_volume": "片段裁剪或音量无效",
    "unsupported_version": "不支持的时间线版本",
    "duplicate_id": "时间线存在重复的轨道或片段",
    "invalid_id": "轨道或片段缺少有效编号",
    "unknown_track": "片段引用了不存在的轨道",
    "unsupported_kind": "不支持的片段类型",
    "invalid_output": "输出尺寸、帧率或采样率无效",
    "invalid_duration": "时间线或片段时长无效",
}


def go_trim(value: str) -> str:
    return value.strip(GO_WHITESPACE)


def fail(reason: str, context: str | None = None) -> NoReturn:
    message = ERROR_MESSAGES[reason]
    if context is not None:
        message += "：" + context
    raise WorkflowError("canvas_timeline_" + reason, message, 400)


def normalize_options(options: TimelineOptions) -> TimelinePlanOutput:
    width, height = options.width or 1920, options.height or 1080
    fps, sample_rate = options.fps or 30, options.sample_rate or 44100
    if (
        not 2 <= width <= 7680
        or width % 2
        or not 2 <= height <= 4320
        or height % 2
        or not 1 <= fps <= 120
        or not 8000 <= sample_rate <= 96000
    ):
        fail("invalid_output")
    return TimelinePlanOutput(
        width=width,
        height=height,
        fps=fps,
        sample_rate=sample_rate,
        burn_subtitles=options.burn_subtitles is not False,
    )


def checked_end(start_ms: int, duration_ms: int, context: str | None = None) -> int:
    if start_ms < 0 or duration_ms < 0:
        fail("negative_time", context)
    end = start_ms + duration_ms
    if end > MAX_DURATION_MS:
        fail("invalid_duration", context)
    return end


def check_ids(values: Sequence[TimelineClip | TimelineTrack | TimelineSource]) -> None:
    seen: set[str] = set()
    for value in values:
        identifier = go_trim(value.id)
        if not identifier:
            fail("invalid_id")
        if identifier in seen:
            fail("duplicate_id", identifier)
        seen.add(identifier)


def media_resource_id(clip: TimelineClip) -> str:
    key = go_trim(clip.direct_media.storage_key) if clip.direct_media is not None else ""
    return go_trim(key.removeprefix("resource:")) if key.startswith("resource:") else ""


def resolve_source(
    clip: TimelineClip, sources: dict[str, TimelineSource] | None
) -> tuple[str, TimelineSource]:
    resource_id = media_resource_id(clip)
    if sources is None:
        if not resource_id:
            fail("missing_media_ref", clip.id)
        return resource_id, TimelineSource(id=resource_id)
    candidates = [go_trim(clip.node_id)]
    if resource_id:
        candidates += [resource_id, "resource:" + resource_id]
    if clip.direct_media is not None:
        candidates.append(go_trim(clip.direct_media.id))
    for identifier in candidates:
        if identifier and identifier in sources:
            return identifier, sources[identifier]
    fail("missing_source", clip.id)


def check_source_timing(clip: TimelineClip, source: TimelineSource) -> None:
    if (
        clip.kind != "image"
        and source.duration_ms > 0
        and clip.source_start_ms + clip.duration_ms > source.duration_ms + DURATION_TOLERANCE_MS
    ):
        fail("insufficient_duration", clip.id)


def gap(start_ms: int, duration_ms: int) -> TimelineSegment:
    return TimelineSegment(kind="gap", start_ms=start_ms, duration_ms=duration_ms)


def srt_timestamp(milliseconds: int) -> str:
    hours, rest = divmod(max(0, milliseconds), 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def compile_timeline_plan(request: CanvasTimelinePlanRequest) -> CanvasTimelinePlanRead:
    output = normalize_options(request.options)
    project = request.timeline
    if project.version != 2:
        fail("unsupported_version")
    checked_end(0, project.duration_ms)
    check_ids(project.tracks)
    check_ids(project.clips)
    sources = None
    if request.sources is not None:
        check_ids(request.sources)
        sources = {go_trim(source.id): source for source in request.sources}
    tracks = {track.id: track for track in project.tracks}
    visual: list[TimelineClip] = []
    audio: list[TimelineAudioClip] = []
    subtitles: list[TimelineSubtitle] = []
    end = project.duration_ms
    for clip in project.clips:
        track = tracks.get(clip.track_id)
        if tracks:
            if track is None:
                fail("unknown_track", clip.track_id)
            if track.visible is False:
                continue
        if min(clip.start_ms, clip.source_start_ms, clip.fade_in_ms, clip.fade_out_ms) < 0:
            fail("negative_time")
        if clip.volume < 0:
            fail("invalid_volume", clip.id)
        clip_end = checked_end(clip.start_ms, clip.duration_ms, clip.id)
        if clip.duration_ms > 0:
            end = max(end, clip_end)
        if clip.kind == "audio":
            if clip.duration_ms <= 0:
                fail("invalid_clip", clip.id)
            identifier, source = resolve_source(clip, sources)
            check_source_timing(clip, source)
            if source.has_audio is False:
                fail("missing_audio_track")
            audio.append(
                TimelineAudioClip(
                    clip_id=clip.id,
                    source_id=identifier,
                    start_ms=clip.start_ms,
                    duration_ms=clip.duration_ms,
                    source_start_ms=clip.source_start_ms,
                    volume=clip.volume,
                    fade_in_ms=clip.fade_in_ms,
                    fade_out_ms=clip.fade_out_ms,
                    muted=track.muted if track else False,
                )
            )
        elif clip.kind in {"video", "image"}:
            if clip.duration_ms <= 0:
                fail("invalid_clip", clip.id)
            visual.append(clip)
        elif clip.kind == "subtitle":
            text = go_trim(clip.text)
            if not text or clip.duration_ms <= 0:
                fail("invalid_clip", clip.id)
            subtitles.append(
                TimelineSubtitle(
                    clip_id=clip.id,
                    start_ms=clip.start_ms,
                    duration_ms=clip.duration_ms,
                    text=text,
                )
            )
        else:
            fail("unsupported_kind", clip.kind)
    visual.sort(key=lambda clip: (clip.start_ms, clip.id))
    audio.sort(key=lambda clip: (clip.start_ms, clip.clip_id))
    subtitles.sort(key=lambda clip: (clip.start_ms, clip.clip_id))
    cursor = 0
    segments: list[TimelineSegment] = []
    for clip in visual:
        if clip.start_ms < cursor:
            fail("overlap")
        if clip.start_ms > cursor:
            segments.append(gap(cursor, clip.start_ms - cursor))
        identifier, source = resolve_source(clip, sources)
        check_source_timing(clip, source)
        track = tracks.get(clip.track_id)
        muted = track.muted if track else False
        has_audio = clip.kind == "video" and not muted and source.has_audio is not False
        if clip.kind == "video" and source.has_video is False:
            fail("missing_video_track", clip.id)
        segments.append(
            TimelineSegment(
                kind=clip.kind,
                clip_id=clip.id,
                source_id=identifier,
                start_ms=clip.start_ms,
                duration_ms=clip.duration_ms,
                source_start_ms=clip.source_start_ms,
                volume=clip.volume,
                fade_in_ms=clip.fade_in_ms,
                fade_out_ms=clip.fade_out_ms,
                muted=muted,
                has_audio=has_audio,
            )
        )
        cursor = checked_end(clip.start_ms, clip.duration_ms, clip.id)
    if end > cursor:
        segments.append(gap(cursor, end - cursor))
    if not visual and not audio:
        fail("no_media")
    srt = ""
    if output.burn_subtitles:
        srt = "".join(
            f"{index}\n{srt_timestamp(clip.start_ms)} --> "
            f"{srt_timestamp(clip.start_ms + clip.duration_ms)}\n{clip.text}\n\n"
            for index, clip in enumerate(subtitles, 1)
        )
    else:
        subtitles = []
    return CanvasTimelinePlanRead(
        output=output,
        duration_ms=sum(segment.duration_ms for segment in segments),
        segments=segments,
        audio=audio or None,
        subtitles=subtitles or None,
        subtitle_srt=srt,
    )
