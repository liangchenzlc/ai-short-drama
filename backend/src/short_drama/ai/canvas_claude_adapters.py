"""固定源 Anthropic Messages 普通文字 recipe，仅用于画布任务。"""

from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from .canvas_text_adapters import canvas_text_messages
from .canvas_text_thinking import thinking_parameters
from .transport import validated_url
from .types import GenerationError, GenerationResult

CLAUDE_MESSAGES = "claude_messages.v1"


class ClaudeTextOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    max_tokens: int = Field(default=4096, gt=0)
    system: str | None = None
    temperature: float | None = Field(default=None, allow_inf_nan=False)
    top_p: float | None = Field(default=None, allow_inf_nan=False)
    top_k: int | None = Field(default=None, ge=0)
    stop_sequences: list[str] | None = None
    metadata: dict[str, JsonValue] | None = None
    service_tier: str | None = None
    stream: bool | None = None
    anthropic_version: str = Field(
        default="2023-06-01", alias="anthropic-version", pattern=r"^[\x21-\x7e]+$", max_length=128
    )
    anthropic_beta: str | None = Field(
        default=None, alias="anthropic-beta", pattern=r"^[\x20-\x7e]+$", max_length=1024
    )


def _unsupported():
    raise GenerationError("unsupported_parameters")


def build_claude_text_submission(snapshot: dict, request: dict) -> tuple[str, dict, dict]:
    canvas, source = request.get("canvas_request"), request.get("source")
    if (
        snapshot.get("service_type") != "text"
        or not isinstance(canvas, dict)
        or not isinstance(source, dict)
        or source.get("scene") not in {"canvas_node", "canvas_model_test"}
        or canvas.get("type") != "canvas_text"
        or canvas.get("operation") != "text"
    ):
        raise GenerationError("unsupported_protocol")
    inputs, canvas_input = request.get("input"), canvas.get("input")
    if (
        not isinstance(inputs, dict)
        or inputs.keys() - {"messages", "reference_media_ids", "reference_urls"}
        or not isinstance(canvas_input, dict)
        or canvas_input.get("mode") != "text"
        or any(canvas_input.get(field) for field in ("referenceVideos", "referenceAudios", "mask"))
    ):
        _unsupported()
    messages = inputs.get("messages")
    if not isinstance(messages, list) or not messages:
        _unsupported()
    for item in messages:
        if (
            not isinstance(item, dict)
            or item.keys() - {"role", "content"}
            or item.get("role") not in {"system", "user", "assistant"}
            or not isinstance(item.get("content"), str)
        ):
            _unsupported()
    if messages[-1] != {"role": "user", "content": canvas.get("prompt")}:
        _unsupported()
    metadata = canvas_input.get("metadata") or {}
    defaults = snapshot.get("canvas_text_defaults") or {}
    if not isinstance(metadata, dict) or not isinstance(defaults, dict):
        _unsupported()
    provider_options = metadata.get("providerOptions") or {}
    if not isinstance(provider_options, dict):
        _unsupported()
    declared = provider_options.get("claude-api") or {}
    parameters = request.get("parameters") or {}
    if (
        not isinstance(declared, dict)
        or not isinstance(parameters, dict)
        or parameters.keys() - {"temperature", "max_output_tokens"}
    ):
        _unsupported()
    values = {**deepcopy(defaults), **deepcopy(declared)}
    for source_key, target in (("max_output_tokens", "max_tokens"), ("temperature", "temperature")):
        if parameters.get(source_key) is not None:
            values[target] = parameters[source_key]
    try:
        options = ClaudeTextOptions.model_validate(values)
    except ValidationError:
        _unsupported()
    text_options = canvas_input.get("textOptions") or {}
    if (
        not isinstance(text_options, dict)
        or text_options.keys() - {"stream", "thinking"}
        or type(text_options.get("stream", True)) is not bool
        or type(text_options.get("thinking", False)) is not bool
    ):
        _unsupported()
    if (
        inputs.get("reference_media_ids")
        or inputs.get("reference_urls")
        or canvas_input.get("referenceImages")
        or (request.get("canvas_text_references") or {}).get("references")
    ):
        messages = canvas_text_messages(snapshot, request, CLAUDE_MESSAGES, messages)
    body = options.model_dump(exclude_none=True, exclude={"anthropic_version", "anthropic_beta"})
    body.update(
        thinking_parameters(
            CLAUDE_MESSAGES,
            text_options.get("thinking", False),
            snapshot.get("canvas_text_capability"),
        )
    )
    if "thinking" in body and body["max_tokens"] <= body["thinking"]["budget_tokens"]:
        _unsupported()
    body.update(
        model=snapshot["model_key"],
        messages=deepcopy([item for item in messages if item["role"] != "system"]),
        stream=text_options.get("stream", True),
    )
    capability = snapshot.get("canvas_text_capability")
    if isinstance(capability, dict) and capability.get("streaming") is False:
        body["stream"] = False
    config = canvas_input.get("config") or {}
    if not isinstance(config, dict) or not isinstance(config.get("systemPrompt", ""), str):
        _unsupported()
    instructions = config.get("systemPrompt", "").strip()
    if instructions:
        body["system"] = instructions
    elif not body.get("system"):
        body.pop("system", None)
    headers = {"anthropic-version": options.anthropic_version}
    if options.anthropic_beta:
        headers["anthropic-beta"] = options.anthropic_beta
    parts = urlsplit(validated_url(snapshot["base_url"].strip()))
    path = parts.path.rstrip("/")
    if not path.endswith("/v1/messages"):
        path += "/messages" if path.endswith("/v1") else "/v1/messages"
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), headers, body


def parse_claude_text_result(body: dict, *, submitted: bool) -> GenerationResult:
    def invalid():
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )

    if not isinstance(body, dict):
        invalid()
    if body.get("error"):
        return GenerationResult(
            "failed",
            CLAUDE_MESSAGES,
            error={"code": "provider_rejected", "message": "The provider rejected generation."},
        )
    content = body.get("content")
    if not isinstance(content, list):
        invalid()
    text, reasoning = [], []
    for block in content:
        if not isinstance(block, dict):
            invalid()
        kind = block.get("type")
        if kind not in {"text", "thinking"}:
            continue
        value = block.get("text" if kind == "text" else "thinking")
        if not isinstance(value, str):
            invalid()
        (text if kind == "text" else reasoning).append(value.strip())
    if not text or not "".join(text):
        invalid()
    reason = body.get("stop_reason")
    if reason is not None and not isinstance(reason, str):
        invalid()
    return GenerationResult(
        "succeeded",
        CLAUDE_MESSAGES,
        text="".join(text),
        reasoning="".join(reasoning) or None,
        usage=body.get("usage") if isinstance(body.get("usage"), dict) else {},
        finish_reason="max_output_tokens" if reason == "max_tokens" else "stop",
    )
