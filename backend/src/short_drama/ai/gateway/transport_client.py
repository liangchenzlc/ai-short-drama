import json
import time
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image

from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO
from short_drama.ai.adapters.image.agnes_adapters import (
    agnes_queue_rejected,
)
from short_drama.ai.adapters.image.image_references import (
    CANVAS_REFERENCE_IMAGE_BYTES,
    saved_mask_bytes,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
    RUNNINGHUB_VIDEO,
)
from short_drama.ai.adapters.text.chat_protocols import is_compatible_chat
from short_drama.ai.adapters.text.claude_stream import (
    ClaudeTextStreamObserver,
    decode_claude_text_stream,
)
from short_drama.ai.adapters.tools.adapters import (
    is_canvas_tool_choice_rejection,
)
from short_drama.ai.adapters.tools.stream import decode_canvas_tool_stream
from short_drama.ai.adapters.video.video_adapters import (
    OPENAI_VIDEOS,
    VIDEO_ADAPTERS,
)
from short_drama.ai.gateway.base import GatewayBase
from short_drama.ai.gateway.streaming import TextStreamObserver, decode_text_stream
from short_drama.ai.gateway.support import (
    MAX_REFERENCE_IMAGE_BYTES,
    MAX_REFERENCE_TOTAL_BYTES,
    _authentication,
    _safe_value,
)
from short_drama.ai.gateway.transport import MultipartBody, response_retry_after, validated_url
from short_drama.ai.types import GenerationError


class GatewayTransport(GatewayBase):
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

            from short_drama.service.media.processing.audio import inspect_audio

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
        agnes_video=False,
        claude_text=False,
        canvas_tool_adapter=None,
        request_deadline=None,
        paid_submission=None,
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
        if method == "GET" or paid_submission is False and not isinstance(body, MultipartBody):
            budget = min(budget, 30)
        deadline = time.monotonic() + budget
        if request_deadline is not None:
            deadline = min(deadline, request_deadline)
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
                if claude_text:
                    observer = ClaudeTextStreamObserver(on_text_delta, secrets=secrets)
                    return
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
        submitting = method == "POST" if paid_submission is None else paid_submission
        try:
            status, response_headers, data = self.transport.request(
                method,
                url,
                headers=headers,
                body=request_body,
                max_bytes=max_bytes,
                deadline=deadline,
                read_error_body=(canvas_video and not submitting)
                or (canvas_tool_adapter == "openai_chat.v1" and method == "POST"),
                **stream_options,
                **({"read_server_error_body": True} if agnes_video and submitting else {}),
                **({"query": True} if agnes_video and method == "GET" else {}),
            )
        except GenerationError as error:
            if paid_submission is False:
                raise GenerationError(
                    error.code,
                    retryable=error.retryable or error.accepted_unknown,
                    http_status=error.http_status,
                    retry_after=error.retry_after,
                ) from None
            raise
        if (
            canvas_tool_adapter == "openai_chat.v1"
            and submitting
            and is_canvas_tool_choice_rejection(status, data)
        ):
            raise GenerationError("canvas_tool_choice_rejected")
        if canvas_video and not 200 <= status < 300:
            retry_after = response_retry_after(response_headers)
            if agnes_video and submitting and agnes_queue_rejected(status, data):
                raise GenerationError(
                    "provider_queue_full",
                    http_status=status,
                    retryable=True,
                    retry_after=retry_after,
                )
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
            if canvas_tool_adapter is not None:
                return decode_canvas_tool_stream(data, canvas_tool_adapter)
            if claude_text:
                return decode_claude_text_stream(data)
            return decode_text_stream(
                data, responses="input" in body, include_reasoning=is_compatible_chat(snapshot)
            )
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
        result.reasoning = _safe_value(result.reasoning, secret)
        result.usage = _safe_value(result.usage, secret)
        result.finish_reason = _safe_value(result.finish_reason, secret)
        result.resolved_parameters = _safe_value(result.resolved_parameters, secret)
        result.error = _safe_value(result.error, secret)
        for call in result.tool_calls:
            function = call["function"]
            if secret and (secret in call["id"] or secret in function["name"]):
                raise GenerationError("invalid_response", accepted_unknown=submitted)
            arguments = json.loads(function["arguments"])
            function["arguments"] = json.dumps(_safe_value(arguments, secret), ensure_ascii=False)
            if call.get("thoughtSignature"):
                call["thoughtSignature"] = _safe_value(call["thoughtSignature"], secret)
        if secret and secret in str(result.voice):
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        return result

    def download_media(self, url, max_bytes, *, snapshot=None, credential=None):
        if snapshot is None and credential is None:
            return self.transport.download_media(url, max_bytes)
        adapter = ((snapshot or {}).get("capability_cache") or {}).get("adapter")
        if adapter in RUNNINGHUB_ADAPTERS:
            # 固定源的产物下载从不附积分、上传 Key 或自定义鉴权头，即使同源。
            return self.transport.download_media(
                url, max_bytes, canvas_video=adapter == RUNNINGHUB_VIDEO
            )
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
        if adapter == OPENAI_VIDEOS and target.path.endswith("/content"):
            headers["Accept"] = "video/mp4"
        if adapter == ASYNC_AUDIO and target.path.endswith("/content"):
            headers["Accept"] = "audio/*"
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
