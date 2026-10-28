"""私有模型历史及工具回执编解码，保持重放标识不变。"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
)

from short_drama.agent.gateway.contracts import (
    FRAMEWORK_VERSION,
    HISTORY_CODEC_VERSION,
    MAX_HISTORY_BYTES,
    AgentGatewayError,
    AgentSegmentResult,
)

_DEFERRED_ADAPTER = TypeAdapter(DeferredToolRequests)


_RESULTS_ADAPTER = TypeAdapter(DeferredToolResults)


def _json_value(value):
    """Normalize only finite, JSON-compatible data, without reflecting bad values."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_HISTORY_BYTES:
            raise ValueError
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError):
        raise AgentGatewayError("invalid_agent_history") from None


def serialize_history(messages: Sequence[ModelMessage]) -> dict:
    try:
        payload = ModelMessagesTypeAdapter.dump_python(messages, mode="json", warnings="error")
        return _json_value(
            {
                "codec": "pydantic-ai.messages",
                "version": HISTORY_CODEC_VERSION,
                "framework_version": FRAMEWORK_VERSION,
                "messages": payload,
            }
        )
    except (ValueError, TypeError):
        raise AgentGatewayError("invalid_agent_history") from None


def deserialize_history(payload: Mapping[str, Any] | None) -> list[ModelMessage]:
    if payload is None:
        return []
    normalized = _json_value(payload)
    if (
        not isinstance(normalized, dict)
        or normalized.get("codec") != "pydantic-ai.messages"
        or normalized.get("version") != HISTORY_CODEC_VERSION
        or normalized.get("framework_version") != FRAMEWORK_VERSION
        or not isinstance(normalized.get("messages"), list)
    ):
        raise AgentGatewayError("unsupported_agent_history")
    try:
        return ModelMessagesTypeAdapter.validate_python(normalized["messages"])
    except (ValueError, TypeError, ValidationError):
        raise AgentGatewayError("invalid_agent_history") from None


def serialize_deferred_requests(requests: DeferredToolRequests) -> dict:
    return _json_value(_DEFERRED_ADAPTER.dump_python(requests, mode="json", warnings="error"))


def deserialize_deferred_results(payload: Mapping[str, Any]) -> DeferredToolResults:
    try:
        return _RESULTS_ADAPTER.validate_python(_json_value(payload))
    except (ValueError, TypeError, ValidationError):
        raise AgentGatewayError("invalid_deferred_results") from None


def serialize_segment_result(result: AgentSegmentResult) -> dict:
    """Private normalized layer; tools/history must never be projected as public text."""
    deferred = isinstance(result.output, DeferredToolRequests)
    return {
        "output_kind": "tool_requests" if deferred else "text",
        "output": serialize_deferred_requests(result.output) if deferred else result.output,
        "narration": result.narration,
        "history": result.history,
        "usage": result.usage,
        "protocol": result.protocol,
        "requests": result.requests,
        "streaming": result.streaming,
    }
