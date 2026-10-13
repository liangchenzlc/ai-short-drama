"""固定源普通文字 thinking；仅使用服务端冻结的布尔能力。"""

from .types import GenerationError

THINKING_ADAPTERS = frozenset({"openai_chat.v1", "openai_responses.v1", "claude_messages.v1"})


def thinking_parameters(adapter: str | None, enabled: bool, capability: dict | None) -> dict:
    if type(enabled) is not bool:
        raise GenerationError("unsupported_parameters")
    if not enabled:
        return {}
    if (
        adapter not in THINKING_ADAPTERS
        or not isinstance(capability, dict)
        or capability.get("thinking") is not True
    ):
        raise GenerationError("unsupported_parameters")
    if adapter == "openai_chat.v1":
        return {"reasoning_effort": "medium"}
    if adapter == "openai_responses.v1":
        return {"reasoning": {"effort": "medium", "summary": "auto"}}
    return {"thinking": {"type": "enabled", "budget_tokens": 1024}}
