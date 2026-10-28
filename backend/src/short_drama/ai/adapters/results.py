import re
from urllib.parse import quote

from short_drama.ai.adapters.audio.canvas_audio import (
    ASYNC_AUDIO,
    canvas_audio_poll_endpoint,
    parse_canvas_audio_result,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
    parse_runninghub_result,
    runninghub_root,
)
from short_drama.ai.adapters.text.claude_adapters import (
    CLAUDE_MESSAGES,
    parse_claude_text_result,
)
from short_drama.ai.adapters.text.gemini_adapters import (
    GEMINI_GENERATE_CONTENT,
    parse_gemini_text_result,
)
from short_drama.ai.adapters.video.video_adapters import (
    VIDEO_ADAPTERS,
    canvas_video_poll_endpoint,
    parse_canvas_video_result,
)
from short_drama.ai.gateway.endpoints import endpoint as endpoint
from short_drama.ai.types import GenerationError, GenerationResult


def poll_endpoint(snapshot, task_id, adapter):
    if adapter in RUNNINGHUB_ADAPTERS:
        return runninghub_root(snapshot["base_url"]) + "/task/openapi/outputs"
    if adapter == ASYNC_AUDIO:
        return canvas_audio_poll_endpoint(snapshot, task_id)
    if adapter in VIDEO_ADAPTERS:
        return canvas_video_poll_endpoint(snapshot, task_id, adapter)
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        raise GenerationError("invalid_provider_task")
    encoded = quote(task_id, safe="")
    if adapter == "ark_video.v1":
        suffix = f"/contents/generations/tasks/{encoded}"
    elif adapter == "modelhub_video.v1":
        suffix = f"/videos/tasks/{encoded}"
    elif adapter in ("dashscope_video.v1", "dashscope_images.v1"):
        suffix = f"/tasks/{encoded}"
    elif adapter == "openai_responses.v1":
        suffix = f"/responses/{encoded}"
    else:
        raise GenerationError("unsupported_poll")
    return endpoint(snapshot["base_url"], suffix, adapter)


def parse_result(body, adapter, *, submitted, task_id=None):
    if adapter in RUNNINGHUB_ADAPTERS:
        return parse_runninghub_result(body, adapter, submitted=submitted, task_id=task_id)
    if adapter == ASYNC_AUDIO:
        return parse_canvas_audio_result(body, submitted=submitted, task_id=task_id)
    if adapter == CLAUDE_MESSAGES:
        return parse_claude_text_result(body, submitted=submitted)
    if adapter == GEMINI_GENERATE_CONTENT:
        return parse_gemini_text_result(body, submitted=submitted)
    if adapter in VIDEO_ADAPTERS:
        return parse_canvas_video_result(body, adapter, submitted=submitted, task_id=task_id)

    def invalid():
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )

    if not isinstance(body, dict):
        invalid()
    result = GenerationResult("succeeded", adapter, provider_task_id=task_id)
    result.usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    if body.get("error") or body.get("code"):
        result.status = "failed"
        result.error = {"code": "provider_rejected", "message": "The provider rejected generation."}
        return result
    try:
        if adapter == "dashscope_voice_design.v1":
            output = body["output"]
            preview = output["preview_audio"]
            voice_id = output["voice_id"]
            if (
                not isinstance(voice_id, str)
                or not voice_id
                or len(voice_id) > 255
                or output.get("target_model") != "cosyvoice-v3.5-flash"
                or preview.get("response_format") != "wav"
                or not isinstance(preview.get("data"), str)
                or len(preview["data"]) > 14 * 1024**2
            ):
                invalid()
            result.provider_task_id = body.get("request_id")
            if not isinstance(result.provider_task_id, str) or not result.provider_task_id:
                invalid()
            result.voice = {"voice_id": voice_id, "target_model": output["target_model"]}
            result.outputs = [{"base64": preview["data"], "media_type": "audio"}]
        elif adapter == "dashscope_speech.v1":
            output = body["output"]
            if output.get("finish_reason") != "stop":
                invalid()
            result.provider_task_id = body.get("request_id")
            if not isinstance(result.provider_task_id, str) or not result.provider_task_id:
                invalid()
            result.outputs = [{"url": output["audio"]["url"], "media_type": "audio"}]
            result.finish_reason = "stop"
        elif adapter == "openai_chat.v1":
            choice = body["choices"][0]
            result.text = choice["message"]["content"]
            result.finish_reason = choice.get("finish_reason")
            if not isinstance(result.text, str):
                invalid()
        elif adapter == "openai_responses.v1":
            status = body.get("status")
            if status in ("queued", "in_progress"):
                result.status, result.provider_task_id = "submitted", body["id"]
            elif status == "failed":
                result.status = "failed"
                result.error = {
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                }
            else:
                direct_text = body.get("output_text")
                if isinstance(direct_text, str) and direct_text:
                    texts = [direct_text]
                else:
                    texts = [
                        part["text"]
                        for item in body["output"]
                        if item.get("type") == "message"
                        for part in item["content"]
                        if part.get("type") == "output_text"
                    ]
                if not texts or any(not isinstance(text, str) for text in texts):
                    invalid()
                result.text = "".join(texts)
                reason = (body.get("incomplete_details") or {}).get("reason")
                result.finish_reason = (
                    "length" if reason == "max_output_tokens" else reason or "stop"
                )
        elif adapter in ("openai_images.v1", "ark_images.v1"):
            for item in body["data"]:
                if item.get("url"):
                    result.outputs.append({"url": item["url"], "media_type": "image"})
                elif item.get("b64_json"):
                    result.outputs.append(
                        {"base64": item["b64_json"], "media_type": "image", "mime": "image/png"}
                    )
                elif not item.get("error"):
                    invalid()
            if not result.outputs:
                invalid()
        elif adapter == "modelhub_video.v1":
            status = body.get("status")
            result.provider_task_id = body.get("task_id") or body.get("id") or task_id
            if status in (
                "processing",
                "in_queue",
                "pending",
                "queued",
                "not_start",
                "submitted",
            ) or (status is None and submitted):
                result.status = "submitted"
            elif status in ("failed", "cancelled", "blocked"):
                result.status = "failed"
                result.error = {
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                }
            elif status in ("success", "completed"):
                result.outputs = [
                    {
                        "url": (body.get("result") or {}).get("url") or body.get("url"),
                        "media_type": "video",
                    }
                ]
            else:
                invalid()
        elif adapter == "ark_video.v1":
            status = body.get("status")
            result.provider_task_id = body.get("id", task_id)
            if status in ("queued", "running") or (status is None and submitted):
                result.status = "submitted"
            elif status in ("failed", "cancelled", "expired"):
                result.status = "failed"
                result.error = {
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                }
            elif status == "succeeded":
                result.outputs = [{"url": body["content"]["video_url"], "media_type": "video"}]
            else:
                invalid()
        else:
            output = body["output"]
            status = output.get("task_status")
            result.provider_task_id = output.get("task_id", task_id)
            if status in ("PENDING", "RUNNING"):
                result.status = "submitted"
            elif status == "UNKNOWN":
                raise GenerationError("provider_outcome_unknown", accepted_unknown=True)
            elif status in ("FAILED", "CANCELED", "CANCELLED"):
                result.status = "failed"
                result.error = {
                    "code": "provider_failed",
                    "message": "The provider could not complete generation.",
                }
            elif adapter == "dashscope_video.v1" and status == "SUCCEEDED":
                result.outputs = [{"url": output["video_url"], "media_type": "video"}]
            elif adapter == "dashscope_images.v1" and status in (None, "SUCCEEDED"):
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
                if not result.outputs:
                    invalid()
            else:
                invalid()
        if result.status == "submitted" and (
            not isinstance(result.provider_task_id, str) or not result.provider_task_id
        ):
            invalid()
        if len(result.outputs) > 4:
            invalid()
        for output in result.outputs:
            field = "url" if "url" in output else "base64"
            if not isinstance(output[field], str) or not output[field]:
                invalid()
        return result
    except (KeyError, TypeError, IndexError, AttributeError):
        invalid()
