"""Input declarations are hints; actual requests reveal model compatibility."""

from urllib.parse import urlsplit

from short_drama.agent.gateway.inputs import select_protocol
from short_drama.ai.types import GenerationError


def context_profile(snapshot):
    declaration = (snapshot.get("capability_cache") or {}).get("agent_inputs") or {}
    valid = all(
        declaration.get(field) == snapshot.get(field)
        for field in ("row_version", "model_key", "base_url", "credential_identity")
    )
    official = urlsplit(
        snapshot.get("base_url") or ""
    ).hostname == "api.deepseek.com" and snapshot.get("model_key") in {
        "deepseek-flash",
        "deepseek-v4-pro",
    }
    window = declaration.get("context_window_tokens")
    declared_window = valid and type(window) is int and 16384 <= window <= 1048576
    output = declaration.get("max_output_tokens")
    declared_output = valid and type(output) is int and 1 <= output <= 1048576
    return {
        "context_window_tokens": window if declared_window else 1_000_000 if official else 65536,
        "context_window_evidence": (
            "declared"
            if declared_window
            else "official_profile"
            if official
            else "platform_default"
        ),
        "max_output_tokens": output if declared_output else 393_216 if official else 8192,
        "output_capacity_evidence": (
            "declared"
            if declared_output
            else "official_profile"
            if official
            else "platform_default"
        ),
    }


def request_output_tokens(snapshot, source, requested=None):
    profile = context_profile(snapshot)
    capacity = profile["max_output_tokens"]
    auxiliary_limit = {
        "tool_selection": 2048,
        "summarization": 8192,
        "conversation_title": 64,
    }.get(source)
    if auxiliary_limit is not None:
        return min(
            capacity, auxiliary_limit, requested if requested is not None else auxiliary_limit
        )
    if requested is None:
        return capacity
    if profile["output_capacity_evidence"] == "platform_default":
        # 未知模型的 8192 是初值；恢复可逐步放大，同时给固定输入与历史留出空间。
        capacity = profile["context_window_tokens"] - 6144 - 1024
    return min(capacity, requested)


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
