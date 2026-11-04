"""Public interface metadata and the single protocol-to-adapter mapping."""

from dataclasses import dataclass

from short_drama.ai.adapters.image.registry import IMAGE_PROTOCOLS
from short_drama.ai.adapters.runninghub.adapters import RUNNINGHUB_ADAPTERS
from short_drama.ai.adapters.text.chat_protocols import COMPATIBLE_CHAT_PROTOCOLS
from short_drama.ai.official_models import official_model_options
from short_drama.ai.types import GenerationError


@dataclass(frozen=True)
class ModelInterface:
    id: str
    label: str
    kind: str
    adapter_id: str
    api_format: str = "openai"
    aliases: tuple[str, ...] = ()
    requires_secret_key: bool = False


INTERFACES = (
    ModelInterface("chat-completion", "OpenAI Chat Completions", "text", "openai_chat.v1"),
    ModelInterface("openai-response", "OpenAI Responses", "text", "openai_responses.v1"),
    ModelInterface(
        "claude-messages",
        "Claude Messages",
        "text",
        "claude_messages.v1",
        "claude",
        ("claude-api", "messages"),
    ),
    *(
        ModelInterface(
            item.id, item.label, "image", item.adapter.adapter_id, item.api_format, item.aliases
        )
        for item in IMAGE_PROTOCOLS
    ),
    ModelInterface("volcengine-ark-video", "火山方舟 Video", "video", "ark_video.v1"),
    ModelInterface("minimax-video", "MiniMax Video", "video", "minimax_video.v2"),
    ModelInterface("kling-video", "Kling Video", "video", "kling_video.v4"),
    ModelInterface(
        "dashscope-wan-video",
        "通义万相 Video",
        "video",
        "dashscope_video.v1",
        aliases=("qwen-video",),
    ),
    ModelInterface(
        "newapi",
        "OpenAI Videos",
        "video",
        "canvas_openai_videos.v1",
        aliases=("openai-video", "openai-videos"),
    ),
    ModelInterface(
        "newapi-channel-2", "NewAPI Channel 2", "video", "canvas_newapi_video_generations.v1"
    ),
    ModelInterface("modelhub-video", "ModelHub Video", "video", "modelhub_video.v1"),
    ModelInterface("openai-audio", "OpenAI Audio Speech", "audio", "openai_speech.v1"),
    ModelInterface("async-audio", "异步音频", "audio", "canvas_async_audio.v1"),
    ModelInterface("dashscope-cosyvoice", "DashScope CosyVoice", "audio", "dashscope_speech.v1"),
)
INTERFACE_BY_ID = {key: item for item in INTERFACES for key in (item.id, *item.aliases)}
PROTOCOL_ADAPTERS = {
    **dict.fromkeys(COMPATIBLE_CHAT_PROTOCOLS, "openai_chat.v1"),
    **{key: item.adapter_id for key, item in INTERFACE_BY_ID.items()},
    "gemini-generate-content": "gemini_generate_content.v1",
    "google-gemini-generate-content": "gemini_generate_content.v1",
    "runninghub-workflow": "canvas_runninghub_workflow.v1",
    **{f"runninghub-workflow-{kind}": adapter for adapter, kind in RUNNINGHUB_ADAPTERS.items()},
}
PROVIDER_PRESETS = (
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "types": {"text": "chat-completion", "image": "openai-image", "audio": "openai-audio"},
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "types": {"text": "claude-messages"},
    },
    {
        "id": "google",
        "label": "Google",
        "base_url": "https://generativelanguage.googleapis.com",
        "types": {"image": "gemini-image"},
    },
    {
        "id": "volcengine",
        "label": "火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "types": {
            "text": "chat-completion",
            "image": "volcengine-ark-image",
            "video": "volcengine-ark-video",
        },
    },
    {
        "id": "dashscope",
        "label": "阿里百炼",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "base_urls": {"text": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        "types": {
            "text": "chat-completion",
            "image": "qwen-image",
            "video": "dashscope-wan-video",
            "audio": "dashscope-cosyvoice",
        },
    },
    {
        "id": "xai",
        "label": "xAI",
        "base_url": "https://api.x.ai/v1",
        "types": {"text": "chat-completion", "image": "grok-image"},
    },
    {
        "id": "minimax",
        "label": "MiniMax",
        "base_url": "https://api.minimax.io",
        "types": {"video": "minimax-video"},
    },
    {
        "id": "kling",
        "label": "可灵",
        "base_url": "https://api-beijing.klingai.com",
        "types": {"video": "kling-video"},
    },
)


def public_interface_type(protocol: str, kind: str) -> str:
    adapter_id = PROTOCOL_ADAPTERS.get(protocol)
    return next(
        (item.id for item in INTERFACES if item.kind == kind and item.adapter_id == adapter_id),
        protocol,
    )


def interface_profile(interface_type: str, kind: str) -> dict:
    if interface_type == "auto":
        return {"version": 1, "api_format": "auto", "protocol": "auto"}
    item = INTERFACE_BY_ID.get(interface_type)
    if item is None or item.kind != kind:
        raise GenerationError("unsupported_protocol")
    return {"version": 1, "api_format": item.api_format, "protocol": item.id}


def interface_options(kind: str) -> dict:
    return {
        "interfaces": [
            {"id": "auto", "label": "自动识别", "api_format": "auto", "requires_secret_key": False}
        ]
        + [
            {
                "id": item.id,
                "label": item.label,
                "api_format": item.api_format,
                "requires_secret_key": item.requires_secret_key,
            }
            for item in INTERFACES
            if item.kind == kind
        ],
        "providers": [
            {
                "id": item["id"],
                "label": item["label"],
                "base_url": item.get("base_urls", {}).get(kind, item["base_url"]),
                "interface_type": item["types"][kind],
            }
            for item in PROVIDER_PRESETS
            if kind in item["types"]
        ],
        "models": official_model_options(kind),
    }
