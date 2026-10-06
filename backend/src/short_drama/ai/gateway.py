"""Synchronous calls only; durable scheduling and recording belong to the runtime."""

import base64
import json
import time
from copy import deepcopy
from io import BytesIO
from urllib.parse import urlsplit

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
from .canvas_credentials import CanvasCredentials, canvas_authentication
from .canvas_image_references import (
    CANVAS_REFERENCE_IMAGE_BYTES,
    inline_saved_image_references,
    is_canvas_image_request,
    saved_mask_bytes,
    uses_canvas_inline_images,
)
from .canvas_seedance_preupload import (
    JSON_LIMIT,
    inline_canvas_references,
    prepare_beefapi_seedance_references,
    read_canvas_reference,
    validate_beefapi_snapshot,
)
from .canvas_video_adapters import (
    BEEFAPI_SEEDANCE,
    NEWAPI_VIDEO_GENERATIONS,
    OPENAI_VIDEOS,
    VIDEO_ADAPTERS,
    canvas_video_resolved_parameters,
    complete_video_urls,
)
from .streaming import TextStreamObserver, decode_text_stream
from .transport import MultipartBody, SafeTransport, response_retry_after, validated_url
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


def _authentication(snapshot, credential, headers):
    if isinstance(credential, CanvasCredentials):
        return canvas_authentication(snapshot, credential, headers)
    if "canvas_auth_version" in snapshot:
        raise GenerationError("invalid_credential")
    secret = _credential(credential)
    headers = dict(headers)
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return secret, headers, [secret] if secret else []


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
        mask_reference_loader=None,
        audio_reference_loader=None,
        on_text_delta=None,
        canvas_reference_loader=None,
    ):
        adapter = adapter or select_adapter(snapshot)
        if adapter in VIDEO_ADAPTERS:
            return self._submit_canvas_video(
                snapshot, request_data, credential, adapter, canvas_reference_loader
            )
        url, headers, body = build_submission(snapshot, request_data, adapter)
        canvas_image = adapter == "openai_images.v1" and is_canvas_image_request(
            snapshot, request_data
        )
        if (
            canvas_image
            and body.get("image")
            and (
                reference_loader is None
                or len(request_data.get("input", {}).get("reference_media_ids", []))
                != len(body["image"])
            )
        ):
            raise GenerationError("unresolved_media_reference")
        if uses_canvas_inline_images(snapshot, request_data, adapter):
            deadline = time.monotonic() + float(snapshot.get("budget_seconds", 180))
            inline_saved_image_references(body, request_data, adapter, reference_loader, deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GenerationError("timeout")
            snapshot = {**snapshot, "budget_seconds": remaining}
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
        secret, headers, secrets = _authentication(snapshot, credential, headers)
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
            mask_reference_loader=mask_reference_loader,
            canvas_image=canvas_image,
            audio_reference_loader=audio_reference_loader,
            binary_audio=adapter == "openai_speech.v1",
            on_text_delta=on_text_delta,
            secrets=secrets,
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
        for value in secrets:
            result = self._sanitize(result, value, submitted=True)
        return result

    def validate(self, snapshot, request_data, adapter=None):
        return validate_request(snapshot, request_data, adapter)["resolved_parameters"]

    def _submit_canvas_video(self, snapshot, request, credential, adapter, loader):
        # Admission stays pure. Only this explicitly frozen canvas branch reads media.
        build_submission(snapshot, request, adapter)
        if not isinstance(credential, CanvasCredentials):
            raise GenerationError("invalid_credential")
        _, headers, secrets = _authentication(snapshot, credential, {})
        try:
            budget = float(snapshot.get("budget_seconds", 1800))
            if not 0 < budget <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            raise GenerationError("invalid_budget") from None
        deadline = time.monotonic() + budget
        prepared = deepcopy(request)
        if adapter == BEEFAPI_SEEDANCE:
            prepare_beefapi_seedance_references(
                self.transport, snapshot, prepared, headers, loader, deadline
            )
        elif (
            adapter == NEWAPI_VIDEO_GENERATIONS
            and snapshot.get("canvas_channel_key") == "beefapi"
            and snapshot.get("model_key", "").lower() == "wan3.0-video"
        ):
            validate_beefapi_snapshot(snapshot, self.settings)
            inline_canvas_references(self.transport, prepared, loader, deadline)
        url, _, body = build_submission(snapshot, prepared, adapter)
        resolved = canvas_video_resolved_parameters(body)
        if adapter == OPENAI_VIDEOS:
            reference = body.pop("input_reference", None)
            fields = [
                (name, str(value).lower() if isinstance(value, bool) else str(value))
                for name, value in body.items()
            ]
            if reference:
                data, mime = read_canvas_reference(
                    self.transport, "image", 0, reference, loader, deadline
                )
                fields.append(("input_reference", ("input-reference.png", data, mime)))
            body = MultipartBody(fields)
        if adapter == BEEFAPI_SEEDANCE:
            if len(json.dumps(body, ensure_ascii=False).encode()) > JSON_LIMIT:
                raise GenerationError("reference_inline_too_large")
            key = snapshot.get("submission_key")
            if key:
                if (
                    not isinstance(key, str)
                    or len(key) > 128
                    or any(ord(c) < 33 or ord(c) > 126 for c in key)
                ):
                    raise GenerationError("invalid_submission_key")
                headers["Idempotency-Key"] = key
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GenerationError("timeout")
        raw = self._json_request(
            "POST",
            url,
            {**snapshot, "budget_seconds": remaining},
            headers,
            body,
            secrets=secrets,
            canvas_video=True,
        )
        try:
            result = complete_video_urls(snapshot, parse_result(raw, adapter, submitted=True))
        except GenerationError as error:
            raise GenerationError(error.code, accepted_unknown=True) from None
        result.resolved_parameters = resolved
        for value in secrets:
            result = self._sanitize(result, value, submitted=True)
        return result

    def poll(self, snapshot, provider_task_id, credential, adapter):
        if ADAPTER_TYPES.get(adapter) != snapshot.get("service_type"):
            raise GenerationError("unsupported_protocol")
        if adapter in VIDEO_ADAPTERS:
            if not isinstance(credential, CanvasCredentials):
                raise GenerationError("invalid_credential")
            if adapter == BEEFAPI_SEEDANCE:
                validate_beefapi_snapshot(snapshot, self.settings)
        url = poll_endpoint(snapshot, provider_task_id, adapter)
        _, headers, secrets = _authentication(snapshot, credential, {})
        body = self._json_request(
            "GET", url, snapshot, headers, None, canvas_video=adapter in VIDEO_ADAPTERS
        )
        result = parse_result(body, adapter, submitted=False, task_id=provider_task_id)
        if adapter in VIDEO_ADAPTERS:
            result = complete_video_urls(snapshot, result)
        for value in secrets:
            result = self._sanitize(result, value, submitted=False)
        return result

    def _image_edit_body(
        self,
        body,
        deadline,
        reference_loader=None,
        *,
        modelhub=False,
        audio_reference_loader=None,
        canvas_image=False,
        mask_reference_loader=None,
    ):
        if body.get("mask") and not canvas_image:
            raise GenerationError("unsupported_parameters")
        image_keys = (
            [key for key in body if key.startswith("image_file_")] if modelhub else ["image"]
        )
        refs = [body[key] for key in image_keys] if modelhub else body["image"]
        audio_keys = [key for key in body if key.startswith("audio_file_")] if modelhub else []
        fields = [
            (key, str(value).lower() if isinstance(value, bool) else str(value))
            for key, value in body.items()
            if key not in image_keys + audio_keys + (["mask"] if canvas_image else [])
        ]
        # Bound aggregate memory as well as each file; never silently omit a ref.
        remaining = MAX_REFERENCE_TOTAL_BYTES
        formats = {
            "PNG": ("png", "image/png"),
            "JPEG": ("jpg", "image/jpeg"),
            "WEBP": ("webp", "image/webp"),
        }
        source_size = None
        for index, url in enumerate(refs):
            if remaining <= 0:
                raise GenerationError("reference_images_too_large")
            per_image = (
                CANVAS_REFERENCE_IMAGE_BYTES
                if canvas_image
                else 10 * 1024**2
                if modelhub
                else MAX_REFERENCE_IMAGE_BYTES
            )
            limit = min(per_image, remaining)
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
                    if index == 0:
                        source_size = image.size
                    image.verify()
                if file_format is None:
                    raise ValueError
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                raise GenerationError("invalid_reference_image") from None
            extension, mime = file_format
            field_name = image_keys[index] if modelhub else "image" if canvas_image else "image[]"
            fields.append((field_name, (f"reference-{index + 1}.{extension}", data, mime)))
            remaining -= len(data)
        if body.get("mask"):
            if source_size is None:
                raise GenerationError("unresolved_media_reference")
            data = saved_mask_bytes(mask_reference_loader, deadline, remaining, source_size)
            fields.append(("mask", ("mask.png", data, "image/png")))
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
        on_text_delta=None,
        secrets=None,
        canvas_image=False,
        mask_reference_loader=None,
        canvas_video=False,
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
                canvas_image=canvas_image,
                mask_reference_loader=mask_reference_loader,
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
        observer = None

        def observe_headers(status, response_headers):
            nonlocal observer
            content_type = next(
                (v for k, v in response_headers.items() if k.lower() == "content-type"), ""
            )
            if 200 <= status < 300 and "text/event-stream" in content_type.lower():
                observer = TextStreamObserver(
                    on_text_delta,
                    responses="input" in body,
                    secret=headers.get("Authorization", "").removeprefix("Bearer "),
                    secrets=secrets,
                )

        def observe_chunk(chunk):
            if observer is not None:
                observer.feed(chunk)

        stream_options = (
            {"on_headers": observe_headers, "on_chunk": observe_chunk}
            if on_text_delta is not None and method == "POST" and snapshot["service_type"] == "text"
            else {}
        )
        status, response_headers, data = self.transport.request(
            method,
            url,
            headers=headers,
            body=request_body,
            max_bytes=max_bytes,
            deadline=deadline,
            read_error_body=canvas_video and method == "GET",
            **stream_options,
        )
        submitting = method == "POST"
        if canvas_video and not 200 <= status < 300:
            retry_after = response_retry_after(response_headers)
            retryable = not submitting and (status in {404, 408, 409, 425, 429} or status >= 500)
            code = "provider_rejected"
            if status == 400 and not submitting:
                try:
                    parsed = json.loads(data)
                except (ValueError, UnicodeError):
                    parsed = {}
                candidates = (
                    [parsed.get(key) for key in ("error", "data")] + [parsed]
                    if isinstance(parsed, dict)
                    else []
                )
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if any(
                        isinstance(candidate.get(key), str)
                        and candidate[key].strip().lower()
                        in {"task_not_exist", "task_not_found", "task not exist", "task not found"}
                        for key in ("code", "message", "msg")
                    ):
                        code, retryable = "provider_task_not_ready", True
                        break
            elif status in {401, 403}:
                code = "provider_auth"
            elif status == 429:
                code = "provider_rate_limit"
                retryable = True
            elif status == 404:
                code = "provider_endpoint"
            elif 300 <= status < 400:
                code = "provider_redirect"
            elif status >= 500:
                code = "upstream_unavailable"
            raise GenerationError(
                code,
                http_status=status,
                retryable=retryable,
                accepted_unknown=submitting and status >= 500,
                retry_after=retry_after,
            )
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
            if observer is not None:
                observer.finish()
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
        result.error = _safe_value(result.error, secret)
        if secret and secret in str(result.voice):
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        return result

    def download_media(self, url, max_bytes, *, snapshot=None, credential=None):
        if snapshot is None and credential is None:
            return self.transport.download_media(url, max_bytes)
        adapter = ((snapshot or {}).get("capability_cache") or {}).get("adapter")
        canvas_video = adapter in VIDEO_ADAPTERS
        _, headers, _ = _authentication(snapshot or {}, credential, {})
        target = urlsplit(validated_url(url, query=True))
        base = urlsplit(validated_url((snapshot or {}).get("base_url")))

        def origin(parts):
            return (
                parts.scheme,
                parts.hostname,
                parts.port or (443 if parts.scheme == "https" else 80),
            )

        if origin(target) != origin(base):
            # CDN or signed external artifacts never inherit provider credentials.
            return self.transport.download_media(url, max_bytes, canvas_video=canvas_video)
        if adapter in {OPENAI_VIDEOS, BEEFAPI_SEEDANCE} and target.path.endswith("/content"):
            headers["Accept"] = "video/mp4"
        status, response_headers, data = self.transport.request(
            "GET",
            url,
            headers=headers,
            max_bytes=max_bytes,
            deadline=time.monotonic() + 180,
            query=True,
        )
        if 300 <= status < 400:
            raise GenerationError("provider_redirect")
        if status != 200:
            raise GenerationError(
                "download_failed",
                retryable=status in {404, 408, 409, 425, 429} or status >= 500
                if canvas_video
                else status == 429 or status >= 500,
                http_status=status if canvas_video else None,
                retry_after=response_retry_after(response_headers) if canvas_video else None,
            )
        mime = next(
            (value for key, value in response_headers.items() if key.lower() == "content-type"),
            "application/octet-stream",
        )
        return data, mime.split(";", 1)[0].strip().lower()
