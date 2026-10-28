"""模型段落的结果、能力证据与安全错误契约。"""

import json
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Literal

from pydantic_ai import DeferredToolRequests

from short_drama.ai.types import GenerationError

FRAMEWORK_VERSION = version("pydantic-ai-slim")


HISTORY_CODEC_VERSION = 1


MAX_HISTORY_BYTES = 8 * 1024**2


MAX_REQUEST_BYTES = 8 * 1024**2


MAX_TOOLS = 32


class AgentGatewayError(GenerationError):
    """A safe error whose acceptance flag never authorizes automatic resubmission."""

    def __init__(self, code, *, requests=0, accepted_unknown=False, http_status=None):
        self.requests = requests
        self.completed_segments = ()
        super().__init__(code, accepted_unknown=accepted_unknown, http_status=http_status)


def provider_failure_code(status, content):
    if status in {401, 403}:
        return "agent_provider_authentication"
    if status == 429:
        return "agent_provider_rate_limited"
    if status >= 500 or status == 408:
        return "agent_provider_unknown"
    try:
        payload = json.loads(content[:65536])
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        details = " ".join(str(error.get(key, "")) for key in ("code", "type", "message")).lower()
    except (ValueError, TypeError, AttributeError):
        details = ""
    unsupported = any(word in details for word in ("not support", "unsupported", "not allowed"))
    if unsupported:
        if any(word in details for word in ("tool", "function_call")):
            return "agent_tools_unsupported"
        if any(word in details for word in ("audio", "input_audio")):
            return "agent_audio_input_unsupported"
        if any(word in details for word in ("image", "vision")):
            return "agent_image_input_unsupported"
    return "agent_provider_rejected"


@dataclass(frozen=True, repr=False)
class AgentSegmentResult:
    output: str | DeferredToolRequests
    history: dict
    usage: dict
    protocol: str
    requests: int
    streaming: bool = False
    narration: str = ""


@dataclass(frozen=True)
class AgentCapabilityEvidence:
    protocol: str
    tool_calling: bool
    tool_result_continuation: bool
    streaming: Literal["verified", "not_tested"] = "not_tested"
    requests: int = 0
    framework_version: str = FRAMEWORK_VERSION
    segments: tuple[AgentSegmentResult, ...] = field(default=(), repr=False)
