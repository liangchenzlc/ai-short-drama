"""固定源内置编辑器工具的四协议转换和回复解析；没有工具执行或图写入权限。"""

import json
import re
from copy import deepcopy

from pydantic import ValidationError

from short_drama.schemas.canvas_tool_contracts import (
    TOOL_REPLY_BYTES,
    CanvasToolCanonical,
    CanvasToolReply,
    validate_tool_arguments,
)

from .transport import validated_url
from .types import GenerationError

CANVAS_TOOL_ADAPTERS = frozenset(
    {
        "openai_chat.v1",
        "openai_responses.v1",
        "claude_messages.v1",
        "gemini_generate_content.v1",
    }
)


def is_canvas_tool_choice_rejection(status: int, data: bytes) -> bool:
    """只分类已明确拒绝选择参数的有界4xx；上游正文不进入异常或归档。"""
    if status not in {400, 422} or len(data) > 65536:
        return False
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error", payload)
    values = (
        [error.get(key) for key in ("message", "code", "param")]
        if isinstance(error, dict)
        else [error]
    )
    return any(
        marker in value.lower()
        for value in values
        if isinstance(value, str)
        for marker in ("tool_choice", "tool choice", "tool-choice")
    )


def _canonical(value: dict | CanvasToolCanonical) -> CanvasToolCanonical:
    if isinstance(value, CanvasToolCanonical):
        value = value.model_dump(by_alias=True)
    try:
        return CanvasToolCanonical.model_validate(value)
    except (ValidationError, ValueError):
        raise GenerationError("unsupported_parameters") from None


def build_canvas_tool_body(
    canonical: dict | CanvasToolCanonical,
    adapter: str,
    *,
    image_references: dict[str, dict] | None = None,
) -> dict:
    """稳定身份只在执行期水合；参数必须来自已授权的服务端引用表。"""
    if adapter not in CANVAS_TOOL_ADAPTERS:
        raise GenerationError("unsupported_protocol")
    contract = _canonical(canonical)
    request = contract.model_dump(by_alias=True, exclude_none=True)
    references = image_references or {}
    if set(references) != set(contract.image_keys()):
        raise GenerationError("unresolved_media_reference")
    for reference in references.values():
        if (
            not isinstance(reference, dict)
            or reference.get("mime_type")
            not in {"image/png", "image/jpeg", "image/webp", "image/gif"}
            or not isinstance(reference.get("url"), str)
        ):
            raise GenerationError("unresolved_media_reference")
        validated_url(reference["url"], query=True)
    messages = request["messages"]
    instructions = request["systemPrompt"].strip()
    if instructions and not any(
        message["role"] == "system"
        and isinstance(message["content"], str)
        and message["content"].strip() == instructions
        for message in messages
    ):
        messages = [{"role": "system", "content": instructions}, *messages]
    choice, tools = request["toolChoice"], request["tools"]
    if adapter == "openai_chat.v1":
        return {
            "messages": [_convert_message(message, adapter, references) for message in messages],
            "tools": tools,
            "tool_choice": {"type": "function", "function": {"name": choice["name"]}}
            if isinstance(choice, dict)
            else choice,
            "parallel_tool_calls": False,
        }
    if adapter == "openai_responses.v1":
        return {
            "input": [_convert_message(message, adapter, references) for message in messages],
            "tools": [{"type": "function", **tool["function"]} for tool in tools],
            "tool_choice": choice,
            "parallel_tool_calls": False,
        }
    system = "\n\n".join(
        _system_text(message) for message in messages if message["role"] == "system"
    )
    non_system = [message for message in messages if message["role"] != "system"]
    if adapter == "claude_messages.v1":
        result = {
            "messages": [_convert_message(message, adapter, references) for message in non_system],
            "max_tokens": 4096,
            "tools": [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"]["parameters"],
                }
                for tool in tools
            ],
            "tool_choice": {"type": "tool", "name": choice["name"]}
            if isinstance(choice, dict)
            else {"type": "any" if choice == "required" else "auto"},
        }
        result["tools"][-1]["cache_control"] = {"type": "ephemeral"}
        if system:
            result["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return result
    result = {
        "contents": [_convert_message(message, adapter, references) for message in non_system],
        "tools": [
            {
                "functionDeclarations": [
                    {key: value for key, value in tool["function"].items() if key != "strict"}
                    for tool in tools
                ]
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [choice["name"]]}
            if isinstance(choice, dict)
            else {"mode": "ANY" if choice == "required" else "AUTO"}
        },
    }
    if system:
        result["systemInstruction"] = {"parts": [{"text": system}]}
    return result


def _system_text(message: dict) -> str:
    content = message["content"]
    return (
        content
        if isinstance(content, str)
        else "\n".join(part["text"] for part in content if part["type"] == "text")
    )


def _convert_message(message: dict, adapter: str, references: dict) -> dict:
    content = message["content"]
    if isinstance(content, str):
        if adapter != "gemini_generate_content.v1":
            return {"role": message["role"], "content": content}
        parts = [{"text": content}]
    else:
        parts = []
        for part in content:
            if part["type"] == "text":
                parts.append(
                    {"text": part["text"]}
                    if adapter == "gemini_generate_content.v1"
                    else {
                        "type": "input_text" if adapter == "openai_responses.v1" else "text",
                        "text": part["text"],
                    }
                )
                continue
            reference = references[part["image_url"]["url"]]
            url = reference["url"]
            if adapter == "openai_chat.v1":
                parts.append({"type": "image_url", "image_url": {"url": url}})
            elif adapter == "openai_responses.v1":
                parts.append({"type": "input_image", "image_url": url})
            elif adapter == "claude_messages.v1":
                parts.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                parts.append({"fileData": {"fileUri": url, "mimeType": reference["mime_type"]}})
    if adapter == "gemini_generate_content.v1":
        return {"role": "model" if message["role"] == "assistant" else "user", "parts": parts}
    return {"role": message["role"], "content": parts}


def parse_canvas_tool_result(
    body: dict, adapter: str, canonical: dict | CanvasToolCanonical, *, submitted: bool
) -> CanvasToolReply:
    if adapter not in CANVAS_TOOL_ADAPTERS:
        raise GenerationError("unsupported_protocol")
    contract = _canonical(canonical)
    if not isinstance(body, dict):
        raise GenerationError("invalid_response", accepted_unknown=submitted)
    if body.get("error") or body.get("code") or body.get("status") in {"failed", "cancelled"}:
        raise GenerationError("provider_rejected")
    try:
        content, reasoning, calls = _parse_reply(body, adapter)
        return validate_canvas_tool_reply(
            {
                "content": content,
                "reasoning": reasoning or None,
                "toolCalls": calls,
            },
            contract,
            submitted=submitted,
        )
    except (KeyError, IndexError, TypeError, ValueError, ValidationError, RecursionError):
        raise GenerationError("invalid_response", accepted_unknown=submitted) from None


def validate_canvas_tool_reply(
    value: dict, canonical: dict | CanvasToolCanonical, *, submitted: bool
) -> CanvasToolReply:
    """协议解析及密钥脱敏后都验证同一固定合同。"""
    contract = _canonical(canonical)
    try:
        reply = CanvasToolReply.model_validate(value)
        requested = contract.tools[0].function.name
        for call in reply.tool_calls:
            if call.function.name != requested:
                raise ValueError("Unexpected editor tool")
            validate_tool_arguments(requested, call.function.arguments)
        if not reply.tool_calls:
            _validate_content_fallback(requested, reply.content)
        encoded = json.dumps(reply.model_dump(by_alias=True), ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > TOOL_REPLY_BYTES:
            raise ValueError("Editor tool reply exceeds the byte limit")
        return reply
    except (KeyError, IndexError, TypeError, ValueError, ValidationError, RecursionError):
        raise GenerationError("invalid_response", accepted_unknown=submitted) from None


def _validate_content_fallback(name: str, content: str) -> None:
    # 保留源JSON正文fallback；进入任务结果前仍验证相同固定schema。
    candidates = [content, re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())]
    if name == "optimize_prompt":
        start, end = content.find("{"), content.rfind("}")
        if 0 <= start < end:
            candidates.append(content[start : end + 1])
    for candidate in candidates:
        try:
            validate_tool_arguments(name, candidate)
            return
        except (ValueError, RecursionError):
            continue
    raise ValueError("Editor tool did not return a valid structured result")


def _parse_reply(body: dict, adapter: str) -> tuple[str, str, list[dict]]:
    content, reasoning, calls = [], [], []
    if adapter == "openai_chat.v1":
        choice = body["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise ValueError("Incomplete tool reply")
        message = choice["message"]
        text = message.get("content")
        if text is not None:
            content.append(text)
        reasoning.append(message.get("reasoning_content") or message.get("reasoning") or "")
        calls = deepcopy(message.get("tool_calls") or [])
    elif adapter == "openai_responses.v1":
        if body.get("status") in {"queued", "in_progress", "incomplete"} or body.get(
            "incomplete_details"
        ):
            raise ValueError("Incomplete tool reply")
        direct = body.get("output_text")
        for item in body.get("output", []):
            if item["type"] == "function_call":
                calls.append(
                    {
                        "id": item.get("call_id") or item.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"],
                        },
                    }
                )
            elif item["type"] == "message" and not direct:
                content.extend(
                    part["text"] for part in item["content"] if part["type"] == "output_text"
                )
            elif item["type"] == "reasoning":
                reasoning.extend(
                    part["text"]
                    for part in item.get("summary", [])
                    if part.get("type") == "summary_text"
                )
        if direct:
            content = [direct]
    elif adapter == "claude_messages.v1":
        if body.get("stop_reason") in {"max_tokens", "refusal"}:
            raise ValueError("Incomplete tool reply")
        for item in body["content"]:
            if item["type"] == "text":
                content.append(item["text"])
            elif item["type"] == "thinking":
                reasoning.append(item["thinking"])
            elif item["type"] == "tool_use":
                calls.append(
                    {
                        "id": item.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": json.dumps(
                                item["input"], ensure_ascii=False, allow_nan=False
                            ),
                        },
                    }
                )
    else:
        candidate = body["candidates"][0]
        if candidate.get("finishReason") in {
            "MAX_TOKENS",
            "SAFETY",
            "RECITATION",
            "PROHIBITED_CONTENT",
        }:
            raise ValueError("Incomplete tool reply")
        for part in candidate["content"]["parts"]:
            if "thought" in part and type(part["thought"]) is not bool:
                raise ValueError("Invalid private thought discriminator")
            if "text" in part:
                (reasoning if part.get("thought") is True else content).append(part["text"])
            if "functionCall" in part:
                item = part["functionCall"]
                call = {
                    "id": item.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "arguments": json.dumps(item["args"], ensure_ascii=False, allow_nan=False),
                    },
                }
                signature = part.get("thoughtSignature") or part.get("thought_signature")
                if signature:
                    call["thoughtSignature"] = signature
                calls.append(call)
    return "".join(content), "".join(reasoning), calls
