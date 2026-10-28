"""固定源十八个相同Chat兼容合同；路由、模型及能力只取冻结配置。"""

from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError, GenerationResult
from short_drama.schemas.canvas.models.model_catalog import validate_public_json

COMPATIBLE_CHAT_PROTOCOLS = frozenset(
    {
        "atlascloud-chat",
        "baichuan-chat",
        "deepseek-chat",
        "fireworks-chat",
        "groq-chat",
        "kimi-chat",
        "litellm-proxy-chat",
        "localai-chat",
        "mistral-chat",
        "newapi-chat",
        "nvidia-nim-chat",
        "openrouter-chat",
        "siliconflow-chat",
        "together-chat",
        "vllm-chat",
        "xai-grok-chat",
        "yi-chat",
        "zhipu-glm-chat",
    }
)


class CompatibleChatOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    temperature: float | None = Field(None, allow_inf_nan=False)
    top_p: float | None = Field(None, allow_inf_nan=False)
    max_tokens: int | None = Field(None, gt=0)
    tools: list[dict[str, JsonValue]] | None = None
    tool_choice: str | dict[str, JsonValue] | None = None
    response_format: dict[str, JsonValue] | None = None
    stream: bool | None = None
    provider: dict[str, JsonValue] | None = None
    transforms: list[str] | None = None
    extra_body: dict[str, JsonValue] | None = None


def is_compatible_chat(snapshot: dict) -> bool:
    protocol = snapshot.get("canvas_text_protocol")
    if protocol is None:
        return False
    if (
        not isinstance(protocol, str)
        or protocol not in COMPATIBLE_CHAT_PROTOCOLS
        or snapshot.get("service_type") != "text"
    ):
        raise GenerationError("unsupported_protocol")
    return True


def compatible_chat_endpoint(base_url: str) -> str:
    parts = urlsplit(validated_url(base_url.strip()))
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path + "/chat/completions", "", ""))


def apply_compatible_chat_options(
    snapshot: dict, request: dict, body: dict, *, tool: bool = False
) -> dict:
    if not is_compatible_chat(snapshot):
        raise GenerationError("unsupported_protocol")
    try:
        values = deepcopy(snapshot.get("canvas_text_defaults"))
        if values is None:
            values = {}
        metadata = (request.get("canvas_request") or {}).get("input", {}).get("metadata") or {}
        namespaces = metadata.get("providerOptions")
        if namespaces is None:
            namespaces = {}
        protocol = snapshot["canvas_text_protocol"]
        if (
            not isinstance(values, dict)
            or not isinstance(namespaces, dict)
            or namespaces.keys() - {protocol}
        ):
            raise ValueError
        namespace = namespaces.get(protocol)
        if namespace is None:
            namespace = {}
        if not isinstance(namespace, dict):
            raise ValueError
        values.update(namespace)
        validate_public_json(values)
        options = CompatibleChatOptions.model_validate(values).model_dump(exclude_none=True)
        # 编辑器工具始终使用冻结的canonical定义；协议参数不能替换工具或模型。
        if tool:
            for name in ("tools", "tool_choice", "response_format"):
                options.pop(name, None)
        result = {**options, **deepcopy(body)}
        if "max_completion_tokens" in result:
            result["max_tokens"] = result.pop("max_completion_tokens")
        result.pop("stream_options", None)
        return result
    except (TypeError, ValueError, ValidationError, AttributeError):
        raise GenerationError("unsupported_parameters") from None


def parse_compatible_chat_result(body: dict, adapter: str) -> GenerationResult:
    try:
        if (body.get("error") or {}).get("code"):
            return GenerationResult(
                status="failed",
                adapter=adapter,
                error={
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                },
            )
        choice = body["choices"][0]
        message = choice.get("message") or {}
        text = message.get("content") or choice.get("text")
        reasoning = message.get("reasoning_content")
        usage = body.get("usage") or {}
        if (
            not isinstance(text, str)
            or not text.strip()
            or reasoning is not None
            and not isinstance(reasoning, str)
            or not isinstance(usage, dict)
        ):
            raise ValueError
        return GenerationResult(
            status="succeeded",
            adapter=adapter,
            text=text,
            reasoning=reasoning,
            usage=deepcopy(usage),
            finish_reason=choice.get("finish_reason"),
        )
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        raise GenerationError("invalid_response", accepted_unknown=True) from None
