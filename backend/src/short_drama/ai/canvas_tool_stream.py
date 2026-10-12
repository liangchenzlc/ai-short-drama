"""有界工具SSE重组；仅正文观察，工具参数在完整校验后交付本人。"""

from short_drama.schemas.canvas_tool_contracts import validate_tool_arguments

from .streaming import _events, decode_text_stream
from .types import GenerationError


def decode_canvas_tool_stream(data: bytes, adapter: str) -> dict:
    if adapter == "openai_responses.v1":
        return decode_text_stream(data, responses=True)
    try:
        if adapter == "openai_chat.v1":
            return _chat_stream(data)
        if adapter == "claude_messages.v1":
            return _claude_stream(data)
        raise ValueError("Unsupported tool stream")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise GenerationError("invalid_response", accepted_unknown=True) from None


def _chat_stream(data: bytes) -> dict:
    text, reasoning, calls, usage = [], [], {}, {}
    finish, done = None, False
    for event in _events(data):
        if event is None:
            done = True
            break
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        if not isinstance(event.get("choices"), list):
            raise ValueError
        for choice in event["choices"]:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict) or finish is not None and delta:
                raise ValueError
            for key, target in (
                ("content", text),
                ("reasoning_content", reasoning),
                ("reasoning", reasoning),
            ):
                part = delta.get(key)
                if part is not None:
                    if not isinstance(part, str):
                        raise ValueError
                    target.append(part)
            fragments = delta.get("tool_calls", [])
            if not isinstance(fragments, list):
                raise ValueError
            for item in fragments:
                index = item["index"]
                if (
                    type(index) is not int
                    or not 0 <= index <= 15
                    or item.get("type", "function") != "function"
                ):
                    raise ValueError
                call = calls.setdefault(
                    index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                identifier = item.get("id")
                if identifier is not None:
                    if not isinstance(identifier, str) or call["id"] and identifier != call["id"]:
                        raise ValueError
                    call["id"] = identifier
                function = item.get("function", {})
                for key in ("name", "arguments"):
                    value = function.get(key)
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError
                        call["function"][key] += value
            reason = choice.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str):
                    raise ValueError
                finish = reason
    if not done or finish is None:
        raise ValueError("Incomplete tool stream")
    return {
        "choices": [
            {
                "message": {
                    "content": "".join(text),
                    "reasoning_content": "".join(reasoning),
                    "tool_calls": [calls[index] for index in sorted(calls)],
                },
                "finish_reason": finish,
            }
        ],
        "usage": usage,
    }


def _claude_stream(data: bytes) -> dict:
    blocks, partials, closed, usage = {}, {}, set(), {}
    stop_reason, started = None, False
    for event in _events(data):
        if event is None:
            break
        kind = event.get("type")
        if kind == "message_start":
            if started:
                raise ValueError
            started = True
            usage.update(event["message"].get("usage") or {})
        elif kind == "content_block_start":
            index = event["index"]
            if type(index) is not int or not 0 <= index <= 31 or index in blocks:
                raise ValueError
            block = event["content_block"]
            if not isinstance(block, dict) or block.get("type") not in {
                "text",
                "thinking",
                "redacted_thinking",
                "tool_use",
            }:
                raise ValueError
            blocks[index] = dict(block)
        elif kind == "content_block_delta":
            index, delta = event["index"], event["delta"]
            if index not in blocks or index in closed:
                raise ValueError
            block = blocks[index]
            delta_type = delta["type"]
            if delta_type == "text_delta" and block["type"] == "text":
                block["text"] = block.get("text", "") + delta["text"]
            elif delta_type == "thinking_delta" and block["type"] == "thinking":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
            elif delta_type == "input_json_delta" and block["type"] == "tool_use":
                partials[index] = partials.get(index, "") + delta["partial_json"]
            elif delta_type != "signature_delta":
                raise ValueError
        elif kind == "content_block_stop":
            index = event["index"]
            if index not in blocks or index in closed:
                raise ValueError
            closed.add(index)
        elif kind == "message_delta":
            stop_reason = event["delta"].get("stop_reason") or stop_reason
            usage.update(event.get("usage") or {})
    if not started or not blocks or set(blocks) != closed:
        raise ValueError("Incomplete Claude tool blocks")
    for index, raw in partials.items():
        # 在解析为对象前核对重复字段与固定schema，随后仍校验本次请求工具身份。
        blocks[index]["input"] = validate_tool_arguments(blocks[index]["name"], raw)
    return {
        "content": [blocks[index] for index in sorted(blocks)],
        "stop_reason": stop_reason,
        "usage": usage,
    }
