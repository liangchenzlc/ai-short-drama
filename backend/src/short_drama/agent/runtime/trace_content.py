"""Display redaction is shared by live events, receipts and archived detail."""

import json
import re

from short_drama.schemas.assistant.operations.trace import TRACE_PREVIEW_BYTES

_PRIVATE_KEYS = re.compile(
    r"(?:secret|password|authorization|api[_-]?key|signature|encrypted|locator|bucket|object_key|traceback)|^(?:token|access_token|refresh_token)$",
    re.IGNORECASE,
)
_LOCATOR = re.compile(r"minio://[^\s\"'<>]+")
_CREDENTIAL = re.compile(
    r"(?i)(bearer\s+\S+|(?:api[_-]?key|password|secret|token|authorization)"
    r"[\s\"']*[:=][\s\"']*[^\s,}\"']+)"
)
_URL_QUERY = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']+")


def display_safe(value):
    if isinstance(value, str):
        return _CREDENTIAL.sub("[已隐藏]", _LOCATOR.sub("[内部资源]", _URL_QUERY.sub(r"\1", value)))
    if isinstance(value, dict):
        return {
            key: display_safe(child)
            for key, child in value.items()
            if not key.startswith("_")
            and not _PRIVATE_KEYS.search(key)
            and key not in {"uri", "headers", "body_b64"}
        }
    if isinstance(value, (tuple, list)):
        return [display_safe(child) for child in value]
    return value


def utf8_preview(text: str, maximum: int = TRACE_PREVIEW_BYTES) -> str:
    return text.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")


def display_text(value) -> str:
    return (
        display_safe(value)
        if isinstance(value, str)
        else json.dumps(display_safe(value), ensure_ascii=False, separators=(",", ":"))
    )


def display_refs(values) -> list[dict]:
    """References retain navigation facts; rich bodies belong to bounded channels."""
    result, remaining = [], TRACE_PREVIEW_BYTES
    for value in display_safe(values)[:32]:
        item = {
            key: utf8_preview(child, 480) if isinstance(child, str) else child
            for key, child in value.items()
            if key
            not in {"content", "text", "instructions", "description", "body", "output", "data"}
            and (child is None or isinstance(child, (str, int, float, bool)))
        }
        size = len(display_text(item).encode("utf-8"))
        if size > remaining:
            break
        result.append(item)
        remaining -= size
    return result


def reasoning_texts(chunk):
    for block in chunk.content_blocks:
        if block.get("type") == "reasoning":
            text = block.get("reasoning")
            if isinstance(text, str) and text:
                yield text
