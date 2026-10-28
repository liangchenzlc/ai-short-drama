import re

from pydantic import ValidationError

from short_drama.ai.adapters.audio.canvas_audio import (
    ASYNC_AUDIO,
    canvas_audio_options,
    canvas_audio_voice,
)
from short_drama.ai.adapters.catalog import _unsupported, select_adapter
from short_drama.ai.adapters.constants import ADAPTER_TYPES
from short_drama.ai.adapters.image.image_operations import (
    is_image_operation,
    operation_text_messages,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
    runninghub_submission,
)
from short_drama.ai.adapters.text.chat_protocols import (
    apply_compatible_chat_options,
    compatible_chat_endpoint,
    is_compatible_chat,
)
from short_drama.ai.adapters.text.claude_adapters import (
    CLAUDE_MESSAGES,
    build_claude_text_submission,
)
from short_drama.ai.adapters.text.gemini_adapters import (
    GEMINI_GENERATE_CONTENT,
    build_gemini_text_submission,
)
from short_drama.ai.adapters.text.text_adapters import canvas_text_messages, is_canvas_text_request
from short_drama.ai.adapters.text.text_thinking import thinking_parameters
from short_drama.ai.adapters.tools.submission import (
    build_canvas_tool_submission,
    is_canvas_tool_request,
)
from short_drama.ai.adapters.video.input_capabilities import video_input_capabilities
from short_drama.ai.adapters.video.video_adapters import (
    VIDEO_ADAPTERS,
    build_canvas_video_submission,
)
from short_drama.ai.gateway.endpoints import endpoint as endpoint
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError
from short_drama.schemas.canvas.generation.generation_parameters import (
    CANVAS_PARAMETERS,
    CanvasAudioParameters,
    CanvasImageParameters,
    CanvasVideoParameters,
)


def _openai_image_references(model):
    # Includes provider aliases such as gpt-image-2.5-flare. This describes the
    # implemented Images edit protocol, not verified provider entitlement.
    return model.startswith("gpt-image-")


def _size(parameters, *, family, model):
    aspect, resolution = parameters.get("aspect"), parameters.get("resolution")
    if aspect is None and resolution is None:
        return None
    if resolution and re.fullmatch(r"\d{2,5}[x*]\d{2,5}", resolution):
        width, height = map(int, re.split("[x*]", resolution))
        if aspect:
            try:
                a, b = map(int, aspect.split(":"))
                # Allow provider-recommended dimensions rounded to multiples of 16.
                if not a or not b or abs(width / height - a / b) > 0.015:
                    _unsupported()
            except (ValueError, ZeroDivisionError):
                _unsupported()
        return f"{width}{'*' if family == 'dashscope' else 'x'}{height}"
    if family == "openai":
        if re.match(r"^gpt-image-2(?:[.-]|$)", model):
            # App presets, expressed as pixels rather than provider-specific
            # resolution labels. Keep legacy GPT Image / DALL-E rules below.
            presets = {
                "1K": {
                    "1:1": "1024x1024",
                    "16:9": "1536x864",
                    "9:16": "864x1536",
                    "4:3": "1152x864",
                    "3:4": "864x1152",
                    "3:2": "1536x1024",
                    "2:3": "1024x1536",
                },
                "2K": {
                    "1:1": "2048x2048",
                    "16:9": "2560x1440",
                    "9:16": "1440x2560",
                    "4:3": "2304x1728",
                    "3:4": "1728x2304",
                    "3:2": "2496x1664",
                    "2:3": "1664x2496",
                },
            }
            tier = "1K" if resolution in (None, "1024") else resolution
            size = presets.get(tier, {}).get(aspect or "1:1")
            if not size:
                _unsupported()
            return size
        if resolution not in (None, "1K", "1024"):
            _unsupported()
        sizes = {"1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536"}
        if model.startswith("dall-e-3"):
            sizes = {"1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792"}
        if aspect not in (None, *sizes):
            _unsupported()
        return sizes[aspect or "1:1"]
    if family == "ark":
        if aspect is None and resolution in ("1K", "2K", "4K"):
            return resolution
        sizes = {
            "2K": {
                "1:1": "2048x2048",
                "16:9": "2560x1440",
                "9:16": "1440x2560",
                "4:3": "2304x1728",
                "3:4": "1728x2304",
                "3:2": "2496x1664",
                "2:3": "1664x2496",
            },
            "4K": {
                "1:1": "4096x4096",
                "16:9": "4096x2304",
                "9:16": "2304x4096",
                "4:3": "4096x3072",
                "3:4": "3072x4096",
            },
        }
        value = sizes.get(resolution or "2K", {}).get(aspect or "1:1")
    else:
        sizes = {
            "1:1": "1024*1024",
            "16:9": "1280*720",
            "9:16": "720*1280",
            "4:3": "1152*864",
            "3:4": "864*1152",
        }
        if model.startswith(("wan2.5", "wan2.6")):
            sizes = {
                "1:1": "1280*1280",
                "16:9": "1696*960",
                "9:16": "960*1696",
                "4:3": "1472*1104",
                "3:4": "1104*1472",
            }
        if resolution not in (None, "1K", "1024"):
            _unsupported()
        value = sizes.get(aspect or "1:1")
    if not value:
        _unsupported()
    return value


def _input(request, allowed):
    value = request.get("input", {})
    if not isinstance(value, dict) or value.keys() - allowed:
        _unsupported()
    return value


def _canvas_image_submission(model, prompt, refs, count, parameters, adapter):
    """固定源声明式图片插件的请求；标准模式继续使用 _size。"""
    from short_drama.ai.adapters.image.agnes_adapters import AGNES_IMAGE, agnes_image_body

    if adapter == "openai_images.v1" and model == AGNES_IMAGE:
        return "/images/generations", {}, agnes_image_body(prompt, refs, count, parameters)
    body = {"model": model}
    raw = parameters.size
    if parameters.mask_media_id and (adapter != "openai_images.v1" or not refs):
        _unsupported()
    if parameters.transparent_background and adapter != "openai_images.v1":
        _unsupported()
    if adapter == "openai_images.v1":
        if refs and (not _openai_image_references(model) or len(refs) > 16):
            _unsupported()
        if model.startswith("dall-e-3") and count != 1:
            _unsupported()
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
        body.update(prompt=prompt, n=count, output_format="png")
        if raw != "auto":
            body["size"] = sizes.get(raw or "1:1", raw)
        if parameters.quality != "auto":
            body["quality"] = {"1k": "low", "2k": "medium", "4k": "high"}.get(
                parameters.quality, parameters.quality
            )
        if parameters.transparent_background:
            body["background"] = "transparent"
        if refs:
            body["image"] = refs
        if parameters.mask_media_id:
            body["mask"] = "https://reference.invalid/mask"
        suffix = "/images/edits" if refs else "/images/generations"
    elif adapter == "ark_images.v1":
        if count != 1:
            _unsupported()
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
        body.update(prompt=prompt, response_format="b64_json", watermark=False)
        if raw:
            body["size"] = sizes.get(raw, raw)
        if refs:
            body["image"] = refs[0] if len(refs) == 1 else refs
        suffix = "/images/generations"
    elif adapter == "dashscope_images.v1":
        native_params = {"n": count, "watermark": False}
        if model.startswith(("qwen-image", "wan2.6", "wan2.7")):
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
            if raw in sizes:
                native_params["size"] = sizes[raw]
            native_params.update(prompt_extend=True, enable_thinking=False)
            body["input"] = {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"image": ref} for ref in refs] + [{"text": prompt}],
                    }
                ]
            }
            suffix = "/services/aigc/image-generation/generation"
        else:
            body["input"] = {"prompt": prompt}
            if raw:
                native_params["size"] = raw
            if refs:
                body["input"]["ref_img" if len(refs) == 1 else "ref_images"] = (
                    refs[0] if len(refs) == 1 else refs
                )
            suffix = "/services/aigc/text2image/image-synthesis"
        body["parameters"] = native_params
        return suffix, {"X-DashScope-Async": "enable"}, body
    else:
        _unsupported()
    return suffix, {}, body


def build_submission(snapshot, request, adapter):
    if is_canvas_tool_request(request):
        return build_canvas_tool_submission(snapshot, request, adapter)
    if adapter == CLAUDE_MESSAGES:
        return build_claude_text_submission(snapshot, request)
    if adapter == GEMINI_GENERATE_CONTENT:
        return build_gemini_text_submission(snapshot, request)
    if adapter in RUNNINGHUB_ADAPTERS:
        return runninghub_submission(snapshot, request, adapter)
    if adapter in VIDEO_ADAPTERS:
        return build_canvas_video_submission(snapshot, request, adapter)
    kind = ADAPTER_TYPES.get(adapter)
    if kind != snapshot.get("service_type"):
        raise GenerationError("unsupported_protocol")
    canvas_parameters = None
    if request.get("image_edit_parameters") is not None:
        if not is_image_operation(request) or kind != "image" or "canvas_parameters" in request:
            _unsupported()
        try:
            canvas_parameters = CanvasImageParameters.model_validate(
                request["image_edit_parameters"]
            )
        except ValidationError:
            raise GenerationError("unsupported_parameters") from None
    if request.get("canvas_parameters") is not None:
        if (request.get("source") or {}).get("scene") not in {"canvas_node", "canvas_model_test"}:
            _unsupported()
        try:
            canvas_parameters = CANVAS_PARAMETERS.validate_python(request["canvas_parameters"])
        except ValidationError:
            raise GenerationError("unsupported_parameters") from None
        if canvas_parameters.mode != kind:
            _unsupported()
    params = {k: v for k, v in request.get("parameters", {}).items() if v is not None}
    supported = {
        "audio": {"voice"},
        "text": {"temperature", "max_output_tokens"},
        "image": {"aspect", "resolution", "count"},
        "video": {"aspect", "resolution", "duration_ms", "generate_audio"},
    }[kind]
    if params.keys() - supported:
        _unsupported()
    model = snapshot["model_key"]
    body, headers = {"model": model}, {}
    if adapter == "dashscope_voice_design.v1":
        if select_adapter(snapshot) != "dashscope_speech.v1" or model != "cosyvoice-v3.5-flash":
            raise GenerationError("unsupported_voice_design")
        value = _input(request, {"voice_prompt", "preview_text"})
        if (
            params
            or not isinstance(value.get("voice_prompt"), str)
            or not 1 <= len(value["voice_prompt"].strip()) <= 500
            or not isinstance(value.get("preview_text"), str)
            or not 15 <= len(value["preview_text"].strip()) <= 200
        ):
            _unsupported()
        return (
            endpoint(snapshot["base_url"], "/services/audio/tts/customization", adapter),
            {},
            {
                "model": "voice-enrollment",
                "input": {
                    "action": "create_voice",
                    "target_model": model,
                    "prefix": "drama",
                    **value,
                },
                "parameters": {"sample_rate": 24000, "response_format": "wav"},
            },
        )
    if kind == "audio":
        value = _input(request, {"text"})
        if adapter == ASYNC_AUDIO and not isinstance(canvas_parameters, CanvasAudioParameters):
            _unsupported()
        if (
            not isinstance(value.get("text"), str)
            or not value["text"].strip()
            or len(value["text"]) > 4096
        ):
            _unsupported()
        if not isinstance(params.get("voice"), str) or not params["voice"].strip():
            _unsupported()
        if adapter == "dashscope_speech.v1":
            if canvas_parameters is not None:
                _unsupported()
            if model not in {
                "cosyvoice-v3.5-flash",
                "cosyvoice-v3.5-plus",
                "cosyvoice-v3-flash",
                "cosyvoice-v3-plus",
                "cosyvoice-v2",
            }:
                _unsupported()
            body["input"] = {
                "text": value["text"],
                "voice": params["voice"],
                "format": "wav",
                "sample_rate": 24000,
            }
            return (
                endpoint(snapshot["base_url"], "/services/audio/tts/SpeechSynthesizer", adapter),
                headers,
                body,
            )
        body.update(input=value["text"], voice=params["voice"], response_format="wav")
        if isinstance(canvas_parameters, CanvasAudioParameters):
            options = canvas_audio_options(snapshot, request, adapter)
            body.update(
                response_format=canvas_parameters.format,
                speed=canvas_parameters.speed or options.speed or 1,
            )
            fallback_voice = options.voice if options.voice and options.voice.strip() else "alloy"
            body["voice"] = canvas_audio_voice(model, params["voice"]) or fallback_voice
            instructions = canvas_parameters.instructions.strip() or options.instructions or ""
            if instructions.strip():
                body["instructions"] = instructions
        if adapter == ASYNC_AUDIO:
            from short_drama.ai.adapters.video.video_adapters import canvas_video_endpoint

            return canvas_video_endpoint(snapshot["base_url"], "/v1/audio/tasks"), headers, body
        return endpoint(snapshot["base_url"], "/audio/speech", adapter), headers, body
    if kind == "text":
        canvas_references = is_canvas_text_request(snapshot, request)
        value = _input(
            request,
            {
                "messages",
                "reference_media_ids",
                "reference_urls",
                "video_reference_media_ids",
                "video_reference_urls",
            }
            if canvas_references or is_image_operation(request)
            else {"messages"},
        )
        messages = value.get("messages")
        if not isinstance(messages, list) or not messages:
            _unsupported()
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.keys() - {"role", "content"}
                or message.get("role") not in ("system", "developer", "user", "assistant")
                or not isinstance(message.get("content"), str)
            ):
                _unsupported()
        responses = adapter == "openai_responses.v1"
        if canvas_references:
            messages = canvas_text_messages(snapshot, request, adapter, messages)
            if responses:
                config = request["canvas_request"]["input"].get("config") or {}
                if not isinstance(config, dict) or not isinstance(
                    config.get("systemPrompt", ""), str
                ):
                    _unsupported()
                if instructions := config.get("systemPrompt", "").strip():
                    body["instructions"] = instructions
        elif is_image_operation(request):
            messages = operation_text_messages(snapshot, request, adapter, messages)
        body["input" if responses else "messages"] = messages
        # The worker collects SSE internally; the browser still uses task polling.
        body["stream"] = True
        if (request.get("source") or {}).get("scene") in {"canvas_node", "canvas_model_test"}:
            options = (request.get("canvas_request") or {}).get("input", {}).get("textOptions", {})
            if (
                not isinstance(options, dict)
                or options.keys() - {"stream", "thinking"}
                or type(options.get("stream", True)) is not bool
                or type(options.get("thinking", False)) is not bool
            ):
                _unsupported()
            if options.get("thinking", False) and (
                (request.get("canvas_request") or {}).get("type") != "canvas_text"
                or (request.get("canvas_request") or {}).get("operation") != "text"
            ):
                _unsupported()
            body.update(
                thinking_parameters(
                    adapter, options.get("thinking", False), snapshot.get("canvas_text_capability")
                )
            )
            body["stream"] = options.get("stream", True)
            capability = snapshot.get("canvas_text_capability")
            if isinstance(capability, dict) and capability.get("streaming") is False:
                body["stream"] = False
            if adapter == "openai_chat.v1" and body["stream"]:
                body["stream_options"] = {"include_usage": True}
        if "temperature" in params:
            body["temperature"] = params["temperature"]
        if "max_output_tokens" in params:
            token_field = "max_output_tokens" if responses else "max_tokens"
            if not responses and model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
                token_field = "max_completion_tokens"
            body[token_field] = params["max_output_tokens"]
        suffix = "/responses" if responses else "/chat/completions"
        if adapter == "openai_chat.v1" and is_compatible_chat(snapshot):
            body = apply_compatible_chat_options(snapshot, request, body)
            return compatible_chat_endpoint(snapshot["base_url"]), headers, body
    elif kind == "image":
        value = _input(request, {"prompt", "reference_media_ids", "reference_urls"})
        prompt, refs = value.get("prompt"), value.get("reference_urls", [])
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(refs, list):
            _unsupported()
        if value.get("reference_media_ids") and not refs:
            raise GenerationError("unresolved_media_reference")
        for ref in refs:
            validated_url(ref, query=True)
        count = params.get("count", 1)
        if type(count) is not int or not 1 <= count <= 4:
            _unsupported()
        if isinstance(canvas_parameters, CanvasImageParameters):
            if params.keys() - {"count"}:
                _unsupported()
            suffix, headers, body = _canvas_image_submission(
                model, prompt, refs, count, canvas_parameters, adapter
            )
            return endpoint(snapshot["base_url"], suffix, adapter), headers, body
        if adapter == "openai_images.v1":
            if refs and (not _openai_image_references(model) or len(refs) > 16):
                _unsupported()
            if model.startswith("dall-e-3") and count != 1:
                _unsupported()
            body.update(prompt=prompt, n=count)
            try:
                size = _size(params, family="openai", model=model)
            except GenerationError:
                raise GenerationError("unsupported_image_size") from None
            if size:
                body["size"] = size
            if refs:
                # A pure request recipe; the gateway downloads and uploads the
                # files only during execution, never during admission validation.
                body["image"] = refs
            suffix = "/images/edits" if refs else "/images/generations"
        elif adapter == "ark_images.v1":
            # Standard multi-image generation must return inline bytes. Provider URLs
            # are short-lived and each URL requires an independent download; one
            # expired/failed URL otherwise turns an otherwise valid 4-image result
            # into partial_result during archive.
            body.update(prompt=prompt, response_format="b64_json", stream=False)
            if refs:
                body["image"] = refs
            size = _size(params, family="ark", model=model)
            if size:
                body["size"] = size
            body["sequential_image_generation"] = "auto" if count > 1 else "disabled"
            if count > 1:
                body["sequential_image_generation_options"] = {"max_images": count}
            suffix = "/images/generations"
        else:
            modern = model.startswith(("qwen-image", "wan2.6", "wan2.7"))
            edit = model.startswith("qwen-image-edit") or model == "wan2.6-image"
            if refs and not edit:
                _unsupported()
            if edit and (not refs or len(refs) > (4 if model == "wan2.6-image" else 3)):
                _unsupported()
            native_params = {"n": count}
            if (
                model == "wan2.6-image"
                and not params.get("aspect")
                and params.get("resolution") in ("1K", "2K")
            ):
                size = params["resolution"]
            else:
                size = _size(params, family="dashscope", model=model)
            if size:
                native_params["size"] = size
            if model == "wan2.6-image":
                native_params["enable_interleave"] = False
            body["parameters"] = native_params
            if modern:
                content = [{"image": ref} for ref in refs] + [{"text": prompt}]
                body["input"] = {"messages": [{"role": "user", "content": content}]}
                suffix = "/services/aigc/multimodal-generation/generation"
            else:
                body["input"] = {"prompt": prompt}
                headers["X-DashScope-Async"] = "enable"
                suffix = "/services/aigc/text2image/image-synthesis"
    else:
        if isinstance(canvas_parameters, CanvasVideoParameters):
            if canvas_parameters.generate_audio is not None:
                if adapter not in {"ark_video.v1", "modelhub_video.v1"}:
                    _unsupported()
                params["generate_audio"] = canvas_parameters.generate_audio
            if canvas_parameters.watermark is not None and adapter != "ark_video.v1":
                _unsupported()
        value = _input(
            request,
            {
                "prompt",
                "first_frame_media_id",
                "last_frame_media_id",
                "first_frame_url",
                "last_frame_url",
                "reference_media_ids",
                "reference_urls",
                "audio_reference_media_ids",
                "audio_reference_urls",
            },
        )
        prompt = value.get("prompt")
        first, last = value.get("first_frame_url"), value.get("last_frame_url")
        refs = value.get("reference_urls", [])
        audio_refs = value.get("audio_reference_urls", [])
        if (
            not isinstance(audio_refs, list)
            or len(audio_refs) > 2
            or (audio_refs and (adapter != "modelhub_video.v1" or first or last))
            or (value.get("audio_reference_media_ids") and not audio_refs)
        ):
            _unsupported()
        if "generate_audio" in params and (
            (
                adapter != "modelhub_video.v1"
                and not isinstance(canvas_parameters, CanvasVideoParameters)
            )
            or type(params["generate_audio"]) is not bool
        ):
            raise GenerationError("native_audio_unsupported")
        for ref in audio_refs:
            validated_url(ref, query=True)
        if not isinstance(refs, list) or len(refs) > 9:
            _unsupported()
        if value.get("reference_media_ids") and not refs:
            raise GenerationError("unresolved_media_reference")
        if refs and (adapter != "modelhub_video.v1" or first or last):
            _unsupported()
        for ref in refs:
            validated_url(ref, query=True)
        if not isinstance(prompt, str) or not prompt.strip():
            _unsupported()
        for name in ("first", "last"):
            if value.get(f"{name}_frame_media_id") and not value.get(f"{name}_frame_url"):
                raise GenerationError("unresolved_media_reference")
        for ref in (first, last):
            if ref:
                validated_url(ref, query=True)
        if last and not first:
            _unsupported()
        video_options = video_input_capabilities(snapshot, adapter)
        if (request.get("source") or {}).get("scene") == "shot_video":
            if not video_options["reference_images" if refs else "first_frame"]:
                _unsupported()
            durations = video_options["duration_seconds"]
            if durations and params.get("duration_ms", 0) // 1000 not in durations:
                _unsupported()
        native_params = {}
        if "duration_ms" in params:
            duration = params["duration_ms"]
            if type(duration) is not int or duration <= 0 or duration % 1000:
                _unsupported()
            native_params["duration"] = duration // 1000
        if adapter == "modelhub_video.v1":
            # Public ModelHub /v1/models + /quick-start, checked 2026-10-12.
            minimax = model == "minimax-h3"
            if model not in {"seedance-2.0-mini", "minimax-h3"}:
                _unsupported()
            duration = native_params.get("duration", 5)
            resolution = params.get("resolution", "480p" if minimax else "720p").lower()
            ratio = params.get("aspect", "auto" if first and not minimax else "9:16")
            if (
                duration not in range(5 if minimax else 4, 16)
                or resolution not in (("480p",) if minimax else ("480p", "720p"))
                or ratio not in ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "auto")
                or (minimax and (ratio == "auto" or audio_refs or "generate_audio" in params))
            ):
                _unsupported()
            body.update(
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                ratio=ratio,
                functionMode="first_last_frames" if first else "omni_reference",
            )
            for index, ref in enumerate(refs or list(filter(None, (first, last))), 1):
                body[f"image_file_{index}"] = ref
            for index, ref in enumerate(audio_refs, 1):
                body[f"audio_file_{index}"] = ref
            if "generate_audio" in params:
                body["generate_audio"] = params["generate_audio"]
            suffix = "/videos/generations"
        elif adapter == "ark_video.v1":
            body["content"] = [{"type": "text", "text": prompt}]
            for role, ref in (("first_frame", first), ("last_frame", last)):
                if ref:
                    body["content"].append(
                        {"type": "image_url", "image_url": {"url": ref}, "role": role}
                    )
            if "aspect" in params:
                native_params["ratio"] = params["aspect"]
            if "resolution" in params:
                native_params["resolution"] = params["resolution"].lower()
            if isinstance(canvas_parameters, CanvasVideoParameters):
                if canvas_parameters.generate_audio is not None:
                    native_params["generate_audio"] = canvas_parameters.generate_audio
                if canvas_parameters.watermark is not None:
                    native_params["watermark"] = canvas_parameters.watermark
            body.update(native_params)
            suffix = "/contents/generations/tasks"
        else:
            body["input"] = {"prompt": prompt}
            if first:
                body["input"]["img_url"] = first
            if last:
                # First/last-frame generation has a distinct model and input contract.
                if "kf2v" not in model:
                    _unsupported()
                body["input"].pop("img_url", None)
                body["input"].update(first_frame_url=first, last_frame_url=last)
            resolution, aspect = params.get("resolution"), params.get("aspect")
            if first:
                if aspect:
                    _unsupported()  # Image-to-video derives ratio from its first frame.
                if resolution:
                    allowed_resolutions = {
                        "wan2.2-i2v-plus": {"480P", "1080P"},
                        "wanx2.1-i2v-plus": {"720P"},
                        "wanx2.1-i2v-turbo": {"480P", "720P"},
                    }.get(model, {"480P", "720P", "1080P"})
                    if model.startswith("wan2.6"):
                        allowed_resolutions = {"720P", "1080P"}
                    if resolution.upper() not in allowed_resolutions:
                        _unsupported()
                    native_params["resolution"] = resolution.upper()
            elif model.startswith("wan2.7"):
                if resolution:
                    native_params["resolution"] = resolution.upper()
                if aspect:
                    native_params["ratio"] = aspect
            elif resolution or aspect:
                sizes = {
                    "480p": {"16:9": "832*480", "9:16": "480*832", "1:1": "624*624"},
                    "720p": {"16:9": "1280*720", "9:16": "720*1280", "1:1": "960*960"},
                    "1080p": {"16:9": "1920*1080", "9:16": "1080*1920", "1:1": "1440*1440"},
                }
                size = sizes.get((resolution or "720p").lower(), {}).get(aspect or "16:9")
                if not size:
                    _unsupported()
                native_params["size"] = size
            body["parameters"] = native_params
            headers["X-DashScope-Async"] = "enable"
            suffix = "/services/aigc/video-generation/video-synthesis"
    return endpoint(snapshot["base_url"], suffix, adapter), headers, body
