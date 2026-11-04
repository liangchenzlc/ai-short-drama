"""Static image families used by settings, discovery, admission and execution."""

from dataclasses import dataclass

from short_drama.ai.adapters.image.ark_images import ArkImagesAdapter
from short_drama.ai.adapters.image.contracts import ImageProtocolAdapter
from short_drama.ai.adapters.image.dashscope_images import DashScopeImagesAdapter
from short_drama.ai.adapters.image.gemini_images import GeminiImagesAdapter
from short_drama.ai.adapters.image.grok_images import GrokImagesAdapter
from short_drama.ai.adapters.image.openai_images import OpenAIImagesAdapter
from short_drama.ai.types import GenerationError


@dataclass(frozen=True)
class ImageProtocol:
    id: str
    label: str
    adapter: ImageProtocolAdapter
    api_format: str = "openai"
    aliases: tuple[str, ...] = ()
    revision: str = "v1"


IMAGE_PROTOCOLS = (
    ImageProtocol("openai-image", "OpenAI Images", OpenAIImagesAdapter()),
    ImageProtocol("volcengine-ark-image", "火山方舟 Images", ArkImagesAdapter()),
    ImageProtocol(
        "qwen-image",
        "通义／万相 Images",
        DashScopeImagesAdapter(),
        aliases=("dashscope-qwen-image",),
    ),
    ImageProtocol(
        "gemini-image", "Google Gemini Image", GeminiImagesAdapter(), api_format="gemini"
    ),
    ImageProtocol("grok-image", "Grok Image", GrokImagesAdapter()),
)
IMAGE_INTERFACES = {key: item for item in IMAGE_PROTOCOLS for key in (item.id, *item.aliases)}
IMAGE_ADAPTERS = {item.adapter.adapter_id: item.adapter for item in IMAGE_PROTOCOLS}


def get_image_adapter(adapter_id: str) -> ImageProtocolAdapter:
    try:
        return IMAGE_ADAPTERS[adapter_id]
    except KeyError:
        raise GenerationError("unsupported_protocol") from None
