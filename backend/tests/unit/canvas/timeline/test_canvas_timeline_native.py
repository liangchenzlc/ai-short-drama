"""原生时间线必须使用已编译计划，并以真实探测事实校准音轨。"""

import pytest

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_timeline import CanvasTimelinePlanRequest
from short_drama.service.canvas_timeline_compile import compile_timeline_plan
from short_drama.service.canvas_timeline_native import (
    SourceFacts,
    apply_source_facts,
    build_ffmpeg_args,
    output_duration_within_plan,
    subtitle_font_failure,
)


def plan():
    return compile_timeline_plan(
        CanvasTimelinePlanRequest.model_validate(
            {
                "timeline": {
                    "version": 2,
                    "durationMs": 3000,
                    "tracks": [{"id": "v"}, {"id": "a"}],
                    "clips": [
                        {
                            "id": "first",
                            "kind": "video",
                            "trackId": "v",
                            "durationMs": 1000,
                            "directMedia": {"storageKey": "resource:tone"},
                        },
                        {
                            "id": "last",
                            "kind": "video",
                            "trackId": "v",
                            "startMs": 2000,
                            "durationMs": 1000,
                            "directMedia": {"storageKey": "resource:silent"},
                        },
                        {
                            "id": "voice",
                            "kind": "audio",
                            "trackId": "a",
                            "startMs": 100,
                            "durationMs": 900,
                            "sourceStartMs": 100,
                            "fadeInMs": 800,
                            "fadeOutMs": 100,
                            "volume": 0.2,
                            "directMedia": {"storageKey": "resource:voice"},
                        },
                    ],
                },
                "options": {"width": 320, "height": 180},
            }
        )
    )


def facts():
    return {
        "tone": SourceFacts(has_audio=True, has_video=True, duration_ms=1000),
        "silent": SourceFacts(has_audio=False, has_video=True, duration_ms=1000),
        "voice": SourceFacts(has_audio=True, has_video=False, duration_ms=1000),
    }


def test_actual_facts_preserve_selection_and_clear_silent_video_audio():
    compiled = plan()
    selected = [
        (item.kind, item.clip_id, item.start_ms, item.duration_ms) for item in compiled.segments
    ]
    apply_source_facts(compiled, facts())
    assert selected == [
        (item.kind, item.clip_id, item.start_ms, item.duration_ms) for item in compiled.segments
    ]
    assert [item.has_audio for item in compiled.segments] == [True, False, False]
    compiled.segments[0].muted = True
    apply_source_facts(compiled, facts())
    assert compiled.segments[0].has_audio is False


@pytest.mark.parametrize(
    "change, message",
    [
        ({"tone": SourceFacts(False, False, 1000)}, "缺少所需音视频轨"),
        ({"tone": SourceFacts(True, True, 0)}, "无法解析素材"),
        ({"tone": SourceFacts(True, True, 899)}, "素材时长不足"),
        ({"voice": SourceFacts(False, False, 1000)}, "没有可用音轨"),
    ],
)
def test_actual_source_failures_are_not_converted_to_silent_or_dropped_media(change, message):
    with pytest.raises(WorkflowError, match=message):
        apply_source_facts(plan(), {**facts(), **change})


def test_missing_source_is_rejected_before_lowering_and_100ms_slack_is_supported():
    current = facts()
    del current["tone"]
    with pytest.raises(WorkflowError, match="找不到素材：first"):
        apply_source_facts(plan(), current)
    current = facts()
    current["tone"] = SourceFacts(True, True, 900)
    apply_source_facts(plan(), current)


def test_image_does_not_require_duration_or_audio():
    compiled = plan()
    compiled.segments[0].kind = "image"
    apply_source_facts(compiled, {**facts(), "tone": SourceFacts(False, True, 0)})
    assert compiled.segments[0].has_audio is False


def test_lowering_includes_gap_stereo_silence_fades_and_delayed_mix():
    compiled = plan()
    apply_source_facts(compiled, facts())
    args = build_ffmpeg_args(compiled, {key: key + ".dat" for key in facts()}, "output.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert "color=c=black:s=320x180:r=30" in args
    assert args.count("anullsrc=r=44100:cl=stereo") == 2
    assert "concat=n=3:v=1:a=1" in graph
    assert "[6:a]aformat=" in graph and "volume=0.200" in graph
    assert "afade=t=in:st=0:d=0.800" in graph
    assert "afade=t=out:st=0.800:d=0.100,adelay=100:all=1" in graph
    assert "amix=inputs=2:duration=first:normalize=0" in graph
    assert "alimiter" not in graph and args[-3:] == ["-t", "3.000", "output.mp4"]


def test_lowering_cannot_succeed_with_a_missing_audio_or_visual_source():
    with pytest.raises(WorkflowError, match="找不到素材"):
        build_ffmpeg_args(plan(), {"tone": "tone", "silent": "silent"}, "output.mp4")
    with pytest.raises(WorkflowError, match="找不到素材"):
        build_ffmpeg_args(plan(), {}, "output.mp4")


@pytest.mark.parametrize(
    "message",
    [
        "failed to find any fallback",
        "no usable fontconfig",
        "fontselect: failed",
        "can't find selected font provider",
        "couldn't find font family",
        "failed to find font",
        "no fonts found",
        "missing glyph 0x4E2D",
    ],
)
def test_successful_encoder_exit_must_not_mask_missing_subtitles(message):
    assert subtitle_font_failure(message.upper())


def test_normal_chinese_font_fallback_is_not_failure():
    assert not subtitle_font_failure(
        "Glyph 0x4E2D not found, selecting one more font for (Arial, 400, 0)"
    )
    assert not subtitle_font_failure("fontselect: (Arial, 400, 0) -> NotoSansCJK, 0, NotoSansCJK")


@pytest.mark.parametrize(
    "planned, actual, fps, accepted",
    [
        (1000, 1080, 30, True),
        (1000, 920, 30, True),
        (1000, 2000, 30, False),
        (500, 1000, 1, True),
        (500, 2000, 1, False),
        (0, 1000, 30, False),
        (1000, 0, 30, False),
    ],
)
def test_output_tolerance_respects_low_fps_quantization(planned, actual, fps, accepted):
    assert output_duration_within_plan(planned, actual, fps) is accepted
