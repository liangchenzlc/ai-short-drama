"""Synchronous calls only; durable scheduling and recording belong to the runtime."""

import base64
import json
import time
from io import BytesIO

from PIL import Image

from .adapters import (
    ADAPTER_TYPES,
    build_submission,
    parse_result,
    poll_endpoint,
    resolved_parameters,
    select_adapter,
    validate_request,
)
from .streaming import decode_text_stream
from .transport import MultipartBody, SafeTransport
from .types import GenerationError, GenerationResult

MAX_REFERENCE_IMAGE_BYTES = 50 * 1024**2
MAX_REFERENCE_TOTAL_BYTES = 100 * 1024**2


def _credential(value):
    secret = value.get_secret_value() if hasattr(value, "get_secret_value") else value or ""
    if (
        not isinstance(secret, str)
        or len(secret) > 16384
        or any(ord(c) < 33 or ord(c) > 126 for c in secret)
    ):
        raise GenerationError("invalid_credential")
    return secret


def _safe_value(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, dict):
        return {_safe_value(k, secret): _safe_value(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(item, secret) for item in value]
    return value


class GenerationGateway:
    def __init__(self, settings):
        self.settings = settings
        self.transport = SafeTransport(settings)

    def submit(
        self,
        snapshot,
        request_data,
        credential,
        adapter=None,
        *,
        reference_loader=None,
        audio_reference_loader=None,
    ):
        adapter = adapter or select_adapter(snapshot)
        url, headers, body = build_submission(snapshot, request_data, adapter)
        if adapter in {"ark_video.v1", "dashscope_video.v1"} and reference_loader is not None:
            deadline = time.monotonic() + float(snapshot.get("budget_seconds", 1800))
            frame_urls = []
            for index in range(
                sum(
                    bool(request_data["input"].get(f"{name}_frame_media_id"))
                    for name in ("first", "last")
                )
            ):
                data = reference_loader(index, 10 * 1024**2, deadline)
                if len(data) > 10 * 1024**2:
                    raise GenerationError("reference_images_too_large")
                try:
                    with Image.open(BytesIO(data)) as frame:
                        mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                            frame.format
                        )
                        frame.verify()
                    if mime is None:
                        raise ValueError
                except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                    raise GenerationError("invalid_reference_image") from None
                frame_urls.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
            if adapter == "ark_video.v1":
                for item, url_value in zip(body["content"][1:], frame_urls, strict=True):
                    item["image_url"]["url"] = url_value
            else:
                keys = [
                    key
                    for key in ("img_url", "first_frame_url", "last_frame_url")
                    if key in body["input"]
                ]
                for key, url_value in zip(keys, frame_urls, strict=True):
                    body["input"][key] = url_value
        if snapshot.get("service_type") == "text" and body.get("stream"):
            headers["Accept"] = "text/event-stream, application/json"
        secret = _credential(credential)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        body_response = self._json_request(
            "POST",
            url,
            snapshot,
            headers,
            body,
            multipart=adapter == "openai_images.v1" and bool(body.get("image")),
            modelhub_upload=adapter == "modelhub_video.v1"
            and bool(body.get("image_file_1") or body.get("audio_file_1")),
            reference_loader=reference_loader,
            audio_reference_loader=audio_reference_loader,
            binary_audio=adapter == "openai_speech.v1",
        )
        result = (
            GenerationResult(
                status="succeeded",
                adapter=adapter,
                outputs=[{"base64": base64.b64encode(body_response).decode("ascii")}],
            )
            if adapter == "openai_speech.v1"
            else parse_result(body_response, adapter, submitted=True)
        )
        result.resolved_parameters = resolved_parameters(body)
        if adapter != "dashscope_voice_design.v1" and snapshot.get("service_type") == "audio":
            result.usage = {**result.usage, "input_characters": len(request_data["input"]["text"])}
        return self._sanitize(result, secret, submitted=True)

    def validate(self, snapshot, request_data, adapter=None):
        return validate_request(snapshot, request_data, adapter)["resolved_parameters"]

    def poll(self, snapshot, provider_task_id, credential, adapter):
        if ADAPTER_TYPES.get(adapter) != snapshot.get("service_type"):
            raise GenerationError("unsupported_protocol")
        secret = _credential(credential)
        url = poll_endpoint(snapshot, provider_task_id, adapter)
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        body = self._json_request("GET", url, snapshot, headers, None)
        result = parse_result(body, adapter, submitted=False, task_id=provider_task_id)
        return self._sanitize(result, secret, submitted=False)

    def _image_edit_body(
        self, body, deadline, reference_loader=None, *, modelhub=False, audio_reference_loader=None
    ):
        image_keys = (
            [key for key in body if key.startswith("image_file_")] if modelhub else ["image"]
        )
        refs = [body[key] for key in image_keys] if modelhub else body["image"]
        audio_keys = [key for key in body if key.startswith("audio_file_")] if modelhub else []
        fields = [
            (key, str(value).lower() if isinstance(value, bool) else str(value))
            for key, value in body.items()
            if key not in image_keys + audio_keys
        ]
        # Bound aggregate memory as well as each file; never silently omit a ref.
        remaining = MAX_REFERENCE_TOTAL_BYTES
        formats = {
            "PNG": ("png", "image/png"),
            "JPEG": ("jpg", "image/jpeg"),
            "WEBP": ("webp", "image/webp"),
        }
        for index, url in enumerate(refs):
            if remaining <= 0:
                raise GenerationError("reference_images_too_large")
            limit = min(10 * 1024**2 if modelhub else MAX_REFERENCE_IMAGE_BYTES, remaining)
            if reference_loader is None:
                data, _ = self.transport.download_media(url, limit, deadline=deadline)
            else:
                # Runtime-only callback, bound to persisted media IDs. Never accepted
                # from request JSON and never used for arbitrary external URLs.
                data = reference_loader(index, limit, deadline)
            if len(data) > limit:
                raise GenerationError("reference_images_too_large")
            try:
                with Image.open(BytesIO(data)) as image:
                    file_format = formats.get(image.format)
                    image.verify()
                if file_format is None:
                    raise ValueError
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                raise GenerationError("invalid_reference_image") from None
            extension, mime = file_format
            field_name = image_keys[index] if modelhub else "image[]"
            fields.append((field_name, (f"reference-{index + 1}.{extension}", data, mime)))
            remaining -= len(data)
        for index, key in enumerate(audio_keys):
            if audio_reference_loader is None:
                raise GenerationError("unresolved_audio_reference")
            data = audio_reference_loader(index, 10 * 1024**2, deadline)
            if len(data) > 10 * 1024**2 or len(data) > remaining:
                raise GenerationError("reference_audio_too_large")
            # Only worker-bound persisted audio can enter this path; decode before submission.
            from pathlib import Path
            from tempfile import TemporaryDirectory

            from short_drama.service.audio_media import inspect_audio

            with TemporaryDirectory() as temp:
                path = Path(temp) / "reference.wav"
                path.write_bytes(data)
                try:
                    meta = inspect_audio(path, self.settings)
                except (ValueError, RuntimeError):
                    raise GenerationError("invalid_audio_reference") from None
            if not 3000 <= meta["duration_ms"] <= 7500:
                raise GenerationError("invalid_audio_reference")
            fields.append((key, (f"voice-{index + 1}.{meta['ext']}", data, meta["mime"])))
            remaining -= len(data)
        return MultipartBody(fields)

    def _json_request(
        self,
        method,
        url,
        snapshot,
        headers,
        body,
        *,
        multipart=False,
        modelhub_upload=False,
        reference_loader=None,
        binary_audio=False,
        audio_reference_loader=None,
    ):
        default_budget = {"text": 3600, "image": 300, "video": 1800}.get(
            snapshot.get("service_type"), 3600
        )
        try:
            budget = float(snapshot.get("budget_seconds", default_budget))
            if not 0 < budget <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            raise GenerationError("invalid_budget") from None
        # Each poll is bounded separately; the runtime owns the overall task deadline.
        if method == "GET":
            budget = min(budget, 30)
        deadline = time.monotonic() + budget
        request_body = (
            self._image_edit_body(
                body,
                deadline,
                reference_loader,
                modelhub=modelhub_upload,
                audio_reference_loader=audio_reference_loader,
            )
            if multipart or modelhub_upload
            else body
        )
        max_bytes = getattr(self.settings, "generation_max_response_bytes", 8 * 1024**2)
        if snapshot.get("service_type") == "audio":
            max_bytes = 100 * 1024**2
        if snapshot.get("service_type") == "image":
            image_limit = getattr(self.settings, "generation_max_image_bytes", 50 * 1024**2)
            max_bytes = max(max_bytes, 4 * ((image_limit + 2) // 3 * 4) + 65536)
        status, response_headers, data = self.transport.request(
            method,
            url,
            headers=headers,
            body=request_body,
            max_bytes=max_bytes,
            deadline=deadline,
        )
        submitting = method == "POST"
        if status in (404, 405):
            # A generic 404 can be a model/business error, not proof of a wrong protocol.
            # Do not authorize a second paid POST from status alone.
            raise GenerationError("provider_endpoint")
        if status in (401, 403):
            raise GenerationError("provider_auth")
        if status == 429:
            raise GenerationError("provider_rate_limit", retryable=True)
        if 300 <= status < 400:
            raise GenerationError("provider_redirect")
        if status >= 500:
            raise GenerationError(
                "upstream_unavailable",
                accepted_unknown=submitting,
                retryable=not submitting,
                http_status=status,
            )
        if status < 200 or status >= 300:
            raise GenerationError("provider_rejected")
        content_type = next(
            (value for key, value in response_headers.items() if key.lower() == "content-type"), ""
        )
        if (
            submitting
            and snapshot.get("service_type") == "text"
            and "text/event-stream" in content_type.lower()
        ):
            return decode_text_stream(data, responses="input" in body)
        if submitting and binary_audio:
            return data
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise GenerationError(
                "invalid_response", accepted_unknown=submitting, retryable=not submitting
            ) from None

    def _sanitize(self, result, secret, *, submitted):
        # Identifiers/URLs containing a credential cannot safely be persisted or followed.
        if secret and result.provider_task_id and secret in result.provider_task_id:
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        for output in result.outputs:
            if secret and secret in output.get("url", ""):
                raise GenerationError("invalid_response", accepted_unknown=submitted)
        result.text = _safe_value(result.text, secret)
        result.usage = _safe_value(result.usage, secret)
        result.finish_reason = _safe_value(result.finish_reason, secret)
        result.resolved_parameters = _safe_value(result.resolved_parameters, secret)
        if secret and secret in str(result.voice):
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        return result

    def download_media(self, url, max_bytes):
        return self.transport.download_media(url, max_bytes)
