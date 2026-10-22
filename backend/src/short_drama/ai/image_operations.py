"""标准图片工具的受控媒体输入；不伪造画布来源或接受任意读取器。"""

from copy import deepcopy

from .canvas_text_adapters import text_image_limits
from .transport import validated_url
from .types import GenerationError


def is_image_operation(request: dict) -> bool:
    source = request.get("source")
    return isinstance(source, dict) and source.get("scene") == "image_operation"


def operation_vision_supported(snapshot, adapter):
    if adapter not in {"openai_chat.v1", "openai_responses.v1"}:
        return False
    capability = snapshot.get("canvas_text_capability") or (
        snapshot.get("capability_cache") or {}
    ).get("canvas_text_capability")
    try:
        count, per_image, _ = text_image_limits(capability)
        return count > 0 and per_image > 0
    except GenerationError:
        return False


def validate_operation_vision(snapshot, byte_size, prompt):
    capability = snapshot.get("canvas_text_capability") or (
        snapshot.get("capability_cache") or {}
    ).get("canvas_text_capability")
    count, per_image, prompt_chars = text_image_limits(capability)
    if count < 1 or byte_size is None or byte_size > per_image:
        raise GenerationError("reference_images_too_large")
    if len(prompt) > prompt_chars:
        raise GenerationError("unsupported_parameters")


def operation_text_messages(snapshot, request, adapter, messages):
    inputs = request.get("input") or {}
    identifiers, urls = inputs.get("reference_media_ids"), inputs.get("reference_urls")
    if (
        not is_image_operation(request)
        or not operation_vision_supported(snapshot, adapter)
        or not isinstance(identifiers, list)
        or len(identifiers) != 1
        or not isinstance(urls, list)
        or len(urls) != 1
        or not messages
        or messages[-1].get("role") != "user"
    ):
        raise GenerationError("unsupported_parameters")
    validated_url(urls[0], query=True)
    result = deepcopy(messages)
    text, url = messages[-1]["content"], urls[0]
    result[-1]["content"] = (
        [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": url}]
        if adapter == "openai_responses.v1"
        else [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": url}}]
    )
    return result
