"""Input declarations are hints; actual requests reveal model compatibility."""

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
        image = True
        audio = protocol == "openai_chat.v1"
        evidence = "runtime"
    return {
        "text": True,
        "image": image,
        "audio": audio,
        "video": "sampled_frames" if image else "unsupported",
        "evidence": evidence,
    }
