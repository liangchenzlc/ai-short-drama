"""One bounded input envelope; estimates never authorize truncating protocol state."""

import json
from copy import deepcopy

from short_drama.agent.gateway.contracts import AgentGatewayError

CONTEXT_TOKENS = 65536
TOOL_RESULT_TOKENS = 4096
SAFETY_TOKENS = 2048
MEDIA_TOKENS = {"video": 8192, "audio": 16000}


def image_tokens(detail: str | None = None) -> int:
    return 2048 if detail == "low" else 4096


def input_limit(output_tokens, *, context_window=CONTEXT_TOKENS):
    return context_window - output_tokens - TOOL_RESULT_TOKENS - SAFETY_TOKENS


def _user_content(content):
    if not isinstance(content, list):
        return content, 0
    result, reserved = [], 0
    for part in content:
        value = deepcopy(part)
        if isinstance(value, dict):
            kind = value.get("type")
            if (
                kind == "image_url"
                and isinstance(value.get("image_url"), dict)
                and str(value["image_url"].get("url", "")).startswith("data:image/")
            ):
                reserved += image_tokens(value["image_url"].get("detail"))
                value["image_url"]["url"] = "[managed binary]"
            elif kind == "input_image" and str(value.get("image_url", "")).startswith(
                "data:image/"
            ):
                reserved += image_tokens(value.get("detail"))
                value["image_url"] = "[managed binary]"
            elif kind == "input_audio" and isinstance(value.get("input_audio"), dict):
                reserved += MEDIA_TOKENS["audio"]
                value["input_audio"]["data"] = "[managed binary]"
        result.append(value)
    return result, reserved


def estimate_input_tokens(body, *, references=False):
    # Platform UTF-8 estimate, shared with native summarization; not a vendor tokenizer.
    payload = deepcopy(body)
    reserved = 0
    if references:

        def count_media(value):
            if isinstance(value, dict):
                if isinstance(value.get("audio_ref"), dict):
                    return MEDIA_TOKENS["audio"]
                reference = value.get("image_ref")
                if isinstance(reference, dict):
                    return image_tokens(reference.get("metadata", {}).get("detail"))
                operation = value.get("operation_ref")
                if isinstance(operation, dict):
                    return operation.get("estimated_tokens", 0)
                return sum(count_media(child) for child in value.values())
            if isinstance(value, list):
                return sum(count_media(child) for child in value)
            return 0

        reserved = count_media(payload)
    # Only actual user media parts receive a binary allowance. Tool JSON, including
    # arbitrary objects named data/kind/media_type, is always counted in full.
    messages = payload.get("messages", payload.get("input", []))
    if not references and isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                message["content"], count = _user_content(message.get("content"))
                reserved += count
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return (size + 2) // 3 + reserved


def provider_budget(body, output_tokens, *, references=False, context_window=CONTEXT_TOKENS):
    estimate = estimate_input_tokens(body, references=references)
    limit = input_limit(output_tokens, context_window=context_window)
    budget = {
        "estimated_input_tokens": estimate,
        "context_window_tokens": context_window,
        "input_limit_tokens": limit,
        "reserved_output_tokens": output_tokens,
        "reserved_tool_tokens": TOOL_RESULT_TOKENS,
        "reserved_safety_tokens": SAFETY_TOKENS,
    }
    if estimate > limit:
        messages = body.get("messages", body.get("input", []))
        fixed = {**body, "messages": [item for item in messages if item.get("role") == "system"]}
        fixed.pop("input", None)
        raise AgentGatewayError(
            "agent_context_budget_exhausted",
            context_budget={
                **budget,
                "fixed_estimated_input_tokens": estimate_input_tokens(fixed, references=references),
                "fixed_kind": "system_and_tools",
                "message_count": len(messages),
                **wire_tool_counts(messages),
            },
        )
    return budget


def wire_tool_counts(messages):
    groups, results = [], set()
    for message in messages:
        calls = {item["id"] for item in message.get("tool_calls", [])}
        if message.get("role") == "tool":
            results.add(message.get("tool_call_id"))
        if message.get("type") == "function_call":
            calls.add(message.get("call_id"))
        elif message.get("type") == "function_call_output":
            results.add(message.get("call_id"))
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                calls.add(block.get("id"))
            elif block.get("type") == "tool_result":
                results.add(block.get("tool_use_id"))
        if calls:
            groups.append(calls)
    return {
        "tool_call_count": sum(len(group) for group in groups),
        "tool_result_count": len(results),
        "complete_tool_group_count": sum(group <= results for group in groups),
    }
