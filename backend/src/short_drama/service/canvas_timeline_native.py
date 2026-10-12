"""固定源原生参数构造与媒体事实绑定；只处理已编译计划及可信本机文件映射。"""

from collections.abc import Mapping
from dataclasses import dataclass

from short_drama.schemas.canvas_timeline import CanvasTimelinePlanRead

from .canvas_timeline_compile import DURATION_TOLERANCE_MS, fail

SUBTITLE_FILE_NAME = "render-subtitles.srt"
SUBTITLE_FONT_FAILURES = (
    "failed to find any fallback",
    "no usable fontconfig",
    "fontselect: failed",
    "can't find selected font provider",
    "couldn't find font family",
    "failed to find font",
    "no fonts found",
    "missing glyph",
)


@dataclass(frozen=True)
class SourceFacts:
    has_audio: bool
    has_video: bool
    duration_ms: int


def source_ids(plan: CanvasTimelinePlanRead) -> list[str]:
    identifiers = [segment.source_id for segment in plan.segments if segment.kind != "gap"]
    identifiers += [clip.source_id for clip in plan.audio or []]
    return list(dict.fromkeys(identifier for identifier in identifiers if identifier))


def apply_source_facts(plan: CanvasTimelinePlanRead, facts: Mapping[str, SourceFacts]) -> None:
    for segment in plan.segments:
        if segment.kind == "gap":
            segment.has_audio = False
            continue
        fact = facts.get(segment.source_id)
        if fact is None:
            fail("missing_source", segment.clip_id)
        if segment.kind == "image":
            segment.has_audio = False
            continue
        if not fact.has_video:
            fail("missing_video_track", segment.clip_id)
        if fact.duration_ms <= 0:
            raise_unreadable_source(segment.clip_id)
        if segment.source_start_ms + segment.duration_ms > fact.duration_ms + DURATION_TOLERANCE_MS:
            fail("insufficient_duration", segment.clip_id)
        segment.has_audio = fact.has_audio and not segment.muted
    for clip in plan.audio or []:
        fact = facts.get(clip.source_id)
        if fact is None:
            fail("missing_source", clip.clip_id)
        if not fact.has_audio:
            fail("missing_audio_track")
        if fact.duration_ms <= 0:
            raise_unreadable_source(clip.clip_id)
        if clip.source_start_ms + clip.duration_ms > fact.duration_ms + DURATION_TOLERANCE_MS:
            fail("insufficient_duration", clip.clip_id)


def raise_unreadable_source(clip_id: str) -> None:
    from short_drama.core.exceptions import WorkflowError

    raise WorkflowError("canvas_timeline_unreadable_source", "无法解析素材：" + clip_id, 400)


def subtitle_font_failure(output: str) -> bool:
    text = output.lower()
    return any(failure in text for failure in SUBTITLE_FONT_FAILURES)


def output_duration_within_plan(plan_ms: int, actual_ms: int, fps: int) -> bool:
    if plan_ms <= 0 or actual_ms <= 0:
        return False
    fps = fps if fps > 0 else 30
    tolerance = max(250, (1000 + fps - 1) // fps + 100)
    return abs(plan_ms - actual_ms) <= tolerance


def audio_fades(fade_in_ms: int, fade_out_ms: int, duration_ms: int) -> str:
    filters = ""
    duration = min(fade_in_ms, duration_ms)
    if duration > 0:
        filters += f",afade=t=in:st=0:d={duration / 1000:.3f}"
    duration = min(fade_out_ms, duration_ms)
    if duration > 0:
        filters += f",afade=t=out:st={(duration_ms - duration) / 1000:.3f}:d={duration / 1000:.3f}"
    return filters


def build_ffmpeg_args(
    plan: CanvasTimelinePlanRead, files: Mapping[str, str], target: str
) -> list[str]:
    if not plan.segments:
        fail("no_media")
    width, height = plan.output.width or 1920, plan.output.height or 1080
    fps, rate = plan.output.fps or 30, plan.output.sample_rate or 44100
    args = ["-nostdin", "-y"]
    filters: list[str] = []
    labels = ""
    for index, segment in enumerate(plan.segments):
        duration = segment.duration_ms / 1000
        seconds = f"{duration:.3f}"
        path = files.get(segment.source_id, "")
        if segment.kind != "gap" and not path:
            fail("missing_source", segment.clip_id)
        if segment.kind == "gap":
            args += [
                "-f",
                "lavfi",
                "-t",
                seconds,
                "-i",
                f"color=c=black:s={width}x{height}:r={fps}",
            ]
        elif segment.kind == "image":
            args += ["-loop", "1", "-t", seconds, "-i", path]
        else:
            args += ["-ss", f"{segment.source_start_ms / 1000:.3f}", "-t", seconds, "-i", path]
        if segment.kind == "video" and segment.has_audio and not segment.muted:
            args += ["-ss", f"{segment.source_start_ms / 1000:.3f}", "-t", seconds, "-i", path]
        else:
            args += ["-f", "lavfi", "-t", seconds, "-i", f"anullsrc=r={rate}:cl=stereo"]
        fades = audio_fades(segment.fade_in_ms, segment.fade_out_ms, segment.duration_ms)
        filters += [
            f"[{2 * index}:v]setpts=PTS-STARTPTS,fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={duration:.3f},trim=duration={duration:.3f}[v{index}]",
            f"[{2 * index + 1}:a]aformat=sample_fmts=fltp:sample_rates={rate}:"
            "channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,volume={segment.volume:.3f},apad,atrim=duration={duration:.3f}"
            f"{fades}[a{index}]",
        ]
        labels += f"[v{index}][a{index}]"
    filters.append(f"{labels}concat=n={len(plan.segments)}:v=1:a=1[vbase][abase]")
    mix = "[abase]"
    for index, clip in enumerate(plan.audio or []):
        path = files.get(clip.source_id, "")
        if not path:
            fail("missing_source", clip.clip_id)
        args += [
            "-ss",
            f"{clip.source_start_ms / 1000:.3f}",
            "-t",
            f"{clip.duration_ms / 1000:.3f}",
            "-i",
            path,
        ]
        volume = 0 if clip.muted else clip.volume
        fades = audio_fades(clip.fade_in_ms, clip.fade_out_ms, clip.duration_ms)
        filters.append(
            f"[{2 * len(plan.segments) + index}:a]aformat=sample_fmts=fltp:sample_rates={rate}:"
            f"channel_layouts=stereo,asetpts=PTS-STARTPTS,volume={volume:.3f},apad,"
            f"atrim=duration={clip.duration_ms / 1000:.3f}{fades},"
            f"adelay={clip.start_ms}:all=1[extra{index}]"
        )
        mix += f"[extra{index}]"
    filters.append(
        f"{mix}amix=inputs={len(plan.audio or []) + 1}:duration=first:normalize=0,"
        f"atrim=duration={plan.duration_ms / 1000:.3f}[aout]"
    )
    filters.append(
        f"[vbase]subtitles=filename={SUBTITLE_FILE_NAME}[vout]"
        if plan.output.burn_subtitles and plan.subtitle_srt
        else "[vbase]null[vout]"
    )
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-t",
        f"{plan.duration_ms / 1000:.3f}",
        target,
    ]
    return args
