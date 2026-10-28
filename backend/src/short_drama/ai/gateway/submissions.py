import base64
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO

from PIL import Image

from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO, complete_audio_urls
from short_drama.ai.adapters.capabilities import resolved_parameters, validate_request
from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.adapters.image.image_references import (
    inline_saved_image_references,
    uses_canvas_inline_images,
)
from short_drama.ai.adapters.results import parse_result
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
)
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.adapters.text.chat_protocols import (
    is_compatible_chat,
    parse_compatible_chat_result,
)
from short_drama.ai.adapters.text.claude_adapters import CLAUDE_MESSAGES
from short_drama.ai.adapters.text.gemini_adapters import GEMINI_GENERATE_CONTENT
from short_drama.ai.adapters.tools.submission import (
    canvas_tool_resolved_parameters,
    is_canvas_tool_request,
)
from short_drama.ai.adapters.video.video_adapters import (
    VIDEO_ADAPTERS,
)
from short_drama.ai.gateway.base import GatewayBase
from short_drama.ai.gateway.support import (
    _authentication,
    log,
)
from short_drama.ai.types import GenerationError, GenerationResult


class GenerationSubmissions(GatewayBase):
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
        count = request_data.get("parameters", {}).get("count", 1)
        if (
            adapter in {"openai_images.v1", "ark_images.v1"}
            and type(count) is int
            and count > 1
            and "canvas_request" not in request_data
        ):
            return self._submit_image_batch(
                snapshot, request_data, credential, adapter, reference_loader, mask_reference_loader
            )
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
        from short_drama.ai.adapters.image.image_references import is_stored_image_edit_request

        canvas_image = adapter == "openai_images.v1" and is_stored_image_edit_request(
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
            else parse_compatible_chat_result(body_response, adapter)
            if adapter == "openai_chat.v1" and is_compatible_chat(snapshot)
            else parse_result(body_response, adapter, submitted=True)
        )
        result.resolved_parameters = (
            canvas_tool_resolved_parameters(body)
            if adapter == "openai_chat.v1" and is_compatible_chat(snapshot)
            else resolved_parameters(body)
        )
        if adapter == ASYNC_AUDIO:
            result = complete_audio_urls(snapshot, result)
        if adapter != "dashscope_voice_design.v1" and snapshot.get("service_type") == "audio":
            result.usage = {**result.usage, "input_characters": len(request_data["input"]["text"])}
        for value in secrets:
            result = self._sanitize(result, value, submitted=True)
        return result

    def _submit_image_batch(self, snapshot, request, credential, adapter, loader, mask_loader):
        # Validate the entire request before starting any paid submission. Like the
        # canvas executor, use one independent single-image request per fixed slot.
        resolved = self.validate(snapshot, request, adapter)
        count = request["parameters"]["count"]
        deadline = time.monotonic() + float(snapshot.get("budget_seconds", 180))

        def submit_one(index):
            single = deepcopy(request)
            single["parameters"]["count"] = 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GenerationError("timeout")
            try:
                result = self.submit(
                    {**snapshot, "budget_seconds": remaining},
                    single,
                    credential,
                    adapter,
                    reference_loader=loader,
                    mask_reference_loader=mask_loader,
                )
                if result.status != "succeeded" or not result.outputs:
                    raise GenerationError("invalid_response", accepted_unknown=True)
                return result
            except GenerationError:
                raise
            except Exception:
                log.warning("Image request interrupted; adapter=%s output_index=%s", adapter, index)
                raise GenerationError("generation_interrupted", accepted_unknown=True) from None

        def add_usage(total, usage):
            for key, value in usage.items():
                if isinstance(value, dict):
                    add_usage(total.setdefault(key, {}), value)
                elif type(value) in {int, float}:
                    total[key] = total.get(key, 0) + value

        outputs, usage, failures = [], {}, []
        with ThreadPoolExecutor(max_workers=count, thread_name_prefix="image-generation") as pool:
            futures = [pool.submit(submit_one, index) for index in range(1, count + 1)]
            for index, future in enumerate(futures, start=1):
                try:
                    result = future.result()
                except GenerationError as error:
                    failures.append((index, error))
                    continue
                # A provider may ignore n=1 as well; each request owns exactly one slot.
                outputs.append({**result.outputs[0], "output_index": index})
                add_usage(usage, result.usage)
        if not outputs:
            error = next((error for _, error in failures if error.accepted_unknown), failures[0][1])
            raise GenerationError(
                error.code,
                accepted_unknown=any(error.accepted_unknown for _, error in failures),
                retryable=all(error.retryable for _, error in failures),
                http_status=error.http_status,
            ) from None
        return GenerationResult(
            status="succeeded",
            adapter=adapter,
            outputs=outputs,
            usage=usage,
            resolved_parameters=resolved,
            error={
                "code": "partial_result",
                "output_errors": [
                    {
                        "output_index": index,
                        "code": error.code,
                        "accepted_unknown": error.accepted_unknown,
                        **({"http_status": error.http_status} if error.http_status else {}),
                    }
                    for index, error in failures
                ],
            }
            if failures
            else None,
        )

    def validate(self, snapshot, request_data, adapter=None):
        validated = validate_request(snapshot, request_data, adapter)
        count = request_data.get("parameters", {}).get("count", 1)
        if (
            validated["adapter"] in {"openai_images.v1", "ark_images.v1"}
            and count > 1
            and "canvas_request" not in request_data
        ):
            single = deepcopy(request_data)
            single["parameters"]["count"] = 1
            return {
                **validate_request(snapshot, single, adapter)["resolved_parameters"],
                "request_count": count,
            }
        return validated["resolved_parameters"]
