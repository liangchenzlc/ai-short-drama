"""固定 Go whisper verbose JSON 的解析与 SRT 编码，不发起模型请求。"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from short_drama.schemas.canvas.tools.transcription.transcription import CanvasTranscriptionSegment
from short_drama.service.canvas.timeline.timeline_compile import go_trim, srt_timestamp


class _Object(list[tuple[str, object]]):
    """保留字段顺序与重复键，以遵循 encoding/json 的 struct 解码。"""


class _Number(str):
    """未知字段的合法大数无需转成 float64；已知数值在赋值时验证。"""


@dataclass
class _Segment:
    start: float = 0.0
    end: float = 0.0
    text: str = ""


def _string(value: object) -> str:
    if not isinstance(value, str) or isinstance(value, _Number):
        raise ValueError("字幕字符串字段类型无效")
    return value.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")


def _float(value: object) -> float:
    if not isinstance(value, _Number):
        raise ValueError("字幕时间字段必须为 JSON 数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("字幕时间超出 float64 范围")
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError(f"无效 JSON 常量 {value}")


def _update_segment(segment: _Segment, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, _Object):
        raise ValueError("segments 的条目必须为对象")
    for key, field in value:
        key = _string(key).casefold()
        if field is None:
            continue
        if key == "start":
            segment.start = _float(field)
        elif key == "end":
            segment.end = _float(field)
        elif key == "text":
            segment.text = _string(field)


def _milliseconds(seconds: float) -> int:
    value = seconds * 1000
    # 固定源 Windows/amd64 的 float64 -> int64 越界值为最小 int64。
    if not math.isfinite(value):
        return -(2**63)
    fraction, integral = math.modf(abs(value))
    rounded = int(integral) + (1 if fraction >= 0.5 else 0)
    result = -rounded if value < 0 else rounded
    return result if -(2**63) <= result < 2**63 else -(2**63)


def decode_whisper_verbose_json(payload: bytes) -> tuple[list[CanvasTranscriptionSegment], str]:
    try:
        parsed = json.loads(
            payload.decode("utf-8", errors="replace"),
            object_pairs_hook=_Object,
            parse_int=_Number,
            parse_float=_Number,
            parse_constant=_invalid_constant,
        )
        if parsed is not None and not isinstance(parsed, _Object):
            raise ValueError("转写结果必须为对象")
        language = ""
        storage: list[_Segment] = []
        length = 0
        for key, value in parsed or []:
            key = _string(key).casefold()
            if key == "language" and value is not None:
                language = _string(value)
            elif key == "segments":
                if value is None:
                    storage, length = [], 0
                    continue
                if not isinstance(value, list) or isinstance(value, _Object):
                    raise ValueError("segments 必须为数组")
                if not value:
                    storage, length = [], 0
                    continue
                # Go 对重复 slice 字段重用既有 struct 元素，null 标量不清零。
                for index, raw in enumerate(value):
                    if index >= len(storage):
                        storage.append(_Segment())
                    _update_segment(storage[index], raw)
                length = len(value)
        segments = [
            CanvasTranscriptionSegment(
                start_ms=_milliseconds(segment.start),
                end_ms=_milliseconds(segment.end),
                text=go_trim(segment.text),
            )
            for segment in storage[:length]
            if go_trim(segment.text)
        ]
        return segments, language
    except (ValueError, OverflowError, TypeError, RecursionError) as error:
        raise RuntimeError(f"转写结果解析失败: {error}") from error


def build_transcription_srt(segments: Sequence[CanvasTranscriptionSegment]) -> str:
    return "".join(
        f"{index}\n{srt_timestamp(segment.start_ms)} --> "
        f"{srt_timestamp(segment.end_ms)}\n{segment.text}\n\n"
        for index, segment in enumerate(segments, start=1)
    )
