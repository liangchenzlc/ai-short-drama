"""Explicit provider contracts, with stable adapter names frozen per invocation."""

import copy
import hashlib
import json
import re
from urllib.parse import quote, urlsplit, urlunsplit

from .transport import validated_url
from .types import GenerationError, GenerationResult

ADAPTER_TYPES = {
    "openai_chat.v1": "text",
    "openai_responses.v1": "text",
    "openai_images.v1": "image",
    "ark_images.v1": "image",
    "ark_video.v1": "video",
    "dashscope_images.v1": "image",
    "dashscope_video.v1": "video",
}

RESOLVER_VERSION = "2026-09-23.1"


def capability_fingerprint(snapshot, credential_identity):
    """credential_identity is a hash of the stored ciphertext, never the plaintext key."""
    value = [
        validated_url(snapshot["base_url"].strip()).rstrip("/"),
        snapshot.get("model_key"),
        snapshot.get("service_type"),
        credential_identity,
        RESOLVER_VERSION,
    ]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def select_adapter(snapshot):
    parts = urlsplit(validated_url(snapshot["base_url"].strip()))
    kind = snapshot.get("service_type")
    cache = snapshot.get("capability_cache")
    identity = snapshot.get("credential_identity")
    if isinstance(cache, dict) and isinstance(identity, str):
        cached_adapter = cache.get("adapter")
        if ADAPTER_TYPES.get(cached_adapter) == kind and cache.get(
            "fingerprint"
        ) == capability_fingerprint(snapshot, identity):
            return cached_adapter
    host, path = parts.hostname, parts.path.rstrip("/")
    ark = host.endswith(".volces.com") or "/api/v3/contents/generations" in path
    dashscope = (
        host
        in {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
        }
        or host.endswith(".maas.aliyuncs.com")
        or "/api/v1/services/aigc/" in path
    )
    if kind == "text":
        return "openai_responses.v1" if path.endswith("/responses") else "openai_chat.v1"
    if kind == "image":
        if ark:
            return "ark_images.v1"
        if dashscope:
            return "dashscope_images.v1"
        # Images is a documented common protocol; unsupported gateways fail explicitly.
        return "openai_images.v1"
    if kind == "video":
        if ark:
            return "ark_video.v1"
        if dashscope:
            return "dashscope_video.v1"
    raise GenerationError("unsupported_protocol")


def endpoint(base_url, suffix, adapter):
    parts = urlsplit(validated_url(base_url.strip()))
    path = parts.path.rstrip("/")
    if adapter.startswith("dashscope_"):
        # Compatible-mode is a text endpoint, not the native media root.
        for marker in ("/compatible-mode", "/api/v1"):
            if marker in path:
                path = path.split(marker, 1)[0]
                break
        path += "/api/v1"
    else:
        for ending in (
            "/chat/completions",
            "/responses",
            "/images/generations",
            "/images/edits",
            "/contents/generations/tasks",
        ):
            if path.endswith(ending):
                path = path[: -len(ending)]
                break
        if not path:
            path = "/api/v3" if adapter.startswith("ark_") else "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path + suffix, "", ""))


def _unsupported():
    raise GenerationError("unsupported_parameters")


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


def build_submission(snapshot, request, adapter):
    kind = ADAPTER_TYPES.get(adapter)
    if kind != snapshot.get("service_type"):
        raise GenerationError("unsupported_protocol")
    params = {k: v for k, v in request.get("parameters", {}).items() if v is not None}
    supported = {
        "text": {"temperature", "max_output_tokens"},
        "image": {"aspect", "resolution", "count"},
        "video": {"aspect", "resolution", "duration_ms"},
    }[kind]
    if params.keys() - supported:
        _unsupported()
    model = snapshot["model_key"]
    body, headers = {"model": model}, {}
    if kind == "text":
        value = _input(request, {"messages"})
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
        body["input" if responses else "messages"] = messages
        # The worker collects SSE internally; the browser still uses task polling.
        body["stream"] = True
        if "temperature" in params:
            body["temperature"] = params["temperature"]
        if "max_output_tokens" in params:
            token_field = "max_output_tokens" if responses else "max_tokens"
            if not responses and model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
                token_field = "max_completion_tokens"
            body[token_field] = params["max_output_tokens"]
        suffix = "/responses" if responses else "/chat/completions"
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
            body.update(prompt=prompt, response_format="url", stream=False)
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
        value = _input(
            request,
            {
                "prompt",
                "first_frame_media_id",
                "last_frame_media_id",
                "first_frame_url",
                "last_frame_url",
            },
        )
        prompt = value.get("prompt")
        first, last = value.get("first_frame_url"), value.get("last_frame_url")
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
        native_params = {}
        if "duration_ms" in params:
            duration = params["duration_ms"]
            if type(duration) is not int or duration <= 0 or duration % 1000:
                _unsupported()
            native_params["duration"] = duration // 1000
        if adapter == "ark_video.v1":
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


def poll_endpoint(snapshot, task_id, adapter):
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        raise GenerationError("invalid_provider_task")
    encoded = quote(task_id, safe="")
    if adapter == "ark_video.v1":
        suffix = f"/contents/generations/tasks/{encoded}"
    elif adapter in ("dashscope_video.v1", "dashscope_images.v1"):
        suffix = f"/tasks/{encoded}"
    elif adapter == "openai_responses.v1":
        suffix = f"/responses/{encoded}"
    else:
        raise GenerationError("unsupported_poll")
    return endpoint(snapshot["base_url"], suffix, adapter)


def parse_result(body, adapter, *, submitted, task_id=None):
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
        if adapter == "openai_chat.v1":
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


def resolved_parameters(body):
    if "parameters" in body:
        return body["parameters"]
    return {
        k: v
        for k, v in body.items()
        if k
        not in {
            "model",
            "prompt",
            "messages",
            "input",
            "content",
            "image",
        }
    }


def validate_request(snapshot, request_data, adapter=None):
    """Pure admission validation; placeholders are never submitted to a provider."""
    adapter = adapter or select_adapter(snapshot)
    request = copy.deepcopy(request_data)
    inputs = request.setdefault("input", {})
    if inputs.get("reference_media_ids") and "reference_urls" not in inputs:
        inputs["reference_urls"] = [
            f"https://reference.invalid/{i}" for i, _ in enumerate(inputs["reference_media_ids"])
        ]
    for name in ("first", "last"):
        if inputs.get(f"{name}_frame_media_id") and not inputs.get(f"{name}_frame_url"):
            inputs[f"{name}_frame_url"] = f"https://reference.invalid/{name}"
    _, _, body = build_submission(snapshot, request, adapter)
    return {"adapter": adapter, "resolved_parameters": resolved_parameters(body)}


def capabilities(snapshot):
    """Local protocol support, not proof of account/model entitlement or a paid probe."""
    empty = {
        "known": False,
        "parameters": [],
        "reference_images": False,
        "first_frame": False,
        "last_frame": False,
    }
    try:
        adapter = select_adapter(snapshot)
    except (GenerationError, KeyError):
        return empty
    kind = snapshot.get("service_type")
    model = snapshot.get("model_key", "")
    edit = model.startswith("qwen-image-edit") or model == "wan2.6-image"
    return {
        "known": True,
        "parameters": {
            "text": ["temperature", "max_output_tokens"],
            "image": ["aspect", "resolution", "count"],
            "video": ["aspect", "resolution", "duration_ms"],
        }[kind],
        "reference_images": adapter == "ark_images.v1"
        or (adapter == "dashscope_images.v1" and edit)
        or (adapter == "openai_images.v1" and _openai_image_references(model)),
        "first_frame": kind == "video",
        "last_frame": adapter == "ark_video.v1"
        or (adapter == "dashscope_video.v1" and "kf2v" in snapshot.get("model_key", "")),
    }
