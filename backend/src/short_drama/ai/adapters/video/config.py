"""视频能力配置与归一化规则。"""

import math
import re
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit

from short_drama.ai.adapters.runninghub.workflow import (
    RUNNINGHUB_WORKFLOW,
)
from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError

OPENAI_VIDEOS = "canvas_openai_videos.v1"


NEWAPI_VIDEO_GENERATIONS = "canvas_newapi_video_generations.v1"


VIDEO_ADAPTERS = frozenset({OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, RUNNINGHUB_WORKFLOW})


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
