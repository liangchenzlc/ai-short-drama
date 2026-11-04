"""Volcengine Ark Images request recipes."""

from short_drama.ai.adapters.image.common import (
    image_capabilities,
    image_input,
    parse_images,
    unsupported,
    unsupported_poll,
)
from short_drama.ai.adapters.image.sizes import image_size
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.types import GenerationResult


class ArkImagesAdapter:
    adapter_id = "ark_images.v1"

    def capabilities(self, snapshot: dict) -> dict:
        return image_capabilities(references=True)

    def build_submission(self, snapshot: dict, request: dict) -> tuple[str, dict, dict]:
        value = image_input(snapshot, request)
        body = {"model": value.model, "prompt": value.prompt, "response_format": "b64_json"}
        canvas = value.canvas
        if canvas is not None:
            if value.count != 1 or canvas.mask_media_id or canvas.transparent_background:
                unsupported()
            sizes = {
                "auto": "2k",
                "1:1": "2048x2048",
                "4:3": "2304x1728",
                "3:4": "1728x2304",
                "16:9": "2560x1440",
                "9:16": "1440x2560",
                "3:2": "2496x1664",
                "2:3": "1664x2496",
                "21:9": "3024x1296",
            }
            body["watermark"] = False
            if canvas.size:
                body["size"] = sizes.get(canvas.size, canvas.size)
            if value.references:
                body["image"] = (
                    value.references[0] if len(value.references) == 1 else value.references
                )
        else:
            body["stream"] = False
            size = image_size(value.parameters, family="ark", model=value.model)
            if size:
                body["size"] = size
            if value.references:
                body["image"] = value.references
            body["sequential_image_generation"] = "auto" if value.count > 1 else "disabled"
            if value.count > 1:
                body["sequential_image_generation_options"] = {"max_images": value.count}
        return endpoint(snapshot["base_url"], "/images/generations", self.adapter_id), {}, body

    def parse_result(self, body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
        return parse_images(body, self.adapter_id, submitted=submitted, task_id=task_id)

    def poll_endpoint(self, snapshot: dict, task_id: str) -> str:
        return unsupported_poll()
