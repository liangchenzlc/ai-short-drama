"""DashScope Qwen/Wan image recipes, both synchronous and task based."""

import re
from urllib.parse import quote

from short_drama.ai.adapters.image.common import (
    image_capabilities,
    image_input,
    invalid_response,
    unsupported,
    validate_outputs,
)
from short_drama.ai.adapters.image.sizes import image_size
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.types import GenerationError, GenerationResult


class DashScopeImagesAdapter:
    adapter_id = "dashscope_images.v1"

    def capabilities(self, snapshot: dict) -> dict:
        model = snapshot.get("model_key", "")
        return image_capabilities(
            references=model.startswith("qwen-image-edit") or model == "wan2.6-image"
        )

    def build_submission(self, snapshot: dict, request: dict) -> tuple[str, dict, dict]:
        value = image_input(snapshot, request)
        model, refs = value.model, value.references
        modern = model.startswith(("qwen-image", "wan2.6", "wan2.7"))
        body = {"model": model}
        params, headers = {"n": value.count}, {}
        canvas = value.canvas
        if canvas is not None:
            if canvas.mask_media_id or canvas.transparent_background:
                unsupported()
            params["watermark"] = False
            if modern:
                sizes = {
                    "1:1": "1024*1024",
                    "3:4": "960*1280",
                    "4:3": "1280*960",
                    "2:3": "1024*1536",
                    "3:2": "1536*1024",
                    "9:16": "864*1536",
                    "16:9": "1536*864",
                }
                for size in (
                    "1024x1024",
                    "1024x1536",
                    "1536x1024",
                    "1024x1280",
                    "1280x1024",
                    "960x1280",
                    "1280x960",
                ):
                    sizes[size] = size.replace("x", "*")
                if canvas.size in sizes:
                    params["size"] = sizes[canvas.size]
                params.update(prompt_extend=True, enable_thinking=False)
                body["input"] = {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"image": ref} for ref in refs] + [{"text": value.prompt}],
                        }
                    ]
                }
                suffix = "/services/aigc/image-generation/generation"
            else:
                body["input"] = {"prompt": value.prompt}
                if canvas.size:
                    params["size"] = canvas.size
                if refs:
                    body["input"]["ref_img" if len(refs) == 1 else "ref_images"] = (
                        refs[0] if len(refs) == 1 else refs
                    )
                suffix = "/services/aigc/text2image/image-synthesis"
            headers["X-DashScope-Async"] = "enable"
        else:
            edit = model.startswith("qwen-image-edit") or model == "wan2.6-image"
            if (
                refs
                and not edit
                or edit
                and (not refs or len(refs) > (4 if model == "wan2.6-image" else 3))
            ):
                unsupported()
            size = (
                value.parameters.get("resolution")
                if model == "wan2.6-image"
                and not value.parameters.get("aspect")
                and value.parameters.get("resolution") in ("1K", "2K")
                else image_size(value.parameters, family="dashscope", model=model)
            )
            if size:
                params["size"] = size
            if model == "wan2.6-image":
                params["enable_interleave"] = False
            if modern:
                body["input"] = {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"image": ref} for ref in refs] + [{"text": value.prompt}],
                        }
                    ]
                }
                suffix = "/services/aigc/multimodal-generation/generation"
            else:
                body["input"] = {"prompt": value.prompt}
                headers["X-DashScope-Async"] = "enable"
                suffix = "/services/aigc/text2image/image-synthesis"
        body["parameters"] = params
        return endpoint(snapshot["base_url"], suffix, self.adapter_id), headers, body

    def parse_result(self, body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
        if not isinstance(body, dict):
            invalid_response(submitted=submitted)
        result = GenerationResult("succeeded", self.adapter_id, provider_task_id=task_id)
        result.usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        if body.get("error") or body.get("code"):
            result.status, result.error = (
                "failed",
                {"code": "provider_rejected", "message": "The provider rejected generation."},
            )
            return result
        try:
            output = body["output"]
            status = output.get("task_status")
            result.provider_task_id = output.get("task_id", task_id)
            if status in ("PENDING", "RUNNING"):
                if not isinstance(result.provider_task_id, str) or not result.provider_task_id:
                    invalid_response(submitted=submitted)
                result.status = "submitted"
            elif status == "UNKNOWN":
                raise GenerationError("provider_outcome_unknown", accepted_unknown=True)
            elif status in ("FAILED", "CANCELED", "CANCELLED"):
                result.status, result.error = (
                    "failed",
                    {
                        "code": "provider_failed",
                        "message": "The provider could not complete generation.",
                    },
                )
            elif status in (None, "SUCCEEDED"):
                if "results" in output:
                    result.outputs = [
                        {"url": item["url"], "media_type": "image"}
                        for item in output["results"]
                        if item.get("url")
                    ]
                else:
                    result.outputs = [
                        {"url": part["image"], "media_type": "image"}
                        for choice in output["choices"]
                        for part in choice["message"]["content"]
                        if part.get("image")
                    ]
                validate_outputs(result, submitted=submitted)
            else:
                invalid_response(submitted=submitted)
            return result
        except (KeyError, TypeError, IndexError, AttributeError):
            invalid_response(submitted=submitted)

    def poll_endpoint(self, snapshot: dict, task_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
            raise GenerationError("invalid_provider_task")
        return endpoint(snapshot["base_url"], f"/tasks/{quote(task_id, safe='')}", self.adapter_id)
