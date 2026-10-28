"""对照固定 BeefTV 编译夹具，并覆盖来源、隐藏轨和毫秒边界。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas.timeline.timeline import CanvasTimelinePlanRequest
from short_drama.service.canvas.timeline.timeline_compile import (
    MAX_DURATION_MS,
    compile_timeline_plan,
)

FIXTURES = Path(__file__).parents[3] / "fixtures" / "canvas_timeline"
# 夹具逐字取自 BeefTV 4ca2a65a7780a8dfcaaa86c33679f84fb04e055c:fixtures/editing。


def payload():
    return {
        "timeline": {
            "version": 2,
            "tracks": [{"id": "visual"}, {"id": "sound"}, {"id": "caption"}],
            "clips": [
                {
                    "id": "clip",
                    "kind": "video",
                    "nodeId": "node",
                    "trackId": "visual",
                    "startMs": 17,
                    "durationMs": 1001,
                    "sourceStartMs": 3,
                    "directMedia": {"id": "direct", "storageKey": "resource:opaque"},
                }
            ],
        },
    }


def compile_body(body):
    return compile_timeline_plan(CanvasTimelinePlanRequest.model_validate(body)).model_dump(
        by_alias=True
    )


@pytest.mark.parametrize("stem", ["gap-mix", "gap-silent-fade", "image-gap-sub", "six-second-mix"])
def test_matches_fixed_source_plan_fixtures(stem):
    body = json.loads((FIXTURES / f"{stem}.timeline.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURES / f"{stem}.plan.json").read_text(encoding="utf-8"))
    assert compile_body(body) == expected


def test_defaults_preserve_milliseconds_zero_volume_and_wire_omissions():
    body = payload()
    body["timeline"]["clips"][0]["volume"] = 0
    plan = compile_body(body)
    assert plan["output"] == {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "sampleRate": 44100,
        "burnSubtitles": True,
    }
    assert plan["durationMs"] == 1018
    assert plan["segments"] == [
        {"kind": "gap", "startMs": 0, "durationMs": 17, "volume": 1, "hasAudio": False},
        {
            "kind": "video",
            "clipId": "clip",
            "sourceId": "opaque",
            "startMs": 17,
            "durationMs": 1001,
            "sourceStartMs": 3,
            "volume": 0,
            "hasAudio": True,
        },
    ]
    assert plan["audio"] is None and plan["subtitles"] is None
    assert "subtitleSrt" not in plan


def test_nil_sources_differ_from_explicit_empty_array():
    body = payload()
    body["sources"] = None
    assert compile_body(body)["segments"][1]["sourceId"] == "opaque"
    body["sources"] = []
    with pytest.raises(WorkflowError, match="找不到素材"):
        compile_body(body)
    del body["sources"]
    body["timeline"]["clips"][0]["directMedia"]["storageKey"] = "https://example.test/video.mp4"
    with pytest.raises(WorkflowError, match="缺少有效媒体引用"):
        compile_body(body)


@pytest.mark.parametrize(
    "ids, expected",
    [
        (["direct", "resource:opaque", "opaque", "node"], "node"),
        (["direct", "resource:opaque", "opaque"], "opaque"),
        (["direct", "resource:opaque"], "resource:opaque"),
        (["direct"], "direct"),
    ],
)
def test_opaque_source_priority_does_not_accept_filesystem_fields(ids, expected):
    body = payload()
    body["sources"] = [{"id": value, "path": "/private/native.mp4", "url": "bad"} for value in ids]
    plan = compile_body(body)
    assert plan["segments"][1]["sourceId"] == expected
    assert "private" not in json.dumps(plan) and "bad" not in json.dumps(plan)


def test_hidden_malformed_clip_is_omitted_but_unknown_track_is_rejected():
    body = payload()
    body["timeline"]["tracks"].append({"id": "hidden", "visible": False})
    body["timeline"]["clips"].append(
        {
            "id": "bad",
            "trackId": "hidden",
            "kind": "text",
            "durationMs": -1,
            "volume": -1,
        }
    )
    assert compile_body(body) == compile_body(payload())
    body["timeline"]["clips"][-1]["trackId"] = "missing"
    with pytest.raises(WorkflowError, match="不存在的轨道"):
        compile_body(body)


def test_overlap_is_rejected_across_visual_tracks_while_audio_can_mix():
    body = payload()
    body["timeline"]["tracks"].append({"id": "other"})
    extra = deepcopy(body["timeline"]["clips"][0])
    extra.update(id="second", trackId="other", startMs=1017)
    body["timeline"]["clips"].append(extra)
    with pytest.raises(WorkflowError, match="重叠"):
        compile_body(body)
    extra.update(kind="audio", trackId="sound", startMs=17)
    plan = compile_body(body)
    assert len(plan["audio"]) == 1 and len(plan["segments"]) == 2


@pytest.mark.parametrize("duration, succeeds", [(904, True), (903, False)])
def test_source_timing_uses_fixed_source_100ms_tolerance(duration, succeeds):
    body = payload()
    body["sources"] = [{"id": "node", "durationMs": duration}]
    if succeeds:
        assert compile_body(body)["segments"][1]["durationMs"] == 1001
    else:
        with pytest.raises(WorkflowError, match="素材时长不足"):
            compile_body(body)


def test_image_duration_is_independent_of_source_duration_and_muted_audio_is_retained():
    body = payload()
    clip = body["timeline"]["clips"][0]
    clip["kind"] = "image"
    body["sources"] = [{"id": "node", "durationMs": 1, "hasVideo": False}]
    audio = deepcopy(clip)
    audio.update(id="audio", nodeId="audio", kind="audio", trackId="sound", sourceStartMs=0)
    body["timeline"]["clips"].append(audio)
    body["timeline"]["tracks"][1]["muted"] = True
    body["sources"].append({"id": "audio", "hasAudio": True, "durationMs": 2000})
    plan = compile_body(body)
    assert plan["segments"][1]["hasAudio"] is False
    assert plan["audio"][0]["muted"] is True
    body["sources"][-1]["hasAudio"] = False
    with pytest.raises(WorkflowError, match="没有可用音轨"):
        compile_body(body)


def test_source_capabilities_and_track_mute_clear_video_audio():
    body = payload()
    body["sources"] = [{"id": "node", "hasAudio": False, "hasVideo": True}]
    assert compile_body(body)["segments"][1]["hasAudio"] is False
    body["sources"][0]["hasAudio"] = True
    body["timeline"]["tracks"][0]["muted"] = True
    assert compile_body(body)["segments"][1]["hasAudio"] is False
    body["sources"][0]["hasVideo"] = False
    with pytest.raises(WorkflowError, match="缺少所需音视频轨"):
        compile_body(body)


def test_subtitle_sort_trim_srt_and_disable_preserve_full_timeline_duration():
    body = payload()
    body["timeline"]["clips"] += [
        {
            "id": "z",
            "kind": "subtitle",
            "trackId": "caption",
            "startMs": 2000,
            "durationMs": 1000,
            "text": "  后一条  ",
        },
        {
            "id": "a",
            "kind": "subtitle",
            "trackId": "caption",
            "startMs": 0,
            "durationMs": 1000,
            "text": "第一条\n第二行",
        },
    ]
    plan = compile_body(body)
    assert plan["subtitleSrt"] == (
        "1\n00:00:00,000 --> 00:00:01,000\n第一条\n第二行\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n后一条\n\n"
    )
    assert plan["durationMs"] == 3000
    body["options"] = {"burnSubtitles": False}
    plan = compile_body(body)
    assert plan["durationMs"] == 3000 and plan["subtitles"] is None
    assert "subtitleSrt" not in plan


@pytest.mark.parametrize(
    "options",
    [
        {"width": 3},
        {"height": 1},
        {"width": 7682},
        {"height": 4322},
        {"fps": 121},
        {"fps": -1},
        {"sampleRate": 7999},
        {"sampleRate": 96001},
    ],
)
def test_rejects_impossible_output(options):
    body = payload()
    body["options"] = options
    with pytest.raises(WorkflowError, match="输出尺寸、帧率或采样率无效"):
        compile_body(body)


def test_go_null_scalars_use_defaults_and_unknown_ui_fields_are_ignored():
    body = payload()
    body["timeline"]["tracks"][0].update(visible=None, locked=True, order=9)
    body["timeline"]["clips"][0].update(volume=None, fadeInMs=None, text=None, selected=True)
    body["options"] = {"width": None, "height": 0, "fps": 0, "sampleRate": 0, "burnSubtitles": None}
    assert compile_body(body) == compile_body(payload())


@pytest.mark.parametrize(
    "change, message",
    [
        ({"kind": "text"}, "不支持的片段类型"),
        ({"startMs": -1}, "不能为负数"),
        ({"sourceStartMs": -1}, "不能为负数"),
        ({"volume": -0.1}, "音量无效"),
        ({"durationMs": 0}, "片段时间无效"),
        ({"startMs": MAX_DURATION_MS - 1000, "durationMs": 1001}, "时长无效"),
    ],
)
def test_invalid_visible_media_fails_with_business_context(change, message):
    body = payload()
    body["timeline"]["clips"][0].update(change)
    with pytest.raises(WorkflowError, match=message):
        compile_body(body)


@pytest.mark.parametrize("change", [{"durationMs": 1.5}, {"volume": "1"}, {"startMs": True}])
def test_wire_rejects_coerced_numbers(change):
    body = payload()
    body["timeline"]["clips"][0].update(change)
    with pytest.raises(ValidationError):
        CanvasTimelinePlanRequest.model_validate(body)


def test_duplicate_blank_ids_and_non_media_timeline_fail():
    body = payload()
    body["timeline"]["clips"].append(deepcopy(body["timeline"]["clips"][0]))
    with pytest.raises(WorkflowError, match="重复"):
        compile_body(body)
    body["timeline"]["clips"] = []
    with pytest.raises(WorkflowError, match="没有可渲染"):
        compile_body(body)
    body["timeline"]["tracks"][0]["id"] = " "
    with pytest.raises(WorkflowError, match="有效编号"):
        compile_body(body)


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"kind": ""}, "不支持的片段类型："),
        ({"trackId": ""}, "片段引用了不存在的轨道："),
    ],
)
def test_empty_business_context_preserves_fixed_source_message(change, expected):
    body = payload()
    body["timeline"]["clips"][0].update(change)
    with pytest.raises(WorkflowError) as caught:
        compile_body(body)
    assert caught.value.message == expected


@pytest.mark.parametrize("boundary", ["\x1c", "\x1d", "\x1e", "\x1f"])
def test_subtitle_preserves_control_characters_that_go_does_not_trim(boundary):
    body = payload()
    text = boundary + "字幕" + boundary
    body["timeline"]["clips"].append(
        {
            "id": "subtitle",
            "kind": "subtitle",
            "trackId": "caption",
            "startMs": 0,
            "durationMs": 1000,
            "text": text,
        }
    )
    plan = compile_body(body)
    assert plan["subtitles"][0]["text"] == text
    assert plan["subtitleSrt"].endswith(text + "\n\n")
