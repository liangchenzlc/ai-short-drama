"""Synchronous calls only; durable scheduling and recording belong to the runtime."""

import base64
import json
import mimetypes
import re
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
from .canvas_agnes_adapters import (
    agnes_has_stored_references,
    agnes_queue_rejected,
    agnes_reference_guard,
    agnes_reference_url,
    check_agnes_reference_echo,
    is_agnes_video,
    parse_agnes_video_result,
    validate_agnes_reference_guard,
)
from .canvas_audio_adapters import ASYNC_AUDIO, complete_audio_urls
from .canvas_claude_adapters import CLAUDE_MESSAGES
from .canvas_claude_stream import ClaudeTextStreamObserver, decode_claude_text_stream
from .canvas_credentials import CanvasCredentials, canvas_authentication
from .canvas_gemini_adapters import GEMINI_GENERATE_CONTENT
from .canvas_image_references import (
    CANVAS_REFERENCE_IMAGE_BYTES,
    inline_saved_image_references,
    is_canvas_image_request,
    saved_mask_bytes,
    uses_canvas_inline_images,
)
from .canvas_runninghub_adapters import (
    RUNNINGHUB_ADAPTERS,
    RUNNINGHUB_VIDEO,
    parse_runninghub_result,
    payload_code,
    runninghub_descriptor,
    runninghub_root,
    runninghub_submission,
    uploaded_filename,
    validate_task_id,
    workflow_prompt,
)
from .canvas_runninghub_fields import (
    collect_fields,
    fields_for_mode,
    freeze_random_fields,
    validate_slots,
)
from .canvas_runninghub_workflow import RUNNINGHUB_WORKFLOW
from .canvas_seedance_preupload import (
    JSON_LIMIT,
    inline_canvas_references,
    prepare_beefapi_seedance_references,
    read_canvas_reference,
    validate_beefapi_snapshot,
)
from .canvas_tool_adapters import (
    is_canvas_tool_choice_rejection,
    parse_canvas_tool_result,
    validate_canvas_tool_reply,
)
from .canvas_tool_stream import decode_canvas_tool_stream
from .canvas_tool_submission import canvas_tool_resolved_parameters, is_canvas_tool_request
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


def _authentication(snapshot, credential, headers, *, adapter=None):
    if isinstance(credential, CanvasCredentials):
        return canvas_authentication(snapshot, credential, headers, adapter=adapter)
    if adapter in {CLAUDE_MESSAGES, GEMINI_GENERATE_CONTENT}:
        raise GenerationError("invalid_credential")
    if "canvas_auth_version" in snapshot or "model_auth_version" in snapshot:
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
        canvas_reference_url_resolver=None,
        agnes_reference_guard_writer=None,
    ):
        adapter = adapter or select_adapter(snapshot)
        if adapter in RUNNINGHUB_ADAPTERS:
            return self._submit_runninghub(
                snapshot,
                request_data,
                credential,
                adapter,
                canvas_reference_loader,
                mask_reference_loader,
            )
        if is_canvas_tool_request(request_data):
            return self._submit_canvas_tool(
                snapshot, request_data, credential, adapter, on_text_delta
            )
        if adapter in VIDEO_ADAPTERS:
            return self._submit_canvas_video(
                snapshot,
                request_data,
                credential,
                adapter,
                canvas_reference_loader,
                canvas_reference_url_resolver,
                agnes_reference_guard_writer,
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
        secret, headers, secrets = _authentication(snapshot, credential, headers, adapter=adapter)
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
            on_text_delta=None if adapter == GEMINI_GENERATE_CONTENT else on_text_delta,
            secrets=secrets,
            claude_text=adapter == CLAUDE_MESSAGES,
        )
        result = (
            GenerationResult(
                status="succeeded",
                adapter=adapter,
                outputs=[{"base64": base64.b64encode(body_response).decode("ascii")}],
            )
            if adapter == "openai_speech.v1"
            else body_response
            if isinstance(body_response, GenerationResult)
            else parse_result(body_response, adapter, submitted=True)
        )
        result.resolved_parameters = resolved_parameters(body)
        if adapter == ASYNC_AUDIO:
            result = complete_audio_urls(snapshot, result)
        if adapter != "dashscope_voice_design.v1" and snapshot.get("service_type") == "audio":
            result.usage = {**result.usage, "input_characters": len(request_data["input"]["text"])}
        for value in secrets:
            result = self._sanitize(result, value, submitted=True)
        return result

    def validate(self, snapshot, request_data, adapter=None):
        return validate_request(snapshot, request_data, adapter)["resolved_parameters"]

    def _submit_runninghub(self, snapshot, request, credential, adapter, loader, mask_loader):
        descriptor = runninghub_descriptor(snapshot, request, adapter)
        key, headers, secrets = _authentication(snapshot, credential, {}, adapter=adapter)
        budget = float(snapshot.get("budget_seconds", 300))
        if not 0 < budget <= 86400:
            raise GenerationError("invalid_budget")
        deadline = time.monotonic() + budget
        root = runninghub_root(snapshot["base_url"])
        if descriptor.kind == "workflow" and not descriptor.workflow_json:
            try:
                raw = self._json_request(
                    "POST",
                    root + "/api/openapi/getJsonApiFormat",
                    snapshot,
                    headers,
                    {"apiKey": key, "workflowId": descriptor.workflow_id or snapshot["model_key"]},
                    request_deadline=deadline,
                    paid_submission=False,
                )
                if payload_code(raw) not in {None, 0}:
                    raise GenerationError("invalid_workflow_schema")
                descriptor.workflow_json = workflow_prompt(raw, required=True)
            except GenerationError as error:
                # 固定源允许使用已保存映射继续；不吞未知程序错误或收费请求错误。
                if error.code not in {
                    "provider_auth",
                    "provider_endpoint",
                    "provider_rejected",
                    "upstream_unavailable",
                    "invalid_response",
                    "invalid_workflow_schema",
                    "connection_error",
                    "transport_error",
                    "dns_error",
                    "timeout",
                }:
                    raise
        if not descriptor.fields and descriptor.workflow_json:
            descriptor.fields = freeze_random_fields(
                collect_fields(descriptor.workflow_json, snapshot["service_type"])
            )
        descriptor.fields = fields_for_mode(descriptor.fields, snapshot["service_type"])
        inputs = request["canvas_request"]["input"]
        validate_slots(descriptor.fields, inputs)
        files = {}
        references = []
        for kind, group in (
            ("image", "referenceImages"),
            ("video", "referenceVideos"),
            ("audio", "referenceAudios"),
        ):
            references.extend(
                (kind, index, item, False) for index, item in enumerate(inputs.get(group, []))
            )
        if inputs.get("mask"):
            references.append(("image", 0, inputs["mask"], True))
        upload_key = (
            credential.runninghub_upload_key.get_secret_value()
            if isinstance(credential, CanvasCredentials) and credential.runninghub_upload_key
            else ""
        )
        if references and not upload_key:
            raise GenerationError("workflow_upload_key_missing")
        for kind, index, item, is_mask in references:
            limit = {"image": 50 * 1024**2, "video": 1024**3, "audio": 100 * 1024**2}[kind]
            if time.monotonic() >= deadline:
                raise GenerationError("workflow_upload_timeout")
            mime = item.get("type") or "application/octet-stream"
            if item.get("storageKey"):
                if is_mask:
                    if mask_loader is None:
                        raise GenerationError("unresolved_media_reference")
                    data = mask_loader(0, limit, deadline)
                else:
                    if loader is None:
                        raise GenerationError("unresolved_media_reference")
                    data = loader(kind, index, limit, deadline)
            else:
                data, mime = self.transport.download_media(
                    item.get("url", ""), limit, deadline=deadline
                )
            if not data or len(data) > limit:
                raise GenerationError("workflow_reference_invalid")
            identity = item.get("id") or item.get("storageKey") or item.get("url") or "reference"
            filename = (
                "reference-"
                + (re.sub(r"[^A-Za-z0-9_-]", "", identity)[:64] or "reference")
                + (mimetypes.guess_extension(mime, strict=False) or ".bin")
            )
            body = MultipartBody(
                [("apiKey", upload_key), ("fileType", "input"), ("file", (filename, data, mime))]
            )
            try:
                raw = self._json_request(
                    "POST",
                    root + "/task/openapi/upload",
                    snapshot,
                    headers,
                    body,
                    request_deadline=deadline,
                    paid_submission=False,
                )
            except GenerationError as error:
                if error.code == "provider_auth":
                    raise GenerationError(
                        "workflow_upload_auth", http_status=error.http_status
                    ) from None
                raise
            name = uploaded_filename(raw)
            if any(secret and secret in name for secret in secrets):
                raise GenerationError("invalid_workflow_upload")
            files[identity] = name
        prepared = {
            **request,
            "canvas_runninghub": descriptor.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
        url, _, body = runninghub_submission(snapshot, prepared, adapter, files=files)
        resolved = deepcopy(body)
        body["apiKey"] = key
        raw = self._json_request(
            "POST",
            url,
            snapshot,
            headers,
            body,
            request_deadline=deadline,
            canvas_video=adapter == RUNNINGHUB_VIDEO,
        )
        result = parse_runninghub_result(raw, adapter, submitted=True, root=root)
        result.resolved_parameters = resolved
        for secret in secrets:
            result = self._sanitize(result, secret, submitted=True)
        return result

    def _poll_runninghub(self, snapshot, task_id, credential, adapter):
        validate_task_id(task_id)
        key, headers, secrets = _authentication(snapshot, credential, {}, adapter=adapter)
        root = runninghub_root(snapshot["base_url"])
        raw = self._json_request(
            "POST",
            root + "/task/openapi/outputs",
            snapshot,
            headers,
            {"apiKey": key, "taskId": task_id},
            paid_submission=False,
            canvas_video=adapter == RUNNINGHUB_VIDEO,
        )
        result = parse_runninghub_result(raw, adapter, submitted=False, task_id=task_id, root=root)
        for secret in secrets:
            result = self._sanitize(result, secret, submitted=False)
        return result

    def _submit_canvas_tool(self, snapshot, request, credential, adapter, on_text_delta):
        url, headers, body = build_submission(snapshot, request, adapter)
        if not isinstance(credential, CanvasCredentials):
            raise GenerationError("invalid_credential")
        if body.get("stream"):
            headers["Accept"] = "text/event-stream, application/json"
        _, headers, secrets = _authentication(snapshot, credential, headers, adapter=adapter)
        try:
            budget = float(snapshot.get("budget_seconds", 3600))
            if not 0 < budget <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            raise GenerationError("invalid_budget") from None
        deadline = time.monotonic() + budget
        attempts = [body]
        if adapter == "openai_chat.v1":
            if body.get("tool_choice") != "auto":
                attempts.append({**body, "tool_choice": "auto"})
            attempts.append({key: value for key, value in body.items() if key != "tool_choice"})
        for index, attempted_body in enumerate(attempts):
            try:
                response = self._json_request(
                    "POST",
                    url,
                    snapshot,
                    headers,
                    attempted_body,
                    on_text_delta=None if adapter == GEMINI_GENERATE_CONTENT else on_text_delta,
                    secrets=secrets,
                    claude_text=adapter == CLAUDE_MESSAGES,
                    canvas_tool_adapter=adapter,
                    request_deadline=deadline,
                )
                body = attempted_body
                break
            except GenerationError as error:
                if error.code != "canvas_tool_choice_rejected":
                    raise
                if index == len(attempts) - 1:
                    raise GenerationError("provider_rejected") from None
        reply = parse_canvas_tool_result(
            response, adapter, request["canvas_tool_canonical"], submitted=True
        )
        result = GenerationResult(
            "succeeded",
            adapter,
            text=reply.content,
            reasoning=reply.reasoning,
            tool_calls=[
                call.model_dump(by_alias=True, exclude_none=True) for call in reply.tool_calls
            ],
            usage=response.get("usage") or response.get("usageMetadata") or {},
            finish_reason="stop",
            resolved_parameters=canvas_tool_resolved_parameters(body),
        )
        for secret in secrets:
            result = self._sanitize(result, secret, submitted=True)
        validate_canvas_tool_reply(
            {"content": result.text, "reasoning": result.reasoning, "toolCalls": result.tool_calls},
            request["canvas_tool_canonical"],
            submitted=True,
        )
        return result

    def _submit_canvas_video(
        self, snapshot, request, credential, adapter, loader, url_resolver, guard_writer
    ):
        # Admission stays pure. Only this explicitly frozen canvas branch reads media.
        agnes_video = is_agnes_video(snapshot, adapter)
        if agnes_video:
            validate_request(snapshot, request, adapter)
            if agnes_has_stored_references(request) and not callable(guard_writer):
                raise GenerationError("reference_guard_unavailable")
        else:
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
        reference_urls = []
        guard = None
        if agnes_video:
            refs = (snapshot.get("canvas_video_capability") or {}).get("references") or {}
            for kind, group, limit_name, default_limit in (
                ("image", "referenceImages", "maxImageBytes", 50 * 1024**2),
                ("audio", "referenceAudios", "maxAudioBytes", 15 * 1024**2),
            ):
                maximum = refs.get(limit_name) or default_limit
                for index, item in enumerate(prepared["canvas_request"]["input"].get(group, [])):
                    if item.get("storageKey"):
                        if url_resolver is None:
                            raise GenerationError("reference_media_requires_url")
                        url = agnes_reference_url(url_resolver(kind, index, maximum, deadline))
                        reference_urls.append(url)
                        item.pop("storageKey")
                        item["url"] = url
        elif adapter == BEEFAPI_SEEDANCE:
            prepare_beefapi_seedance_references(
                self.transport, snapshot, prepared, headers, loader, deadline
            )
        elif adapter == RUNNINGHUB_WORKFLOW:
            inline_canvas_references(self.transport, prepared, loader, deadline)
        elif (
            adapter == NEWAPI_VIDEO_GENERATIONS
            and snapshot.get("canvas_channel_key") == "beefapi"
            and snapshot.get("model_key", "").lower() == "wan3.0-video"
        ):
            validate_beefapi_snapshot(snapshot, self.settings)
            inline_canvas_references(self.transport, prepared, loader, deadline)
        url, _, body = build_submission(snapshot, prepared, adapter)
        if reference_urls:
            guard = agnes_reference_guard(reference_urls)
            # The callback commits its short transaction before any provider request.
            guard_writer(deepcopy(guard))
        resolved = canvas_video_resolved_parameters(body)
        if adapter == OPENAI_VIDEOS and not agnes_video:
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
        if adapter in {BEEFAPI_SEEDANCE, RUNNINGHUB_WORKFLOW}:
            if len(json.dumps(body, ensure_ascii=False).encode()) > JSON_LIMIT:
                raise GenerationError("reference_inline_too_large")
        if adapter == BEEFAPI_SEEDANCE:
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
            agnes_video=agnes_video,
        )
        if guard is not None:
            check_agnes_reference_echo(raw, guard, submitted=True, secrets=secrets)
        try:
            result = complete_video_urls(
                snapshot,
                (
                    parse_agnes_video_result(raw, adapter, submitted=True)
                    if agnes_video
                    else parse_result(raw, adapter, submitted=True)
                ),
            )
        except GenerationError as error:
            raise GenerationError(error.code, accepted_unknown=True) from None
        result.resolved_parameters = resolved
        for value in secrets:
            result = self._sanitize(result, value, submitted=True)
        if guard is not None:
            check_agnes_reference_echo(result, guard, submitted=True, secrets=secrets)
        return result

    def poll(
        self,
        snapshot,
        provider_task_id,
        credential,
        adapter,
        *,
        agnes_reference_guard=None,
        require_agnes_reference_guard=False,
    ):
        if ADAPTER_TYPES.get(adapter) != snapshot.get("service_type"):
            raise GenerationError("unsupported_protocol")
        guard = None
        if is_agnes_video(snapshot, adapter) and (
            require_agnes_reference_guard or agnes_reference_guard is not None
        ):
            guard = validate_agnes_reference_guard(agnes_reference_guard)
            check_agnes_reference_echo({"video_id": provider_task_id}, guard, submitted=False)
        if adapter in RUNNINGHUB_ADAPTERS:
            return self._poll_runninghub(snapshot, provider_task_id, credential, adapter)
        if adapter in VIDEO_ADAPTERS:
            if not isinstance(credential, CanvasCredentials):
                raise GenerationError("invalid_credential")
            if adapter == BEEFAPI_SEEDANCE:
                validate_beefapi_snapshot(snapshot, self.settings)
        url = poll_endpoint(snapshot, provider_task_id, adapter)
        _, headers, secrets = _authentication(snapshot, credential, {})
        body = self._json_request(
            "GET",
            url,
            snapshot,
            headers,
            None,
            canvas_video=adapter in VIDEO_ADAPTERS,
            agnes_video=is_agnes_video(snapshot, adapter),
        )
        if guard is not None:
            check_agnes_reference_echo(body, guard, submitted=False, secrets=secrets)
        result = (
            parse_agnes_video_result(body, adapter, submitted=False, task_id=provider_task_id)
            if is_agnes_video(snapshot, adapter)
            else parse_result(body, adapter, submitted=False, task_id=provider_task_id)
        )
        if adapter == ASYNC_AUDIO:
            result = complete_audio_urls(snapshot, result)
        if adapter in VIDEO_ADAPTERS:
            result = complete_video_urls(snapshot, result)
        for value in secrets:
            result = self._sanitize(result, value, submitted=False)
        if guard is not None:
            check_agnes_reference_echo(result, guard, submitted=False, secrets=secrets)
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
        if adapter in {OPENAI_VIDEOS, BEEFAPI_SEEDANCE} and target.path.endswith("/content"):
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
