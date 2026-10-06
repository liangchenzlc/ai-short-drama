"""源 Chat 图片消息构造；只有服务端冻结的画布引用可以进入此分支。"""

from copy import deepcopy

from pydantic import ValidationError

from short_drama.schemas.canvas_text_parameters import CanvasTextReferences

from .transport import validated_url
from .types import GenerationError


def is_canvas_text_request(snapshot: dict, request: dict) -> bool:
    canvas, source = request.get("canvas_request"), request.get("source")
    if not isinstance(canvas, dict) or not isinstance(source, dict):
        return False
    inputs = canvas.get("input")
    return (
        snapshot.get("service_type") == "text"
        and source.get("scene") == "canvas_node"
        and canvas.get("type") == "canvas_text"
        and canvas.get("operation") == "text"
        and isinstance(inputs, dict)
        and inputs.get("mode") == "text"
        and "canvas_text_references" in request
    )


def text_image_limits(capability: dict | None) -> tuple[int, int, int]:
    """缺省源文字模型不声明视觉能力，不能根据模型名称猜测。"""
    if capability is None:
        return 0, 0, 32000
    if not isinstance(capability, dict) or not isinstance(capability.get("references"), dict):
        raise GenerationError("unsupported_parameters")
    if capability.get("streaming") is not None and type(capability["streaming"]) is not bool:
        raise GenerationError("unsupported_parameters")
    references = capability["references"]
    values = []
    for field, default, minimum, maximum in (
        ("maxImages", 0, 0, 100),
        ("maxImageBytes", 0, 0, 2**53 - 1),
        ("promptMaxChars", 32000, 1, 1000000),
    ):
        value = references.get(field, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise GenerationError("unsupported_parameters")
        values.append(value)
    return tuple(values)


def canvas_text_messages(snapshot: dict, request: dict, adapter: str, messages: list) -> list:
    if not is_canvas_text_request(snapshot, request) or adapter != "openai_chat.v1":
        raise GenerationError("unsupported_parameters")
    try:
        envelope = CanvasTextReferences.model_validate(request["canvas_text_references"])
    except ValidationError:
        raise GenerationError("unsupported_parameters") from None
    inputs = request["input"]
    identifiers = [str(reference.media_id) for reference in envelope.references]
    urls = inputs.get("reference_urls")
    declared = (request["canvas_request"].get("input") or {}).get("referenceImages", [])
    if (
        inputs.get("reference_media_ids") != identifiers
        or not isinstance(urls, list)
        or len(urls) != len(identifiers)
        or not isinstance(declared, list)
        or [item.get("storageKey") for item in declared if isinstance(item, dict)]
        != ["resource:" + identifier for identifier in identifiers]
        or not messages
        or messages[-1].get("role") != "user"
        or messages[-1].get("content") != request["canvas_request"].get("prompt")
    ):
        raise GenerationError("unresolved_media_reference")
    count, per_image, prompt_chars = text_image_limits(snapshot.get("canvas_text_capability"))
    if len(identifiers) > count or any(
        reference.byte_size > per_image for reference in envelope.references
    ):
        raise GenerationError("reference_images_too_large")
    if len(messages[-1]["content"]) > prompt_chars:
        raise GenerationError("unsupported_parameters")
    for url in urls:
        if not isinstance(url, str):
            raise GenerationError("unresolved_media_reference")
        validated_url(url, query=True)
    result = deepcopy(messages)
    result[-1]["content"] = [
        {"type": "text", "text": messages[-1]["content"]},
        *[{"type": "image_url", "image_url": {"url": url}} for url in urls],
    ]
    return result
