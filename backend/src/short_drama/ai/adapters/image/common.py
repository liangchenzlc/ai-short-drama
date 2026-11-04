"""Shared input and output boundaries; vendor request shapes stay in their adapters."""

from dataclasses import dataclass

from pydantic import ValidationError

from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError, GenerationResult
from short_drama.schemas.canvas.generation.generation_parameters import CanvasImageParameters


def unsupported() -> None:
    raise GenerationError("unsupported_parameters")


def openai_image_references(model: str) -> bool:
    return model.startswith("gpt-image-")


@dataclass(frozen=True)
class ImageInput:
    model: str
    prompt: str
    references: list[str]
    count: int
    parameters: dict
    canvas: CanvasImageParameters | None


def image_input(snapshot: dict, request: dict) -> ImageInput:
    if snapshot.get("service_type") != "image":
        raise GenerationError("unsupported_protocol")
    value = request.get("input", {})
    if not isinstance(value, dict) or value.keys() - {
        "prompt",
        "reference_media_ids",
        "reference_urls",
    }:
        unsupported()
    prompt, refs = value.get("prompt"), value.get("reference_urls", [])
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(refs, list):
        unsupported()
    if value.get("reference_media_ids") and not refs:
        raise GenerationError("unresolved_media_reference")
    for ref in refs:
        validated_url(ref, query=True)
    params = {key: item for key, item in request.get("parameters", {}).items() if item is not None}
    if params.keys() - {"aspect", "resolution", "count"}:
        unsupported()
    count = params.get("count", 1)
    if type(count) is not int or not 1 <= count <= 4:
        unsupported()
    canvas = None
    raw = request.get("image_edit_parameters", request.get("canvas_parameters"))
    if raw is not None:
        scene = (request.get("source") or {}).get("scene")
        if scene not in {"canvas_node", "canvas_model_test", "image_operation"}:
            unsupported()
        if params.keys() - {"count"}:
            unsupported()
        try:
            canvas = CanvasImageParameters.model_validate(raw)
        except ValidationError:
            raise GenerationError("unsupported_parameters") from None
    return ImageInput(snapshot["model_key"], prompt, refs, count, params, canvas)


def image_capabilities(*, references: bool, mask: bool = False) -> dict:
    return {
        "known": True,
        "parameters": ["aspect", "resolution", "count"],
        "reference_images": references,
        "mask": mask,
        "first_frame": False,
        "last_frame": False,
        "vision": False,
    }


def invalid_response(*, submitted: bool) -> None:
    raise GenerationError("invalid_response", accepted_unknown=submitted, retryable=not submitted)


def parse_images(body: dict, adapter_id: str, *, submitted: bool, task_id=None) -> GenerationResult:
    if not isinstance(body, dict):
        invalid_response(submitted=submitted)
    if body.get("error") or body.get("code"):
        return GenerationResult(
            "failed",
            adapter_id,
            error={"code": "provider_rejected", "message": "The provider rejected generation."},
        )
    result = GenerationResult("succeeded", adapter_id, provider_task_id=task_id)
    result.usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    try:
        for item in body["data"]:
            if item.get("url"):
                result.outputs.append({"url": item["url"], "media_type": "image"})
            elif item.get("b64_json"):
                result.outputs.append(
                    {"base64": item["b64_json"], "media_type": "image", "mime": "image/png"}
                )
            elif not item.get("error"):
                invalid_response(submitted=submitted)
        validate_outputs(result, submitted=submitted)
        return result
    except (KeyError, TypeError, AttributeError):
        invalid_response(submitted=submitted)


def validate_outputs(result: GenerationResult, *, submitted: bool) -> None:
    if not result.outputs or len(result.outputs) > 4:
        invalid_response(submitted=submitted)
    for output in result.outputs:
        value = output.get("url") or output.get("base64")
        if not isinstance(value, str) or not value:
            invalid_response(submitted=submitted)


def unsupported_poll() -> str:
    raise GenerationError("unsupported_poll")
