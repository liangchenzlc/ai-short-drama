"""固定源 hosted Gemini generateContent 普通纯文字 recipe。"""

from copy import deepcopy
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError, GenerationResult

GEMINI_GENERATE_CONTENT = "gemini_generate_content.v1"
API_PREFIXES = ("/api/plan/v3", "/api/v3", "/api/v1", "/v1beta", "/v1", "/v2", "/v3")


class GeminiGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    temperature: float | None = Field(default=None, allow_inf_nan=False)
    topP: float | None = Field(default=None, allow_inf_nan=False)
    topK: int | None = Field(default=None, ge=0)
    maxOutputTokens: int | None = Field(default=None, gt=0)
    candidateCount: int | None = Field(default=None, gt=0)
    stopSequences: list[str] | None = None
    seed: int | None = None
    responseMimeType: Literal["text/plain"] | None = None


class GeminiSafetySetting(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: str = Field(min_length=1, max_length=128)
    threshold: str = Field(min_length=1, max_length=128)


class GeminiTextOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    generationConfig: GeminiGenerationConfig | None = None
    safetySettings: list[GeminiSafetySetting] | None = None
    cachedContent: str | None = Field(default=None, min_length=1, max_length=2048)


def _unsupported():
    raise GenerationError("unsupported_parameters")


def build_gemini_text_submission(snapshot: dict, request: dict) -> tuple[str, dict, dict]:
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
        or inputs.get("reference_media_ids")
        or inputs.get("reference_urls")
        or any(
            canvas_input.get(field)
            for field in ("referenceImages", "referenceVideos", "referenceAudios", "mask")
        )
        or (request.get("canvas_text_references") or {}).get("references")
        or request.get("parameters")
    ):
        _unsupported()
    messages = inputs.get("messages")
    if not isinstance(messages, list) or not messages:
        _unsupported()
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.keys() - {"role", "content"}
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
        ):
            _unsupported()
    if messages[-1] != {"role": "user", "content": canvas.get("prompt")}:
        _unsupported()
    metadata, defaults = (
        canvas_input.get("metadata") or {},
        snapshot.get("canvas_text_defaults") or {},
    )
    if not isinstance(metadata, dict) or not isinstance(defaults, dict):
        _unsupported()
    provider_options = metadata.get("providerOptions") or {}
    if not isinstance(provider_options, dict):
        _unsupported()
    declared = provider_options.get("gemini-generate-content") or {}
    if not isinstance(declared, dict):
        _unsupported()
    try:
        options = GeminiTextOptions.model_validate({**deepcopy(defaults), **deepcopy(declared)})
    except ValidationError:
        _unsupported()
    text_options = canvas_input.get("textOptions") or {}
    config = canvas_input.get("config") or {}
    if (
        not isinstance(text_options, dict)
        or text_options.keys() - {"stream", "thinking"}
        or type(text_options.get("stream", True)) is not bool
        or text_options.get("thinking", False) is not False
        or not isinstance(config, dict)
        or not isinstance(config.get("systemPrompt", ""), str)
    ):
        _unsupported()
    instructions = config.get("systemPrompt", "").strip()
    body = {
        "contents": [
            {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [{"text": message["content"]}],
            }
            for message in messages
            if message["role"] != "system"
        ],
        "systemInstruction": {"parts": [{"text": instructions}]} if instructions else None,
    }
    for name, value in options.model_dump(exclude_none=True).items():
        if value:
            body[name] = value
    parts = urlsplit(validated_url(snapshot["base_url"].strip()))
    path = parts.path.rstrip("/")
    for prefix in API_PREFIXES:
        if path.lower().endswith(prefix):
            path = path[: -len(prefix)]
            break
    model = snapshot.get("model_key")
    if not isinstance(model, str) or not model.strip():
        _unsupported()
    # 保留 Go url.PathEscape 的 segment 规则，不能让模型键注入斜线、query 或 fragment。
    path += "/v1beta/models/" + quote(model, safe=":@&=+$") + ":generateContent"
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), {}, body


def parse_gemini_text_result(body: dict, *, submitted: bool) -> GenerationResult:
    def invalid():
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )

    if not isinstance(body, dict):
        invalid()
    feedback = body.get("promptFeedback")
    if body.get("error") or isinstance(feedback, dict) and feedback.get("blockReason"):
        return GenerationResult(
            "failed",
            GEMINI_GENERATE_CONTENT,
            error={"code": "provider_rejected", "message": "The provider rejected generation."},
        )
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        invalid()
    candidate = candidates[0]
    content = candidate.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        invalid()
    text, reasoning = [], []
    for part in content["parts"]:
        if not isinstance(part, dict):
            invalid()
        if "text" not in part:
            continue
        if not isinstance(part["text"], str) or type(part.get("thought", False)) is not bool:
            invalid()
        (reasoning if part.get("thought") else text).append(part["text"].strip())
    if not "".join(text):
        invalid()
    reason = candidate.get("finishReason")
    if reason is not None and not isinstance(reason, str):
        invalid()
    if reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
        return GenerationResult(
            "failed",
            GEMINI_GENERATE_CONTENT,
            error={"code": "provider_rejected", "message": "The provider rejected generation."},
        )
    return GenerationResult(
        "succeeded",
        GEMINI_GENERATE_CONTENT,
        text="".join(text),
        reasoning="".join(reasoning) or None,
        usage=body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {},
        finish_reason="max_output_tokens" if reason == "MAX_TOKENS" else "stop",
    )
