"""Grok image generation and its JSON, single-reference edit operation."""

from short_drama.ai.adapters.image.common import (
    image_capabilities,
    image_input,
    parse_images,
    unsupported,
    unsupported_poll,
)
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.types import GenerationResult


class GrokImagesAdapter:
    adapter_id = "grok_images.v1"

    def capabilities(self, snapshot: dict) -> dict:
        return image_capabilities(references=True)

    def build_submission(self, snapshot: dict, request: dict) -> tuple[str, dict, dict]:
        value = image_input(snapshot, request)
        if len(value.references) > 1:
            unsupported()
        body = {
            "model": value.model,
            "prompt": value.prompt,
            "n": value.count,
            "response_format": "url",
        }
        aspect, resolution = value.parameters.get("aspect"), value.parameters.get("resolution")
        if value.canvas is not None:
            canvas = value.canvas
            if canvas.mask_media_id or canvas.transparent_background:
                unsupported()
            aspect = canvas.size if canvas.size and ":" in canvas.size else None
            resolution = {"low": "1k", "medium": "2k", "high": "2k", "1k": "1k", "2k": "2k"}.get(
                canvas.quality
            )
        if aspect:
            body["aspect_ratio"] = aspect
        if resolution:
            body["resolution"] = resolution.lower()
        if value.references:
            body["image"] = {"url": value.references[0]}
        suffix = "/images/edits" if value.references else "/images/generations"
        return endpoint(snapshot["base_url"], suffix, self.adapter_id), {}, body

    def parse_result(self, body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
        return parse_images(body, self.adapter_id, submitted=submitted, task_id=task_id)

    def poll_endpoint(self, snapshot: dict, task_id: str) -> str:
        return unsupported_poll()
