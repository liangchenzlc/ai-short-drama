"""Strict UTF-8 SRT interchange. Text is always treated as plain text."""

import re

from short_drama.schemas.episode_sound import SoundDocument, Subtitle


def parse_srt(value: str):
    if len(value.encode("utf-8")) > 2 * 1024**2:
        raise ValueError("字幕文件超过 2 MiB")
    result = []
    for block in re.split(r"\n\s*\n", value.lstrip("\ufeff").replace("\r\n", "\n").strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise ValueError("SRT 序号或内容不完整")
        match = re.fullmatch(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", lines[1]
        )
        if not match:
            raise ValueError("SRT 时间格式无效")
        nums = list(map(int, match.groups()))
        if any(nums[i] >= 60 for i in (1, 2, 5, 6)):
            raise ValueError("SRT 分秒超出范围")

        def ms(n):
            return ((n[0] * 60 + n[1]) * 60 + n[2]) * 1000 + n[3]

        result.append(
            Subtitle(start_ms=ms(nums[:4]), end_ms=ms(nums[4:]), text="\n".join(lines[2:]))
        )
    return SoundDocument(subtitles=result).subtitles


def export_srt(entries):
    def stamp(ms):
        seconds, milliseconds = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    return (
        "\n\n".join(
            f"{i}\n{stamp(e['start_ms'])} --> {stamp(e['end_ms'])}\n{e['text']}"
            for i, e in enumerate(entries, 1)
        )
        + "\n"
    )
