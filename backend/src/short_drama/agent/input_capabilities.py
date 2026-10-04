"""Declared input support is distinct from the paid tool-protocol verification."""

from short_drama.ai.adapters import select_adapter
from short_drama.ai.types import GenerationError


def input_capabilities(snapshot):
    try:
        protocol = select_adapter(snapshot)
    except (GenerationError, KeyError, TypeError):
        return {
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
        name = snapshot.get("model_key", "").lower()
        image = name.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "gpt-6", "qwen-vl"))
        audio = protocol == "openai_chat.v1" and name.startswith("gpt-4o-audio")
        evidence = "model_family" if image or audio else "text_only"
    return {
        "text": True,
        "image": image,
        "audio": audio,
        "video": "sampled_frames" if image else "unsupported",
        "evidence": evidence,
    }
