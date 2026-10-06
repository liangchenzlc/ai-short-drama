"""固定 BeefTV 视频合同；仅供已冻结的画布请求，不扩大标准模式协议。"""

import math
import re
from copy import deepcopy
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from .transport import validated_url
from .types import GenerationError, GenerationResult

OPENAI_VIDEOS = "canvas_openai_videos.v1"
NEWAPI_VIDEO_GENERATIONS = "canvas_newapi_video_generations.v1"
BEEFAPI_SEEDANCE = "canvas_beefapi_seedance_video.v1"
VIDEO_ADAPTERS = frozenset({OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE})
REFERENCE_GROUPS = (
    ("image", "referenceImages"),
    ("video", "referenceVideos"),
    ("audio", "referenceAudios"),
)
API_PREFIXES = ("/api/plan/v3", "/api/v3", "/api/v1", "/v1beta", "/v1", "/v2", "/v3")
SAFE_OPERATIONS = frozenset(
    {
        "text_to_video",
        "image_to_video",
        "reference_to_video",
        "audio_to_video",
        "extend",
        "inpaint",
        "replace_element",
        "style_transfer",
    }
)


def is_seedance_model(model: str) -> bool:
    """源 IsSeedanceVideoConfig 的模型判断；不从提示词推测。"""
    return isinstance(model, str) and "seedance" in model.lower()


def is_seedance_25(model: str) -> bool:
    base = model.strip().lower().split("/")[-1]
    return base == "seedance-2.5" or base.startswith(
        ("seedance-2.5-", "doubao-seedance-2-5", "doubao-seedance-2.5")
    )


def is_seedance_2(model: str) -> bool:
    return "seedance-2" in model.lower()


def canvas_video_endpoint(base: str, path: str) -> str:
    """源 APIURL 的显式版本优先规则；不改变宿主其他 endpoint。"""
    parts = urlsplit(validated_url(base.strip()))
    base_path = parts.path.rstrip("/")
    prefix = next((p for p in API_PREFIXES if base_path.lower().endswith(p)), "")
    requested = next((p for p in API_PREFIXES if path == p or path.startswith(p + "/")), "")
    if requested:
        final = (
            base_path + path[len(requested) :]
            if prefix == requested
            else base_path.removesuffix(prefix) + path
        )
    else:
        final = base_path + path if prefix else base_path + "/v1" + path
    return urlunsplit((parts.scheme, parts.netloc, final, "", ""))


def _invalid(code: str = "unsupported_parameters") -> None:
    raise GenerationError(code)


def _boolean(value, default=False) -> bool:
    if value is None or value == "":
        return default
    if type(value) is bool:
        return value
    if value in ("true", "false"):
        return value == "true"
    _invalid()


def _duration_value(raw) -> int:
    if isinstance(raw, str) and re.fullmatch(r"[+-]?[0-9]{1,6}", raw.strip()):
        raw = int(raw.strip())
    if type(raw) is not int or (raw != -1 and not 1 <= raw <= 3600):
        _invalid()
    return raw


def _resolution_name(profile: dict, raw: str) -> str:
    requested = raw.strip().lower()
    if requested in {"", "auto", "default", "medium", "high"}:
        return ""
    options = profile.get("resolutions") or []
    if not isinstance(options, list) or any(not isinstance(value, str) for value in options):
        _invalid()
    candidates = {requested, _normalize_video_resolution(requested).lower()}
    if requested == "4k":
        candidates.add("2160p")
    if requested in {"2160", "2160p"}:
        candidates.add("4k")
    return next((value.strip() for value in options if value.strip().lower() in candidates), "")


def _normalize_video_resolution(raw: str) -> str:
    raw = raw.strip()
    if raw in {"", "auto", "medium", "high"}:
        return "720p"
    if raw == "low":
        return "480p"
    if raw.lower() == "4k":
        return "2160p"
    if raw.lower() == "2k":
        return "1440p"
    return raw if raw.endswith("p") else raw + "p"


def _ratio_allowed(options: list[str], raw: str) -> bool:
    normalized = raw.strip().lower().replace("×", "x")
    if any(option.strip().lower() == normalized for option in options):
        return True
    try:
        width, height = map(float, normalized.split("x"))
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            return False
        actual = width / height
        for option in options:
            if ":" not in option:
                continue
            left, right = map(float, option.strip().split(":"))
            if left > 0 and right > 0 and abs(left / right - actual) / (left / right) < 0.01:
                return True
    except (ValueError, TypeError, OverflowError):
        return False
    return False


def normalize_canvas_video_config(snapshot: dict, request: dict) -> dict:
    """从可信能力快照补源默认值及校验选项；不修改用户原请求。"""
    original = request.get("canvas_request") or {}
    inputs = original.get("input") or {} if isinstance(original, dict) else None
    config = inputs.get("config") or {} if isinstance(inputs, dict) else None
    profile = snapshot.get("canvas_video_capability") or {}
    if not isinstance(config, dict) or not isinstance(profile, dict):
        _invalid()
    config = deepcopy(config)
    options = inputs.get("capabilityOptions") or {}
    if not isinstance(options, dict):
        _invalid()
    aliases = {"duration": "videoSeconds", "aspectRatio": "size", "resolution": "vquality"}
    allowed = {"videoSeconds", "size", "vquality", "videoGenerateAudio", "videoWatermark"}
    for key, raw in options.items():
        if not isinstance(key, str):
            _invalid()
        key = aliases.get(key.strip(), key.strip())
        if key not in allowed or type(raw) not in {str, int, float, bool}:
            _invalid()
        if type(raw) is float:
            if not math.isfinite(raw):
                _invalid()
            raw = str(int(raw)) if raw.is_integer() else repr(raw)
        elif type(raw) is bool:
            raw = str(raw).lower()
        elif type(raw) is int:
            raw = str(raw)
        if key in {"videoGenerateAudio", "videoWatermark"}:
            _boolean(raw)
        config[key] = raw
    if not profile:
        return config
    duration = profile.get("duration") or {}
    if not isinstance(duration, dict):
        _invalid()

    def fill(key, value):
        raw = config.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            config[key] = str(value).lower() if isinstance(value, bool) else str(value)

    if profile.get("durationSupported", True) and "default" in duration:
        fill("videoSeconds", duration["default"])
    for key, name in (("size", "defaultRatio"), ("vquality", "defaultResolution")):
        if name in profile:
            fill(key, profile[name])
    for key, name in (("videoGenerateAudio", "generateAudio"), ("videoWatermark", "watermark")):
        option = profile.get(name) or {}
        if not isinstance(option, dict):
            _invalid()
        if "default" in option:
            fill(key, option["default"])
    for key in ("videoSeconds", "size", "vquality"):
        if isinstance(config.get(key), str):
            config[key] = config[key].strip()
    if "videoSeconds" in config and duration:
        seconds = _duration_value(config["videoSeconds"])
        if duration.get("selection") == "enum":
            values = duration.get("values", [])
            if (
                not isinstance(values, list)
                or any(type(value) is not int for value in values)
                or seconds not in values
            ):
                _invalid()
        elif duration.get("selection") == "range":
            lower, upper, step = (
                duration.get("min", 1),
                duration.get("max", 3600),
                duration.get("step", 1),
            )
            if (
                any(type(value) is not int for value in (lower, upper, step))
                or step <= 0
                or seconds < lower
                or seconds > upper
                or (seconds - lower) % step
            ):
                _invalid()
        else:
            _invalid()
    if "ratios" in profile:
        ratios = profile["ratios"]
        raw = config.get("size", "")
        if (
            not isinstance(ratios, list)
            or any(not isinstance(value, str) for value in ratios)
            or not isinstance(raw, str)
        ):
            _invalid()
        if raw and not _ratio_allowed(ratios, raw):
            _invalid()
    raw = config.get("vquality", "")
    if not isinstance(raw, str):
        _invalid()
    resolutions = profile.get("resolutions") or []
    if not isinstance(resolutions, list) or any(
        not isinstance(value, str) for value in resolutions
    ):
        _invalid()
    if snapshot.get("canvas_channel_key") == "beefapi" and len(resolutions) == 1:
        raw = _resolution_name(profile, resolutions[0])
    declared = _resolution_name(profile, raw)
    if (
        resolutions
        and raw.lower() not in {"", "auto", "default", "medium", "high"}
        and not declared
    ):
        _invalid()
    if "vquality" in config or declared or raw:
        config["vquality"] = declared or raw
    return config


def frozen_video_input(snapshot: dict, request: dict) -> dict:
    original = request.get("canvas_request") or {}
    if not isinstance(original, dict):
        _invalid("invalid_canvas_video")
    value = original.get("input") or {}
    parameters = request.get("canvas_parameters") or {}
    source, core_input = request.get("source") or {}, request.get("input") or {}
    if not all(isinstance(item, dict) for item in (value, parameters, source, core_input)) or set(
        parameters
    ) - {"mode", "generate_audio", "watermark"}:
        _invalid("invalid_canvas_video")
    if (
        snapshot.get("service_type") != "video"
        or source.get("scene") not in {"canvas_node", "canvas_model_test"}
        or value.get("mode") != "video"
        or parameters.get("mode") != "video"
    ):
        _invalid("invalid_canvas_video")
    if (
        not isinstance(value.get("prompt"), str)
        or not value["prompt"].strip()
        or value.get("prompt") != core_input.get("prompt")
    ):
        _invalid("invalid_canvas_video")
    result = deepcopy(value)
    config, metadata = (
        normalize_canvas_video_config(snapshot, request),
        result.get("metadata") or {},
    )
    result["config"] = config
    if not isinstance(config, dict) or not isinstance(metadata, dict):
        _invalid()
    operation = metadata.get("videoEditOperation") or original.get("operation") or "text_to_video"
    if operation not in SAFE_OPERATIONS:
        _invalid("unsupported_video_operation")
    duration = _duration_value(config.get("videoSeconds", "6"))
    if duration == -1 and -1 not in (
        (snapshot.get("canvas_video_capability") or {}).get("duration") or {}
    ).get("values", []):
        _invalid()
    ratio, resolution = config.get("size", "16:9"), config.get("vquality", "")
    checked_ratios = "ratios" in (snapshot.get("canvas_video_capability") or {})
    if not isinstance(ratio, str) or (
        not checked_ratios
        and not re.fullmatch(
            r"(?:auto|adaptive|[1-9][0-9]{0,4}(?:\.[0-9]+)?[:x×][1-9][0-9]{0,4}(?:\.[0-9]+)?)?",
            ratio,
            flags=re.IGNORECASE,
        )
    ):
        _invalid()
    if (
        not isinstance(resolution, str)
        or len(resolution) > 64
        or any(c.isspace() or ord(c) < 32 for c in resolution)
    ):
        _invalid()
    result.update(
        operation=operation,
        duration=duration,
        ratio=ratio,
        resolution=resolution,
        generate_audio=_boolean(
            config.get("videoGenerateAudio")
            if config.get("videoGenerateAudio") is not None
            else parameters.get("generate_audio"),
            is_seedance_model(snapshot.get("model_key", "")),
        ),
        watermark=_boolean(
            config.get("videoWatermark")
            if config.get("videoWatermark") is not None
            else parameters.get("watermark"),
            False,
        ),
    )
    for _kind, group in REFERENCE_GROUPS:
        items = result.get(group) or []
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            _invalid("invalid_reference")
        for item in items:
            local = item.get("storageKey")
            remote = item.get("url")
            if local:
                if not isinstance(local, str) or not re.fullmatch(r"resource:[1-9][0-9]*", local):
                    _invalid("invalid_reference")
            elif (
                item.get("dataUrl")
                and isinstance(item["dataUrl"], str)
                and item["dataUrl"].startswith("data:")
            ):
                pass
            elif not isinstance(remote, str) or not remote.strip():
                _invalid("invalid_reference")
            elif remote.startswith("asset://"):
                if not re.fullmatch(r"asset://[A-Za-z0-9_-]+", remote):
                    _invalid("invalid_reference")
            else:
                validated_url(remote, query=True)
        result[group] = items
    return result


def _profile(snapshot: dict, adapter: str) -> dict:
    seedance2 = is_seedance_2(snapshot.get("model_key", ""))
    seedance25 = is_seedance_25(snapshot.get("model_key", ""))
    refs = {"maxImages": 9, "maxVideos": 0, "maxAudios": 0}
    operations = {"text_to_video", "image_to_video"}
    if adapter == NEWAPI_VIDEO_GENERATIONS:
        refs.update(maxVideos=3, maxAudios=3)
        operations.add("reference_to_video")
    if seedance2:
        refs.update(
            maxImages=30 if seedance25 else 9,
            maxVideos=10 if seedance25 else 3,
            maxAudios=10 if seedance25 else 3,
        )
        operations.add("reference_to_video")
        if seedance25:
            operations.add("audio_to_video")
    saved = snapshot.get("canvas_video_capability") or {}
    if saved:
        if not isinstance(saved, dict) or not isinstance(saved.get("references", {}), dict):
            _invalid()
        refs.update(saved.get("references", {}))
        if saved.get("operations"):
            if not isinstance(saved["operations"], list) or any(
                not isinstance(op, str) for op in saved["operations"]
            ):
                _invalid()
            operations = set(saved["operations"])
    if (
        snapshot.get("canvas_channel_key") == "beefapi"
        and snapshot.get("model_key", "").lower() == "wan3.0-video"
    ):
        refs.update(maxImages=1, maxVideos=0, maxAudios=0)
        operations = {"text_to_video", "image_to_video"}
    return {
        "references": refs,
        "operations": operations,
        **{key: val for key, val in saved.items() if key not in {"references", "operations"}},
    }


def validate_canvas_video_input(snapshot: dict, request: dict, adapter: str) -> dict:
    value = frozen_video_input(snapshot, request)
    if adapter not in VIDEO_ADAPTERS:
        _invalid("unsupported_protocol")
    if adapter == BEEFAPI_SEEDANCE and (
        snapshot.get("canvas_channel_key") != "beefapi"
        or not is_seedance_model(snapshot.get("model_key", ""))
    ):
        _invalid("unsupported_protocol")
    profile = _profile(snapshot, adapter)
    if value["operation"] not in profile["operations"]:
        _invalid("unsupported_video_operation")
    refs = profile["references"]
    for kind, group in REFERENCE_GROUPS:
        count_key = {"image": "maxImages", "video": "maxVideos", "audio": "maxAudios"}[kind]
        maximum = refs.get(count_key, 0)
        if type(maximum) is not int or len(value[group]) > maximum:
            _invalid("reference_limit")
        byte_key = {"image": "maxImageBytes", "video": "maxVideoBytes", "audio": "maxAudioBytes"}[
            kind
        ]
        prefix = kind.capitalize()
        for key, constraint in refs.items():
            if (
                not isinstance(key, str)
                or type(constraint) not in {int, float}
                or not math.isfinite(constraint)
                or constraint < 0
            ):
                _invalid()
        total_duration = 0
        for item in value[group]:
            if item.get("bytes") is not None and (
                type(item["bytes"]) is not int
                or item["bytes"] <= 0
                or (refs.get(byte_key) and item["bytes"] > refs[byte_key])
            ):
                _invalid("invalid_reference")
            width, height = item.get("width") or 0, item.get("height") or 0
            if type(width) is not int or type(height) is not int or width < 0 or height < 0:
                _invalid("invalid_reference")
            opaque = not item.get("storageKey") and str(item.get("url") or "").startswith(
                "asset://"
            )
            if (
                kind == "video"
                and refs.get("minVideoPixels", 0)
                and (not width or not height)
                and not opaque
            ):
                _invalid("invalid_reference")
            if width and height:
                for attribute, measured in (
                    ("Width", width),
                    ("Height", height),
                    ("Aspect", width / height),
                    ("Pixels", width * height),
                ):
                    minimum, maximum_geometry = (
                        refs.get(f"min{prefix}{attribute}", 0),
                        refs.get(f"max{prefix}{attribute}", 0),
                    )
                    if (minimum and measured < minimum) or (
                        maximum_geometry and measured > maximum_geometry
                    ):
                        _invalid("invalid_reference")
            if kind in {"video", "audio"}:
                milliseconds = item.get("durationMs") or 0
                if type(milliseconds) is not int or milliseconds < 0:
                    _invalid("invalid_reference")
                total_duration += milliseconds
                minimum, maximum_duration = (
                    refs.get(f"min{prefix}DurationSeconds", 0),
                    refs.get(f"max{prefix}DurationSeconds", 0),
                )
                if not (opaque and milliseconds == 0) and (
                    (minimum and milliseconds < round(minimum * 1000))
                    or (maximum_duration and milliseconds > round(maximum_duration * 1000))
                ):
                    _invalid("invalid_reference")
        if (
            kind in {"video", "audio"}
            and refs.get(f"max{prefix}TotalDurationSeconds", 0)
            and total_duration > refs[f"max{prefix}TotalDurationSeconds"] * 1000
        ):
            _invalid("invalid_reference")
    if len(value["referenceImages"]) < refs.get("minImages", 0):
        _invalid("reference_limit")
    if refs.get("promptMaxChars", 0) and len(value["prompt"].strip()) > refs["promptMaxChars"]:
        _invalid("unsupported_parameters")
    if (
        value["referenceAudios"]
        and not value["referenceImages"]
        and not value["referenceVideos"]
        and "audio_to_video" not in profile["operations"]
    ):
        _invalid("unsupported_video_operation")
    duration = profile.get("duration") or {}
    if not isinstance(duration, dict):
        _invalid()
    if duration.get("selection") == "enum" and value["duration"] not in duration.get("values", []):
        _invalid()
    if duration.get("selection") == "range" and (
        value["duration"] < duration.get("min", 1) or value["duration"] > duration.get("max", 3600)
    ):
        _invalid()
    start, end = (
        (value.get("metadata") or {}).get("videoStartFrameNodeId"),
        (value.get("metadata") or {}).get("videoEndFrameNodeId"),
    )
    for identity in (start, end):
        if identity and sum(item.get("id") == identity for item in value["referenceImages"]) != 1:
            _invalid("invalid_reference")
    return value


def _supports(snapshot: dict, name: str) -> bool:
    profile = snapshot.get("canvas_video_capability")
    if not profile:
        return True
    option = profile.get(name) or {}
    if not isinstance(option, dict) or type(option.get("supported", False)) is not bool:
        _invalid()
    return option.get("supported", False)


def image_roles(value: dict, model: str) -> list[str]:
    metadata = value.get("metadata") or {}
    start, end = metadata.get("videoStartFrameNodeId"), metadata.get("videoEndFrameNodeId")
    result = []
    for index, item in enumerate(value["referenceImages"]):
        role = "reference_image"
        if value["operation"] != "reference_to_video":
            if start and item.get("id") == start:
                role = "first_frame"
            elif end and item.get("id") == end:
                role = "last_frame"
            elif (
                is_seedance_25(model)
                and not start
                and not end
                and not value["referenceVideos"]
                and not value["referenceAudios"]
            ):
                if index == 0:
                    role = "first_frame"
                elif index == 1 and len(value["referenceImages"]) == 2:
                    role = "last_frame"
        result.append(role)
    return result


def _media_value(item: dict) -> str:
    return item.get("storageKey") or item.get("dataUrl") or item.get("url") or ""


def build_canvas_video_submission(
    snapshot: dict, request: dict, adapter: str
) -> tuple[str, dict, dict]:
    value = validate_canvas_video_input(snapshot, request, adapter)
    model = snapshot["model_key"]
    body = {"model": model, "prompt": value["prompt"]}
    if adapter == OPENAI_VIDEOS:
        body["seconds"] = str(value["duration"])
        if value["ratio"]:
            body["size"] = value["ratio"]
        if value["resolution"]:
            body["resolution_name"] = value["resolution"]
        variants = snapshot.get("canvas_video_variants")
        if variants not in (None, "", False, 0):
            if type(variants) not in {str, int, float, bool} or (
                type(variants) is float and not math.isfinite(variants)
            ):
                _invalid()
            body["variants"] = variants
        if value["referenceImages"]:
            body["input_reference"] = value["referenceImages"][0]
        path = "/v1/videos"
    elif adapter == NEWAPI_VIDEO_GENERATIONS:
        managed_inline = (
            snapshot.get("canvas_channel_key") == "beefapi" and model.lower() == "wan3.0-video"
        )
        if not managed_inline and any(
            item.get("storageKey")
            or item.get("dataUrl")
            or item.get("url", "").startswith("asset://")
            for _, group in REFERENCE_GROUPS
            for item in value[group]
        ):
            _invalid("reference_media_requires_url")
        body.update(
            seconds=str(value["duration"]),
            aspect_ratio=value["ratio"] or "16:9",
            generate_audio=value["generate_audio"],
        )
        if value["resolution"]:
            body["resolution"] = value["resolution"]
        for kind, group in REFERENCE_GROUPS:
            if value[group]:
                body[f"{kind}_urls"] = [_media_value(item) for item in value[group]]
        path = "/v1/video/generations"
    else:
        body["prompt"] = value["prompt"].strip()
        value["resolution"] = _resolution_name(
            snapshot.get("canvas_video_capability") or {}, value["resolution"]
        ) or _normalize_video_resolution(value["resolution"])
        roles = image_roles(value, model)
        reference_mode = (
            value["operation"] == "reference_to_video"
            or len(value["referenceImages"]) > 1
            or bool(value["referenceVideos"] or value["referenceAudios"])
            or (is_seedance_25(model) and bool(value["referenceImages"]))
        )
        if reference_mode:
            ratio, seconds = value["ratio"], value["duration"]
            if is_seedance_25(model) and (
                any(role in {"first_frame", "last_frame"} for role in roles)
                or (
                    value["referenceVideos"]
                    and value["operation"]
                    in {"extend", "inpaint", "replace_element", "style_transfer"}
                )
            ):
                ratio = "adaptive"
            if (
                is_seedance_25(model)
                and value["referenceVideos"]
                and value["operation"] in {"inpaint", "replace_element", "style_transfer"}
            ):
                seconds = -1
            content = []
            for item, role in zip(value["referenceImages"], roles, strict=True):
                content.append(
                    {"type": "image_url", "image_url": {"url": _media_value(item)}, "role": role}
                )
            for kind, group in REFERENCE_GROUPS[1:]:
                for item in value[group]:
                    content.append(
                        {
                            "type": f"{kind}_url",
                            f"{kind}_url": {"url": _media_value(item)},
                            "role": f"reference_{kind}",
                        }
                    )
            metadata = {"ratio": ratio}
            if _supports(snapshot, "generateAudio"):
                metadata["generate_audio"] = value["generate_audio"]
            task_type = {
                "reference_to_video": "reference",
                "extend": "extend",
                "inpaint": "edit",
                "replace_element": "edit",
                "style_transfer": "edit",
            }.get(value["operation"])
            if (
                is_seedance_25(model)
                and task_type
                and (task_type == "reference" or value["referenceVideos"])
            ):
                metadata["omni_reference_task_type"] = task_type
            if _supports(snapshot, "watermark"):
                metadata["watermark"] = value["watermark"]
            body.update(
                seconds=str(seconds),
                resolution=value["resolution"],
                content=content,
                metadata=metadata,
            )
        else:
            body.update(duration=value["duration"], resolution=value["resolution"])
            if _supports(snapshot, "generateAudio"):
                body["generate_audio"] = value["generate_audio"]
            if value["referenceImages"]:
                body["image"] = {"url": _media_value(value["referenceImages"][0])}
            else:
                body["aspect_ratio"] = value["ratio"]
        path = "/videos"
    return canvas_video_endpoint(snapshot["base_url"], path), {}, body


def canvas_video_poll_endpoint(snapshot: dict, task_id: str, adapter: str) -> str:
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", task_id):
        _invalid("invalid_provider_task")
    route = (
        "/v1/video/generations"
        if adapter == NEWAPI_VIDEO_GENERATIONS
        else "/videos"
        if adapter == BEEFAPI_SEEDANCE
        else "/v1/videos"
    )
    return canvas_video_endpoint(snapshot["base_url"], route + "/" + quote(task_id, safe=""))


def _path(body: dict, path: str):
    value = body
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first(body: dict, paths: tuple[str, ...]):
    return next(
        (value for path in paths if (value := _path(body, path)) not in (None, "", False)), None
    )


def parse_canvas_video_result(
    body: dict, adapter: str, *, submitted: bool, task_id=None
) -> GenerationResult:
    if not isinstance(body, dict):
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )
    seedance = adapter == BEEFAPI_SEEDANCE
    if seedance and isinstance(body.get("data"), dict):
        body = body["data"]
    id_paths = (
        ("data.task_id", "data.taskId", "task_id", "taskId", "data.id", "id")
        if adapter == NEWAPI_VIDEO_GENERATIONS
        else ("id", "task_id", "taskId", "data.id")
    )
    identity = _first(body, id_paths) or task_id
    if type(identity) is int:
        identity = str(identity)
    if identity is not None and (
        not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", identity)
    ):
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )
    status = (
        _first(body, ("metadata.generation_status", "status"))
        if seedance
        else _first(body, ("status", "state", "data.status"))
    )
    status = status.strip().lower() if isinstance(status, str) else "pending"
    urls = (
        ("video_url", "metadata.url")
        if seedance
        else (
            (
                "data.result_url",
                "data.video_url",
                "data.output_url",
                "data.url",
                "data.metadata.url",
                "data.data.video_url",
                "data.data.output_url",
                "data.data.result_url",
                "data.data.url",
                "data.data.metadata.url",
                "video_url",
                "videoUrl",
                "result_url",
                "output_url",
                "url",
                "metadata.url",
                "output.url",
            )
            if adapter == NEWAPI_VIDEO_GENERATIONS
            else ("url", "video_url", "output.url")
        )
    )
    result_url = _first(body, urls)
    failed = status in (
        {"failed", "cancelled", "expired"}
        if seedance
        else {"failed", "failure", "error", "expired", "cancelled", "canceled", "aborted"}
    ) or bool(_path(body, "error.code"))
    if failed:
        return GenerationResult(
            "failed",
            adapter,
            provider_task_id=identity,
            error={
                "code": "provider_failed",
                "message": "The provider could not complete generation.",
            },
        )
    completed = status in (
        {"completed", "succeeded"}
        if seedance
        else {"succeeded", "success", "completed", "complete", "done", "task_status_succeed"}
    )
    if (
        not seedance
        and result_url
        and status
        not in {"running", "processing", "in_progress", "executing", "task_status_running"}
    ):
        completed = True
    if completed:
        if result_url:
            if not isinstance(result_url, str):
                raise GenerationError("invalid_response", accepted_unknown=submitted)
            return GenerationResult(
                "succeeded",
                adapter,
                provider_task_id=identity,
                outputs=[{"url": result_url, "media_type": "video"}],
            )
        if adapter == NEWAPI_VIDEO_GENERATIONS:
            raise GenerationError("missing_output", accepted_unknown=submitted)
        if not identity:
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        return GenerationResult(
            "succeeded",
            adapter,
            provider_task_id=identity,
            outputs=[{"canvas_content_task_id": identity, "media_type": "video"}],
        )
    if not identity:
        raise GenerationError(
            "invalid_response", accepted_unknown=submitted, retryable=not submitted
        )
    return GenerationResult("submitted", adapter, provider_task_id=identity)


def complete_video_urls(snapshot: dict, result: GenerationResult) -> GenerationResult:
    base = urlsplit(snapshot["base_url"])
    for output in result.outputs:
        if "canvas_content_task_id" in output:
            identity = output.pop("canvas_content_task_id")
            route = "/videos/" if result.adapter == BEEFAPI_SEEDANCE else "/v1/videos/"
            output["url"] = canvas_video_endpoint(
                snapshot["base_url"], route + quote(identity, safe="") + "/content"
            )
        raw = output["url"]
        if raw.startswith("/") and not raw.startswith("//"):
            raw = urljoin(snapshot["base_url"], raw)
        target = urlsplit(validated_url(raw, query=True))
        if (
            base.scheme == target.scheme == "https"
            and snapshot.get("canvas_channel_key") == "beefapi"
            and (target.hostname == "beefapi.com" or target.hostname.endswith(".beefapi.com"))
        ):
            raw = urlunsplit((base.scheme, base.netloc, target.path, target.query, ""))
        output["url"] = raw
    return result


def canvas_video_resolved_parameters(body: dict) -> dict:
    result = {
        key: value
        for key, value in body.items()
        if key
        in {
            "seconds",
            "duration",
            "size",
            "aspect_ratio",
            "resolution",
            "resolution_name",
            "generate_audio",
            "watermark",
            "variants",
        }
    }
    if isinstance(body.get("metadata"), dict):
        result["metadata"] = {
            key: value
            for key, value in body["metadata"].items()
            if key in {"ratio", "generate_audio", "watermark", "omni_reference_task_type"}
        }
    return result
