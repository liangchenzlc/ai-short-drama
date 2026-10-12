"""Claude SSE：保持源有效正文 EOF 语义，推理不进入正文增量。"""

import json

from .canvas_claude_adapters import CLAUDE_MESSAGES
from .streaming import TextStreamObserver
from .types import GenerationError, GenerationResult


class ClaudeTextStreamObserver(TextStreamObserver):
    def __init__(self, callback=None, *, secrets=None):
        super().__init__(callback or (lambda _content: None), responses=False, secrets=secrets)
        self.text_parts, self.reasoning_parts, self.usage = [], [], {}
        self.stop_reason = None

    def _invalid(self):
        raise GenerationError("invalid_response", accepted_unknown=True)

    def _event(self):
        lines, self.lines = self.lines, []
        payload = "\n".join(
            line[5:].removeprefix(" ") for line in lines if line.startswith("data:")
        )
        if not payload or payload == "[DONE]":
            return
        try:
            value = json.loads(payload)
        except (ValueError, TypeError):
            self._invalid()
        if not isinstance(value, dict):
            self._invalid()
        kind = value.get("type")
        if kind == "error" or value.get("error"):
            raise GenerationError("provider_rejected")
        if kind in {"content_block_start", "content_block_delta"}:
            block = value.get("content_block" if kind == "content_block_start" else "delta")
            if not isinstance(block, dict):
                self._invalid()
            block_type = block.get("type")
            if block_type in {"text", "text_delta", "thinking", "thinking_delta"}:
                thinking = block_type.startswith("thinking")
                part = (
                    block.get("thinking", block.get("text", ""))
                    if thinking
                    else block.get("text", "")
                )
                if not isinstance(part, str):
                    self._invalid()
                (self.reasoning_parts if thinking else self.text_parts).append(part)
                if part and not thinking:
                    self._emit_safe(part)
        if kind == "message_start":
            message = value.get("message")
            if not isinstance(message, dict):
                self._invalid()
            if isinstance(message.get("usage"), dict):
                self.usage.update(message["usage"])
        if kind == "message_delta":
            delta = value.get("delta")
            if not isinstance(delta, dict):
                self._invalid()
            reason = delta.get("stop_reason")
            if reason is not None and not isinstance(reason, str):
                self._invalid()
            if reason:
                self.stop_reason = reason
            if isinstance(value.get("usage"), dict):
                self.usage.update(value["usage"])

    def result(self) -> GenerationResult:
        text = "".join(self.text_parts)
        if not text.strip():
            self._invalid()
        return GenerationResult(
            "succeeded",
            CLAUDE_MESSAGES,
            text=text,
            reasoning="".join(self.reasoning_parts).strip() or None,
            usage=self.usage,
            finish_reason="max_output_tokens" if self.stop_reason == "max_tokens" else "stop",
        )


def decode_claude_text_stream(data: bytes) -> GenerationResult:
    observer = ClaudeTextStreamObserver()
    observer.feed(data)
    observer.finish()
    return observer.result()
