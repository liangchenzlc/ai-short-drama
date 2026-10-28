"""Normalize provider endpoint paths for both ordinary and tool requests."""

from urllib.parse import urlsplit, urlunsplit

from short_drama.ai.gateway.transport import validated_url


def endpoint(base_url, suffix, adapter):
    parts = urlsplit(validated_url(base_url.strip()))
    path = parts.path.rstrip("/")
    if adapter.startswith("dashscope_"):
        # Compatible-mode is a text endpoint, not the native media root.
        for marker in ("/compatible-mode", "/api/v1"):
            if marker in path:
                path = path.split(marker, 1)[0]
                break
        path += "/api/v1"
    else:
        for ending in (
            "/audio/speech",
            "/chat/completions",
            "/responses",
            "/images/generations",
            "/images/edits",
            "/contents/generations/tasks",
            "/videos/generations",
        ):
            if path.endswith(ending):
                path = path[: -len(ending)]
                break
        if not path:
            path = "/api/v3" if adapter.startswith("ark_") else "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path + suffix, "", ""))
