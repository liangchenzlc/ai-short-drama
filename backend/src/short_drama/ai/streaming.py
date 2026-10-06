"""Decode bounded SSE responses into the existing, non-streaming result contract."""

import codecs
import json
import re

from .types import GenerationError


class TextStreamObserver:
    """只观察供应商实际正文事件；完整响应仍由原解码器验证。"""

    def __init__(
        self, callback, *, responses: bool, secret: str = "", secrets: list[str] | None = None
    ):
        self.callback, self.responses, self.secret = callback, responses, secret
        self.secrets = sorted(
            set(value for value in [secret, *(secrets or [])] if value), key=len, reverse=True
        )
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.pending = ""
        self.lines = []
        self.safe_pending = ""

    def feed(self, data: bytes) -> None:
        try:
            self.pending += self.decoder.decode(data)
        except UnicodeError:
            _invalid()
        self._lines()

    def _lines(self) -> None:
        while match := re.search(r"\r\n|\n|\r(?!$)", self.pending):
            line, self.pending = self.pending[: match.start()], self.pending[match.end() :]
            if line:
                self.lines.append(line)
            else:
                self._event()

    def _event(self) -> None:
        lines, self.lines = self.lines, []
        payload = "\n".join(
            line[5:].removeprefix(" ") for line in lines if line.startswith("data:")
        )
        if not payload or payload == "[DONE]":
            return
        try:
            value = json.loads(payload)
        except (ValueError, TypeError):
            _invalid()
        if not isinstance(value, dict) or value.get("error"):
            _invalid()
        if self.responses:
            content = (
                value.get("delta") if value.get("type") == "response.output_text.delta" else ""
            )
        else:
            content = ""
            choices = value.get("choices", [])
            if not isinstance(choices, list):
                _invalid()
            for choice in choices:
                if not isinstance(choice, dict):
                    _invalid()
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    _invalid()
                part = delta.get("content")
                if part is not None:
                    if not isinstance(part, str):
                        _invalid()
                    content += part
        if not isinstance(content, str):
            _invalid()
        if content:
            self._emit_safe(content)

    def _emit_safe(self, content: str, *, final: bool = False) -> None:
        self.safe_pending += content
        for secret in self.secrets:
            self.safe_pending = self.safe_pending.replace(secret, "[redacted]")
        keep = 0
        if not final:
            for secret in self.secrets:
                for size in range(min(len(self.safe_pending), len(secret) - 1), keep, -1):
                    if self.safe_pending.endswith(secret[:size]):
                        keep = size
                        break
        ready = self.safe_pending[:-keep] if keep else self.safe_pending
        self.safe_pending = self.safe_pending[-keep:] if keep else ""
        if ready:
            self.callback(ready)

    def finish(self) -> None:
        try:
            self.pending += self.decoder.decode(b"", final=True)
        except UnicodeError:
            _invalid()
        self.pending += "\n"
        self._lines()
        self._event()
        self._emit_safe("", final=True)


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
