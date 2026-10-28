"""协议选择、托管媒体输入与冻结凭据校验。"""

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    BinaryContent,
)

from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.ai.types import GenerationError
from short_drama.schemas.ai_config.management.runtime_profile import ModelRuntimeProfile

_BINARY_ADAPTER = TypeAdapter(BinaryContent)


def select_agent_protocol(snapshot):
    profile = snapshot.get("runtime_profile")
    if profile is None:
        protocol = select_adapter(snapshot)
    else:
        try:
            profile = ModelRuntimeProfile.model_validate(profile)
        except (ValidationError, TypeError):
            raise AgentGatewayError("unsupported_agent_protocol") from None
        protocol = {
            "chat-completion": "openai_chat.v1",
            "openai-response": "openai_responses.v1",
        }.get(profile.protocol)
        if profile.api_format != "openai":
            raise AgentGatewayError("unsupported_agent_protocol")
    if snapshot.get("service_type") != "text" or protocol not in {
        "openai_chat.v1",
        "openai_responses.v1",
    }:
        raise AgentGatewayError("unsupported_agent_protocol")
    return protocol


def _prompt_content(value, snapshot):
    """Only inline managed bytes are accepted; SDK URL objects remain forbidden."""
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, list) or not value or len(value) > 80:
        raise AgentGatewayError("unsupported_agent_history_content")
    try:
        protocol = select_agent_protocol(snapshot)
    except (GenerationError, KeyError, TypeError):
        raise AgentGatewayError("unsupported_agent_protocol") from None
    parts = []
    total = 0
    for item in value:
        if isinstance(item, str):
            total += len(item.encode("utf-8"))
            parts.append(item)
            continue
        if isinstance(item, dict):
            if item.keys() - {"kind", "data", "media_type", "identifier", "vendor_metadata"}:
                raise AgentGatewayError("unsupported_agent_history_content")
            try:
                item = _BINARY_ADAPTER.validate_python(item)
            except ValidationError:
                raise AgentGatewayError("unsupported_agent_history_content") from None
        if not isinstance(item, BinaryContent):
            raise AgentGatewayError("unsupported_agent_history_content")
        if item.media_type in {"image/jpeg", "image/png", "image/webp"}:
            supported = True
        elif item.media_type in {"audio/wav", "audio/mpeg"}:
            supported = protocol == "openai_chat.v1"
        else:
            supported = False
        if not supported or not item.data:
            raise AgentGatewayError("unsupported_agent_input_modality")
        total += len(item.data)
        parts.append(item)
    if total > 4 * 1024**2 + 262144:
        raise AgentGatewayError("agent_request_too_large")
    return parts


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
