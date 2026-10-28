"""Deterministic delivery-requirement drafts from the user's own words.

The executor must not be the only author of "what done means". This module runs
on the sending path, before any model call, and turns explicit user sentences
into draft requirements carrying exact quote offsets. It never calls a model and
never guesses beyond a recognised object word plus a quantity or action verb.
"""

import re
from dataclasses import dataclass

MAX_DRAFTS = 32

_QUANTITY = re.compile(
    r"(?P<count>[一二两三四五六七八九十]+|\d+)(?![\d零一二两三四五六七八九十百千万亿])"
    r"\s*(?P<unit>个|名|张|段|条|集|镜|场|句|首|位)?"
)
_WRITE_VERBS = ("保存", "写入", "落到", "改成", "生成", "创建", "补充", "替换")
_READ_VERBS = ("读取", "查看", "检查", "核对", "回读")
_SENTENCE = re.compile(r"[^。！？；;\n]+")
_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

# object word -> (kind, artifact_kind, asset_kind)
_OBJECTS = (
    ("角色", ("write", "asset", "character")),
    ("人物", ("write", "asset", "character")),
    ("场景", ("write", "asset", "scene")),
    ("道具", ("write", "asset", "prop")),
    ("分镜", ("write", "shot", None)),
    ("镜头", ("write", "shot", None)),
    ("台词", ("write", "sound_line", None)),
    ("对白", ("write", "sound_line", None)),
    ("剧本", ("write", None, None)),
    ("正文", ("write", None, None)),
    ("图片", ("image", None, None)),
    ("主图", ("image", None, None)),
    ("视频", ("image", None, None)),
    ("音色", ("image", None, None)),
)


@dataclass(frozen=True)
class Draft:
    key: str
    description: str
    kind: str
    minimum_count: int
    artifact_kind: str | None
    asset_kind: str | None
    quote_start: int
    quote_end: int


def explicit_count(text: str) -> int | None:
    """Parse an explicit leading quantity without inventing a default quantity."""
    match = _QUANTITY.match(text)
    if match is None:
        return None
    raw = match.group("count")
    if raw.isdigit():
        return int(raw)
    if raw in _CN_NUMBERS:
        return _CN_NUMBERS[raw]
    if raw.count("十") == 1:
        tens, ones = raw.split("十")
        if (not tens or tens in _CN_NUMBERS) and (not ones or ones in _CN_NUMBERS):
            return (_CN_NUMBERS[tens] if tens else 1) * 10 + (_CN_NUMBERS[ones] if ones else 0)
    return None


def _count(prefix: str) -> int:
    """Use the quantity closest to the object word, not the first one in the sentence."""
    matches = list(_QUANTITY.finditer(prefix))
    count = explicit_count(matches[-1].group(0)) if matches else None
    return max(1, min(count, 10000)) if count is not None else 1


def draft_requirements(content: str) -> list[Draft]:
    """Return at most ``MAX_DRAFTS`` drafts; an empty list means "no explicit ask"."""
    if not content or not content.strip():
        return []
    drafts: list[Draft] = []
    for sentence in _SENTENCE.finditer(content):
        text = sentence.group(0)
        if not text.strip():
            continue
        start, end = sentence.start(), sentence.end()
        writes = any(verb in text for verb in _WRITE_VERBS)
        reads = any(verb in text for verb in _READ_VERBS)
        if not writes and not reads:
            continue
        for word, (kind, artifact, asset) in _OBJECTS:
            if word not in text:
                continue
            position = content.find(word, start, end)
            if position < 0:
                continue
            row_kind = kind if writes or kind != "write" else "read"
            count = _count(content[start:position]) if writes else 1
            drafts.append(
                Draft(
                    key=f"{row_kind}-{artifact or word}-{len(drafts) + 1}",
                    description=text.strip()[:300],
                    kind=row_kind,
                    minimum_count=count,
                    artifact_kind=artifact,
                    asset_kind=asset,
                    quote_start=start,
                    quote_end=end,
                )
            )
            if len(drafts) >= MAX_DRAFTS:
                return drafts
    return drafts


def quote_is_within(content: str, quote_start: int, quote_end: int) -> bool:
    return 0 <= quote_start < quote_end <= len(content)


def quote_matches(content: str, quote: str, quote_start: int, quote_end: int) -> bool:
    """A model-supplied quote must be exactly the frozen span, not a paraphrase."""
    if not quote_is_within(content, quote_start, quote_end):
        return False
    return content[quote_start:quote_end] == quote
