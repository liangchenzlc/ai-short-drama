"""Google generateContent image protocol; image bytes are loaded by the gateway."""

from urllib.parse import quote, urlsplit, urlunsplit

from short_drama.ai.adapters.image.common import (
    image_capabilities,
    image_input,
    invalid_response,
    unsupported,
    unsupported_poll,
    validate_outputs,
)
from short_drama.ai.adapters.text.gemini_adapters import API_PREFIXES
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationResult


class GeminiImagesAdapter:
    adapter_id = "gemini_images.v1"

    def capabilities(self, snapshot: dict) -> dict:
        return image_capabilities(references=True)

    def build_submission(self, snapshot: dict, request: dict) -> tuple[str, dict, dict]:
        value = image_input(snapshot, request)
        config = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config = {}
        if value.canvas is not None:
            canvas = value.canvas
            if canvas.mask_media_id or canvas.transparent_background:
                unsupported()
            if canvas.size and canvas.size != "auto":
                image_config["aspectRatio"] = canvas.size
            quality = {
                "low": "1K",
                "medium": "2K",
                "high": "4K",
                "1k": "1K",
                "2k": "2K",
                "4k": "4K",
            }.get(canvas.quality)
            if quality:
                image_config["imageSize"] = quality
        else:
            if value.parameters.get("aspect"):
                image_config["aspectRatio"] = value.parameters["aspect"]
            if value.parameters.get("resolution"):
                if value.parameters["resolution"] not in {"1K", "2K", "4K"}:
                    unsupported()
                image_config["imageSize"] = value.parameters["resolution"]
        if image_config:
            config["imageConfig"] = image_config
        parts = [{"text": value.prompt}]
        parts.extend({"fileData": {"fileUri": ref}} for ref in value.references)
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}
        address = urlsplit(validated_url(snapshot["base_url"].strip()))
        path = address.path.rstrip("/")
        for prefix in API_PREFIXES:
            if path.lower().endswith(prefix):
                path = path[: -len(prefix)]
                break
        path += "/v1beta/models/" + quote(value.model, safe=":@&=+$") + ":generateContent"
        return urlunsplit((address.scheme, address.netloc, path, "", "")), {}, body

    def parse_result(self, body: dict, *, submitted: bool, task_id=None) -> GenerationResult:
        if not isinstance(body, dict):
            invalid_response(submitted=submitted)
        feedback = body.get("promptFeedback") or {}
        if body.get("error") or feedback.get("blockReason"):
            return GenerationResult(
                "failed",
                self.adapter_id,
                error={"code": "provider_rejected", "message": "The provider rejected generation."},
            )
        result = GenerationResult("succeeded", self.adapter_id)
        result.usage = (
            body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {}
        )
        try:
            for candidate in body["candidates"]:
                for part in candidate["content"]["parts"]:
                    data = part.get("inlineData") or part.get("inline_data")
                    if data is not None:
                        mime = data.get("mimeType") or data.get("mime_type")
                        if not isinstance(mime, str) or not mime.startswith("image/"):
                            invalid_response(submitted=submitted)
                        result.outputs.append(
                            {"base64": data["data"], "mime": mime, "media_type": "image"}
                        )
            validate_outputs(result, submitted=submitted)
            return result
        except (KeyError, TypeError, AttributeError):
            invalid_response(submitted=submitted)

    def poll_endpoint(self, snapshot: dict, task_id: str) -> str:
        return unsupported_poll()
