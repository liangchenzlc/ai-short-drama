"""Input declarations are hints; actual requests reveal model compatibility."""

from short_drama.agent.gateway.langchain import select_protocol
from short_drama.ai.types import GenerationError


def context_profile(snapshot):
    declaration = (snapshot.get("capability_cache") or {}).get("agent_inputs") or {}
    valid = all(
        declaration.get(field) == snapshot.get(field)
        for field in ("row_version", "model_key", "base_url", "credential_identity")
    )
    window = declaration.get("context_window_tokens")
    declared = valid and type(window) is int and 16384 <= window <= 1048576
    return {
        "context_window_tokens": min(window, 262144) if declared else 65536,
        "context_window_evidence": "declared" if declared else "platform_default",
    }


def input_capabilities(snapshot):
    try:
        protocol = select_protocol(snapshot)
    except (GenerationError, KeyError, TypeError):
        return {
            **context_profile(snapshot),
            "text": True,
            "image": False,
            "audio": False,
            "video": "unsupported",
            "evidence": "text_only",
        }
    declaration = (snapshot.get("capability_cache") or {}).get("agent_inputs") or {}
    valid = all(
        declaration.get(field) == snapshot.get(field)
        for field in ("row_version", "model_key", "base_url", "credential_identity")
    )
    if valid:
        image, audio, evidence = (
            declaration.get("image") is True,
            declaration.get("audio") is True and protocol == "openai_chat.v1",
            "declared",
        )
    else:
        image = True
        audio = protocol == "openai_chat.v1"
        evidence = "runtime"
    return {
        **context_profile(snapshot),
        "text": True,
        "image": image,
        "audio": audio,
        "video": "sampled_frames" if image else "unsupported",
        "evidence": evidence,
    }
