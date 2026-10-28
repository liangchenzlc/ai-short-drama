import logging

from short_drama.ai.adapters.text.claude_adapters import CLAUDE_MESSAGES
from short_drama.ai.adapters.text.gemini_adapters import GEMINI_GENERATE_CONTENT
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials, canvas_authentication
from short_drama.ai.types import GenerationError

"""Synchronous calls only; durable scheduling and recording belong to the runtime."""

MAX_REFERENCE_IMAGE_BYTES = 50 * 1024**2

MAX_REFERENCE_TOTAL_BYTES = 100 * 1024**2

log = logging.getLogger(__name__)


def _credential(value):
    secret = value.get_secret_value() if hasattr(value, "get_secret_value") else value or ""
    if (
        not isinstance(secret, str)
        or len(secret) > 16384
        or any(ord(c) < 33 or ord(c) > 126 for c in secret)
    ):
        raise GenerationError("invalid_credential")
    return secret


def _safe_value(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, dict):
        return {_safe_value(k, secret): _safe_value(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(item, secret) for item in value]
    return value


def _authentication(snapshot, credential, headers, *, adapter=None):
    if isinstance(credential, CanvasCredentials):
        return canvas_authentication(snapshot, credential, headers, adapter=adapter)
    if adapter in {CLAUDE_MESSAGES, GEMINI_GENERATE_CONTENT}:
        raise GenerationError("invalid_credential")
    if "canvas_auth_version" in snapshot or "model_auth_version" in snapshot:
        raise GenerationError("invalid_credential")
    secret = _credential(credential)
    headers = dict(headers)
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return secret, headers, [secret] if secret else []
