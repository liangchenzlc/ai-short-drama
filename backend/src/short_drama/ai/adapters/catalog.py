import hashlib
import json
from urllib.parse import urlsplit

from short_drama.ai.adapters.constants import ADAPTER_TYPES, RESOLVER_VERSION
from short_drama.ai.gateway.endpoints import endpoint as endpoint
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError


def capability_fingerprint(snapshot, credential_identity):
    """credential_identity is a hash of the stored ciphertext, never the plaintext key."""
    value = [
        validated_url(snapshot["base_url"].strip()).rstrip("/"),
        snapshot.get("model_key"),
        snapshot.get("service_type"),
        credential_identity,
        RESOLVER_VERSION,
    ]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def select_adapter(snapshot):
    parts = urlsplit(validated_url(snapshot["base_url"].strip()))
    kind = snapshot.get("service_type")
    cache = snapshot.get("capability_cache")
    identity = snapshot.get("credential_identity")
    if isinstance(cache, dict) and isinstance(identity, str):
        cached_adapter = cache.get("adapter")
        if (
            cached_adapter != "dashscope_voice_design.v1"
            and ADAPTER_TYPES.get(cached_adapter) == kind
            and cache.get("fingerprint") == capability_fingerprint(snapshot, identity)
        ):
            return cached_adapter
    host, path = parts.hostname, parts.path.rstrip("/")
    ark = host.endswith(".volces.com") or "/api/v3/contents/generations" in path
    dashscope = (
        host
        in {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
        }
        or host.endswith(".maas.aliyuncs.com")
        or "/api/v1/services/aigc/" in path
    )
    if kind == "audio":
        if (
            dashscope
            and "/compatible-mode" not in path
            and (
                snapshot.get("model_key", "").startswith("cosyvoice-")
                or path.endswith("/services/audio/tts/SpeechSynthesizer")
            )
        ):
            return "dashscope_speech.v1"
        return "openai_speech.v1"
    if kind == "text":
        return "openai_responses.v1" if path.endswith("/responses") else "openai_chat.v1"
    if kind == "image":
        if ark:
            return "ark_images.v1"
        if dashscope:
            return "dashscope_images.v1"
        # Images is a documented common protocol; unsupported gateways fail explicitly.
        return "openai_images.v1"
    if kind == "video":
        if host == "api.modelhub.cc":
            return "modelhub_video.v1"
        if ark:
            return "ark_video.v1"
        if dashscope:
            return "dashscope_video.v1"
    raise GenerationError("unsupported_protocol")


def _unsupported():
    raise GenerationError("unsupported_parameters")
