"""Decode bounded SSE responses into the existing, non-streaming result contract."""

import json

from .types import GenerationError


def _invalid():
    raise GenerationError("invalid_response", accepted_unknown=True)


def _events(data):
    # SafeTransport has already bounded bytes and elapsed time. Never echo SSE data.
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        for block in text.split("\n\n"):
            lines = [
                line[5:].removeprefix(" ") for line in block.split("\n") if line.startswith("data:")
            ]
            if not lines:
                continue
            value = "\n".join(lines)
            if value == "[DONE]":
                yield None
                return
            event = json.loads(value)
            if not isinstance(event, dict) or event.get("error"):
                _invalid()
            yield event
    except (ValueError, UnicodeError, TypeError):
        _invalid()


def decode_text_stream(data, *, responses):
    if responses:
        for event in _events(data):
            if event is None:
                break
            if event.get("type") in (
                "response.completed",
                "response.incomplete",
                "response.failed",
            ):
                result = event.get("response")
                if not isinstance(result, dict):
                    _invalid()
                expected = event["type"].split(".")[1]
                if result.get("status") != expected:
                    _invalid()
                return result
        _invalid()

    text, usage, finish, done = [], {}, None, False
    try:
        for event in _events(data):
            if event is None:
                done = True
                break
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices")
            if not isinstance(choices, list):
                _invalid()
            for choice in choices:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {})
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str) or finish is not None:
                        _invalid()
                    text.append(content)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if not isinstance(reason, str):
                        _invalid()
                    finish = reason
        if not done or not finish or not text:
            _invalid()
    except (AttributeError, TypeError):
        _invalid()
    return {
        "choices": [{"message": {"content": "".join(text)}, "finish_reason": finish}],
        "usage": usage,
    }
