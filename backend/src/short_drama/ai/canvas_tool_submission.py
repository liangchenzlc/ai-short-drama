"""只接受内部冻结的编辑器工具请求；路由、参数与媒体权限来自服务端快照。"""

from copy import deepcopy
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from short_drama.schemas.canvas_tool_contracts import CanvasToolCanonical, CanvasToolReferences

from .canvas_chat_protocols import (
    apply_compatible_chat_options,
    compatible_chat_endpoint,
    is_compatible_chat,
)
from .canvas_claude_adapters import ClaudeTextOptions
from .canvas_gemini_adapters import API_PREFIXES, GeminiTextOptions
from .canvas_text_adapters import text_image_limits
from .canvas_text_thinking import thinking_parameters
from .canvas_tool_adapters import CANVAS_TOOL_ADAPTERS, build_canvas_tool_body
from .transport import validated_url
from .types import GenerationError


class OpenAIToolOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    temperature: float | None = Field(default=None, allow_inf_nan=False)
    top_p: float | None = Field(default=None, allow_inf_nan=False)
    max_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


def is_canvas_tool_request(request: dict) -> bool:
    return "canvas_tool_canonical" in request


def build_canvas_tool_submission(
    snapshot: dict, request: dict, adapter: str
) -> tuple[str, dict, dict]:
    if (
        adapter not in CANVAS_TOOL_ADAPTERS
        or snapshot.get("service_type") != "text"
        or (request.get("source") or {}).get("scene") != "canvas_tool"
    ):
        raise GenerationError("unsupported_parameters")
    try:
        canvas = request["canvas_request"]
        inputs = canvas["input"]
        canonical = CanvasToolCanonical.model_validate(request["canvas_tool_canonical"])
        repeated = CanvasToolCanonical.model_validate(inputs["agentRequests"]["canonical"])
        if (
            canonical != repeated
            or canvas["type"] != "canvas_text"
            or canvas["operation"] != "text"
        ):
            raise ValueError
        if (
            inputs["mode"] != "text"
            or inputs.get("referenceVideos")
            or inputs.get("referenceAudios")
            or inputs.get("mask")
        ):
            raise ValueError
        metadata = inputs.get("metadata") or {}
        name = canonical.tools[0].function.name
        if name == "optimize_prompt":
            if metadata.get("source") != "prompt-optimizer":
                raise ValueError
        elif metadata.get("source") != "art-critique" or metadata.get("stage") != name:
            raise ValueError
        options = inputs.get("textOptions") or {}
        if (
            options.keys() - {"stream", "thinking"}
            or type(options.get("stream", True)) is not bool
            or type(options.get("thinking", False)) is not bool
        ):
            raise ValueError
        envelope = CanvasToolReferences.model_validate(request["canvas_text_references"])
        count, per_image, prompt_chars = text_image_limits(snapshot.get("canvas_text_capability"))
        if len(envelope.references) > count or any(
            item.byte_size > per_image for item in envelope.references
        ):
            raise GenerationError("reference_images_too_large")
        text_chars = len(canonical.system_prompt) + sum(
            len(message.content)
            if isinstance(message.content, str)
            else sum(len(part.text) for part in message.content if part.type == "text")
            for message in canonical.messages
        )
        if text_chars > prompt_chars:
            raise ValueError
        identifiers = [str(item.media_id) for item in envelope.references]
        keys = ["resource:" + identifier for identifier in identifiers]
        urls = request["input"].get("reference_urls", [])
        if (
            keys != canonical.image_keys()
            or request["input"].get("reference_media_ids", []) != identifiers
            or [item.get("storageKey") for item in inputs.get("referenceImages", [])] != keys
            or not isinstance(urls, list)
            or len(urls) != len(keys)
        ):
            raise GenerationError("unresolved_media_reference")
        references = {
            key: {"url": url, "mime_type": reference.mime_type}
            for key, url, reference in zip(keys, urls, envelope.references, strict=True)
        }
        body = build_canvas_tool_body(canonical, adapter, image_references=references)
        body.update(
            thinking_parameters(
                adapter, options.get("thinking", False), snapshot.get("canvas_text_capability")
            )
        )
        if adapter == "openai_chat.v1" and options.get("thinking", False):
            body.pop("tool_choice", None)
        model = snapshot["model_key"]
        if not isinstance(model, str) or not model.strip():
            raise ValueError
        base = urlsplit(validated_url(snapshot["base_url"].strip()))
        path = base.path.rstrip("/")
        stream = options.get("stream", True)
        if (snapshot.get("canvas_text_capability") or {}).get("streaming") is False:
            stream = False
        defaults = deepcopy(snapshot.get("canvas_text_defaults") or {})
        parameters = request.get("parameters") or {}
        if not isinstance(parameters, dict) or parameters.keys() - {
            "temperature",
            "max_output_tokens",
        }:
            raise ValueError
        if adapter == "claude_messages.v1":
            if parameters.get("max_output_tokens") is not None:
                defaults["max_tokens"] = parameters["max_output_tokens"]
            if parameters.get("temperature") is not None:
                defaults["temperature"] = parameters["temperature"]
            saved = ClaudeTextOptions.model_validate(defaults)
            values = saved.model_dump(
                exclude_none=True,
                exclude={"anthropic_version", "anthropic_beta", "system", "stream"},
            )
            body.update(values, model=model, stream=stream)
            if "thinking" in body and body["max_tokens"] <= body["thinking"]["budget_tokens"]:
                raise ValueError
            if not path.endswith("/v1/messages"):
                path += "/messages" if path.endswith("/v1") else "/v1/messages"
            headers = {"anthropic-version": saved.anthropic_version}
            if saved.anthropic_beta:
                headers["anthropic-beta"] = saved.anthropic_beta
            return urlunsplit((base.scheme, base.netloc, path, "", "")), headers, body
        if adapter == "gemini_generate_content.v1":
            if parameters:
                raise ValueError
            body.update(GeminiTextOptions.model_validate(defaults).model_dump(exclude_none=True))
            for prefix in API_PREFIXES:
                if path.lower().endswith(prefix):
                    path = path[: -len(prefix)]
                    break
            path += "/v1beta/models/" + quote(model, safe=":@&=+$") + ":generateContent"
            return urlunsplit((base.scheme, base.netloc, path, "", "")), {}, body
        # 已存在的OpenAI路径规范化与普通任务相同；调用时模块已完成初始化。
        from .adapters import endpoint

        compatible = adapter == "openai_chat.v1" and is_compatible_chat(snapshot)
        saved = OpenAIToolOptions.model_validate(
            parameters if compatible else {**defaults, **parameters}
        )
        body.update(
            saved.model_dump(exclude_none=True, exclude={"max_tokens", "max_output_tokens"})
        )
        tokens = (
            saved.max_output_tokens if saved.max_output_tokens is not None else saved.max_tokens
        )
        if tokens is not None:
            body["max_output_tokens" if adapter == "openai_responses.v1" else "max_tokens"] = tokens
        body.update(model=model, stream=stream)
        if stream and adapter == "openai_chat.v1":
            body["stream_options"] = {"include_usage": True}
        if compatible:
            body = apply_compatible_chat_options(snapshot, request, body, tool=True)
            return compatible_chat_endpoint(snapshot["base_url"]), {}, body
        return (
            endpoint(
                snapshot["base_url"],
                "/responses" if adapter == "openai_responses.v1" else "/chat/completions",
                adapter,
            ),
            {},
            body,
        )
    except GenerationError:
        raise
    except (KeyError, TypeError, ValueError, ValidationError, AttributeError):
        raise GenerationError("unsupported_parameters") from None


def canvas_tool_resolved_parameters(body: dict) -> dict:
    # 不将工具定义、systemInstruction、contents或执行期签名URL写入任务参数归档。
    return {
        key: deepcopy(body[key])
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
            "max_output_tokens",
            "stream",
            "reasoning_effort",
            "reasoning",
            "thinking",
            "generationConfig",
            "safetySettings",
        )
        if key in body
    }
