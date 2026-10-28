"""固定源 whisper verbose JSON 的时间、空值、结构校验和 SRT 合同。"""

import json

import pytest

from short_drama.schemas.canvas.tools.transcription.transcription import CanvasTranscriptionResult
from short_drama.service.canvas.tools.transcription.transcription import (
    build_transcription_srt,
    decode_whisper_verbose_json,
)


def decode(value):
    return decode_whisper_verbose_json(json.dumps(value).encode())


def test_verbose_segments_keep_source_order_round_and_trim_before_building_srt():
    segments, language = decode(
        {
            "segments": [
                {"start": 1.0005, "end": 2.9995, "text": "\u3000末句中文\n"},
                {"start": 0.1, "end": 0.8, "text": " 首句\n第二行 "},
                {"start": 3, "end": 4, "text": "\t\n\u00a0"},
            ],
            "language": "zh",
            "text": "该非结构化字段不能替代 segments",
        }
    )
    result = CanvasTranscriptionResult(
        segments=segments, language=language, srt=build_transcription_srt(segments)
    )
    assert result.model_dump(by_alias=True) == {
        "segments": [
            {"startMs": 1001, "endMs": 3000, "text": "末句中文"},
            {"startMs": 100, "endMs": 800, "text": "首句\n第二行"},
        ],
        "language": "zh",
        "srt": (
            "1\n00:00:01,001 --> 00:00:03,000\n末句中文\n\n"
            "2\n00:00:00,100 --> 00:00:00,800\n首句\n第二行\n\n"
        ),
    }


@pytest.mark.parametrize("value", [{}, None, {"segments": None}, {"segments": []}])
def test_empty_decodable_results_are_not_invented_speech(value):
    segments, language = decode(value)
    assert segments == [] and language == ""
    assert build_transcription_srt(segments) == ""


def test_null_scalars_and_null_items_follow_go_zero_values():
    segments, language = decode(
        {
            "segments": [None, {"start": None, "end": None, "text": None}, {"text": "正文"}],
            "language": None,
        }
    )
    assert language == ""
    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [(0, 0, "正文")]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0005, 1),
        (-0.0005, -1),
        (0.0004999999999999999, 0),
        (-0.0004999999999999999, 0),
        (0.0015, 2),
        (-0.0015, -2),
        (1e308, -(2**63)),
    ],
)
def test_go_rounds_half_away_from_zero_and_keeps_int64_conversion(seconds, expected):
    segments, _ = decode({"segments": [{"start": seconds, "end": seconds, "text": "正文"}]})
    assert segments[0].start_ms == expected and segments[0].end_ms == expected


def test_control_separator_is_not_go_whitespace_and_negative_srt_times_clamp_to_zero():
    segments, _ = decode({"segments": [{"start": -1, "end": 0.002, "text": "\u001c正文\u001f"}]})
    assert segments[0].text == "\u001c正文\u001f"
    assert (
        build_transcription_srt(segments)
        == "1\n00:00:00,000 --> 00:00:00,002\n\u001c正文\u001f\n\n"
    )


def test_case_insensitive_duplicate_fields_use_last_json_occurrence():
    segments, language = decode_whisper_verbose_json(
        b'{"segments":[{"start":3,"Start":2,"start":1,"END":4,"Text":"body"}],'
        b'"language":"first","LANGUAGE":"last"}'
    )
    assert segments[0].start_ms == 1000 and segments[0].end_ms == 4000
    assert language == "last"


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"segments": {}},
        {"segments": ["bad"]},
        {"segments": [1]},
        {"segments": [{"text": 1}]},
        {"segments": [{"start": "1", "text": "body"}]},
        {"segments": [{"end": True, "text": "body"}]},
        {"language": 1},
    ],
)
def test_invalid_verbose_structure_is_rejected(value):
    with pytest.raises(RuntimeError, match="转写结果解析失败"):
        decode(value)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"segments":[{"start":NaN}]}',
        b'{"segments":[{"end":Infinity}]}',
        b'{"segments":[{"end":1e309}]}',
        b"{} trailing",
        b"\xef\xbb\xbf{}",
    ],
)
def test_invalid_or_nonfinite_json_is_rejected(payload):
    with pytest.raises(RuntimeError, match="转写结果解析失败"):
        decode_whisper_verbose_json(payload)


def test_invalid_utf8_inside_json_text_follows_go_replacement():
    segments, _ = decode_whisper_verbose_json(b'{"segments":[{"text":"\xff"}]}')
    assert segments[0].text == "\ufffd"


def test_duplicate_slice_fields_reuse_struct_values_and_null_scalars_do_not_clear_them():
    segments, language = decode_whisper_verbose_json(
        b'{"segments":[{"start":1,"end":2,"text":"first"},{"text":"second"}],'
        b'"SEGMENTS":[{"start":null,"end":3}],"segments":[null,null],'
        b'"language":"zh","LANGUAGE":null}'
    )
    assert language == "zh"
    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [
        (1000, 3000, "first"),
        (0, 0, "second"),
    ]


@pytest.mark.parametrize("reset", ["null", "[]"])
def test_empty_or_null_duplicate_slice_discards_previous_storage(reset):
    segments, _ = decode_whisper_verbose_json(
        ('{"segments":[{"text":"old"}],"SEGMENTS":' + reset + ',"segments":[{}]}').encode()
    )
    assert segments == []


def test_invalid_duplicate_value_still_errors_when_later_field_is_valid():
    with pytest.raises(RuntimeError, match="转写结果解析失败"):
        decode_whisper_verbose_json(b'{"segments":[{"start":false,"start":1,"text":"body"}]}')


def test_unknown_large_number_and_unpaired_surrogates_match_go():
    segments, _ = decode_whisper_verbose_json(
        b'{"ignored":1e309,"segments":[{"text":"\\ud800"},{"text":"\\ud83d\\ude00"}]}'
    )
    assert [item.text for item in segments] == ["\ufffd", "\U0001f600"]
