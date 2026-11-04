"""OpenAI Images generation and multipart edits, including existing canvas presets."""

from short_drama.ai.adapters.image.agnes_adapters import AGNES_IMAGE, agnes_image_body
from short_drama.ai.adapters.image.common import (
    image_capabilities,
    image_input,
    openai_image_references,
    parse_images,
    unsupported,
    unsupported_poll,
)
from short_drama.ai.adapters.image.sizes import image_size
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.types import GenerationError, GenerationResult


class OpenAIImagesAdapter:
    adapter_id = "openai_images.v1"

    def capabilities(self, snapshot: dict) -> dict:
        supported = openai_image_references(snapshot.get("model_key", ""))
        return image_capabilities(references=supported, mask=supported)

    def build_submission(self, snapshot: dict, request: dict) -> tuple[str, dict, dict]:
        value = image_input(snapshot, request)
        model, prompt, refs, count = value.model, value.prompt, value.references, value.count
        canvas = value.canvas
        if canvas is not None and model == AGNES_IMAGE:
            return (
                endpoint(snapshot["base_url"], "/images/generations", self.adapter_id),
                {},
                agnes_image_body(prompt, refs, count, canvas),
            )
        if refs and (not openai_image_references(model) or len(refs) > 16):
            unsupported()
        if model.startswith("dall-e-3") and count != 1:
            unsupported()
        body = {"model": model, "prompt": prompt, "n": count}
        if canvas is not None:
            sizes = {
                "1:1": "1024x1024",
                "3:2": "1536x1024",
                "2:3": "1024x1536",
                "4:3": "1360x1024",
                "3:4": "1024x1360",
                "16:9": "1824x1024",
                "9:16": "1024x1824",
                "21:9": "2352x1008",
            }
            body["output_format"] = "png"
            if canvas.size != "auto":
                body["size"] = sizes.get(canvas.size or "1:1", canvas.size)
            if canvas.quality != "auto":
                body["quality"] = {"1k": "low", "2k": "medium", "4k": "high"}.get(
                    canvas.quality, canvas.quality
                )
            if canvas.transparent_background:
                body["background"] = "transparent"
            if canvas.mask_media_id:
                if not refs:
                    unsupported()
                body["mask"] = "https://reference.invalid/mask"
        else:
            try:
                size = image_size(value.parameters, family="openai", model=model)
            except GenerationError:
                raise GenerationError("unsupported_image_size") from None
            if size:
                body["size"] = size
        if refs:
            body["image"] = refs
        suffix = "/images/edits" if refs else "/images/generations"
        return endpoint(snapshot["base_url"], suffix, self.adapter_id), {}, body

    def parse_result(self, body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
        return parse_images(body, self.adapter_id, submitted=submitted, task_id=task_id)

    def poll_endpoint(self, snapshot: dict, task_id: str) -> str:
        return unsupported_poll()
