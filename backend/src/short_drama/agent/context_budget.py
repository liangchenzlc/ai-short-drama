"""One bounded input envelope; estimates never authorize truncating protocol state."""

import json
from copy import deepcopy

from short_drama.agent.model_gateway import AgentGatewayError

CONTEXT_TOKENS = 65536
TOOL_RESULT_TOKENS = 4096
SAFETY_TOKENS = 2048
MEDIA_TOKENS = {"image": 2048, "video": 8192, "audio": 16000}


def _measure(value, output_tokens, media_tokens=0):
    # This is a platform estimate, not a vendor tokenizer or advertised model window.
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    estimate = (size + 2) // 3 + media_tokens
    limit = CONTEXT_TOKENS - output_tokens - TOOL_RESULT_TOKENS - SAFETY_TOKENS
    if estimate > limit:
        raise AgentGatewayError("agent_context_budget_exhausted")
    return {
        "estimated_input_tokens": estimate,
        "input_limit_tokens": limit,
        "reserved_output_tokens": output_tokens,
        "reserved_tool_tokens": TOOL_RESULT_TOKENS,
        "reserved_safety_tokens": SAFETY_TOKENS,
    }


def _user_content(content, *, provider=False):
    if not isinstance(content, list):
        return content, 0
    result, reserved = [], 0
    for part in content:
        value = deepcopy(part)
        if isinstance(value, dict):
            if (
                not provider
                and value.get("kind") == "binary"
                and isinstance(value.get("data"), str)
                and value.get("media_type", "").startswith(("image/", "audio/"))
            ):
                reserved += MEDIA_TOKENS[value["media_type"].split("/", 1)[0]]
                value["data"] = "[managed binary]"
            elif provider:
                kind = value.get("type")
                if (
                    kind == "image_url"
                    and isinstance(value.get("image_url"), dict)
                    and str(value["image_url"].get("url", "")).startswith("data:image/")
                ):
                    reserved += MEDIA_TOKENS["image"]
                    value["image_url"]["url"] = "[managed binary]"
                elif kind == "input_image" and str(value.get("image_url", "")).startswith(
                    "data:image/"
                ):
                    reserved += MEDIA_TOKENS["image"]
                    value["image_url"] = "[managed binary]"
                elif kind == "input_audio" and isinstance(value.get("input_audio"), dict):
                    reserved += MEDIA_TOKENS["audio"]
                    value["input_audio"]["data"] = "[managed binary]"
        result.append(value)
    return result, reserved


def segment_budget(values):
    payload = deepcopy(values)
    prompt = payload.get("user_prompt")
    if isinstance(prompt, dict) and prompt.get("codec") == "agent.attachments":
        reserved = sum(MEDIA_TOKENS.get(item.get("kind"), 0) for item in prompt["attachments"])
    else:
        payload["user_prompt"], reserved = _user_content(prompt)
    history = payload.get("history") or {}
    for message in history.get("messages", []):
        for part in message.get("parts", []):
            if part.get("part_kind") == "user-prompt":
                part["content"], count = _user_content(part.get("content"))
                reserved += count
    return _measure(payload, values.get("max_output_tokens", 8192), reserved)


def provider_budget(body, output_tokens):
    payload = deepcopy(body)
    reserved = 0
    # Only actual user media parts receive a binary allowance. Tool JSON, including
    # arbitrary objects named data/kind/media_type, is always counted in full.
    messages = payload.get("messages", payload.get("input", []))
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                message["content"], count = _user_content(message.get("content"), provider=True)
                reserved += count
    return _measure(payload, output_tokens, reserved)
