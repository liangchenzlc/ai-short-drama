"""协议选择、托管媒体输入与冻结凭据校验。"""

from pydantic import ValidationError

from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials


def select_protocol(snapshot: dict) -> str:
    profile = snapshot.get("runtime_profile") or {}
    if profile:
        protocol = {
            ("openai", "chat-completion"): "openai_chat.v1",
            ("openai", "openai-response"): "openai_responses.v1",
            ("claude", "claude-messages"): "claude_messages.v1",
            ("claude", "messages"): "claude_messages.v1",
        }.get((profile.get("api_format"), profile.get("protocol")))
    else:
        protocol = select_adapter(snapshot)
    if snapshot.get("service_type") != "text" or protocol not in {
        "openai_chat.v1",
        "openai_responses.v1",
        "claude_messages.v1",
    }:
        raise AgentGatewayError("unsupported_agent_protocol")
    return protocol


def _credential(value):
    secret = value.get_secret_value() if hasattr(value, "get_secret_value") else value or ""
    if (
        not isinstance(secret, str)
        or len(secret) > 16384
        or any(ord(char) < 33 or ord(char) > 126 for char in secret)
    ):
        raise AgentGatewayError("invalid_credential")
    return secret


def _credentials(value):
    if not isinstance(value, CanvasCredentials):
        secret = _credential(value)
        return secret, {}, [secret] if secret else []
    try:
        value = CanvasCredentials.model_validate(value.model_dump())
    except (ValueError, ValidationError):
        raise AgentGatewayError("invalid_credential") from None
    secret = _credential(value.api_key)
    headers = {}
    secrets = [secret] if secret else []
    for item in value.headers:
        raw = item.value.get_secret_value()
        headers[item.name.lower()] = raw if raw.isascii() else raw.encode("utf-8")
        if raw:
            secrets.append(raw)
    return secret, headers, sorted(set(secrets), key=len, reverse=True)
