"""原版 catalog_fetch/catalog_video 投影；只返回验证后的公开模型元数据。"""

import json
import math

MAX_MODELS = 2000
PARAMETER_KEYS = {
    "aspect_ratio": "aspectRatio",
    "duration_seconds": "durationSeconds",
    "resolution": "resolution",
}
REFERENCE_INTS = {
    "promptMaxChars": (1, 1000000),
    **{key: (0, 100) for key in ("minImages", "maxImages", "maxVideos", "maxAudios")},
    **{
        key: (0, 2**31 - 1)
        for key in (
            "maxVideoDurationSeconds",
            "minVideoDurationSeconds",
            "maxAudioDurationSeconds",
            "maxAudioTotalDurationSeconds",
            "maxVideoTotalDurationSeconds",
            "minImageWidth",
            "maxImageWidth",
            "minImageHeight",
            "maxImageHeight",
            "minVideoWidth",
            "maxVideoWidth",
            "minVideoHeight",
            "maxVideoHeight",
        )
    },
    **{
        key: (0, 2**63 - 1)
        for key in (
            "maxImageBytes",
            "maxVideoBytes",
            "maxAudioBytes",
            "minImagePixels",
            "maxImagePixels",
            "minVideoPixels",
            "maxVideoPixels",
        )
    },
}
REFERENCE_FLOATS = {
    "minImageAspect",
    "maxImageAspect",
    "minVideoAspect",
    "maxVideoAspect",
    "minAudioDurationSeconds",
}


def _text(value, limit=65536) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError
    return value.strip()


def _strings(value, *, allow_empty=True) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError
    result = list(dict.fromkeys(item for raw in value if (item := _text(raw))))
    if not allow_empty and not result:
        raise ValueError
    return result


def _whole(value, minimum=0, maximum=2**63 - 1) -> int:
    if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value:
        raise ValueError
    value = int(value)
    if not minimum <= value <= maximum:
        raise ValueError
    return value


def normalize_video_capabilities(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        json.dumps(value, allow_nan=False)
        raw_refs, duration = value["references"], value["duration"]
        if not isinstance(raw_refs, dict) or not isinstance(duration, dict):
            raise ValueError
        refs = {}
        for key, bounds in REFERENCE_INTS.items():
            if key in raw_refs:
                refs[key] = _whole(raw_refs[key], *bounds)
        for key in REFERENCE_FLOATS:
            if key in raw_refs:
                number = raw_refs[key]
                if type(number) not in (float, int) or not math.isfinite(number) or number < 0:
                    raise ValueError
                refs[key] = number
        for low, high in (
            ("minImages", "maxImages"),
            ("minVideoDurationSeconds", "maxVideoDurationSeconds"),
            ("minAudioDurationSeconds", "maxAudioDurationSeconds"),
        ):
            if (
                low in refs
                and high in refs
                and (high == "maxImages" or refs[high] > 0)
                and refs[low] > refs[high]
            ):
                raise ValueError
        selection = _text(duration["selection"])
        default = _whole(duration["default"], -(2**63), 2**63 - 1)
        normalized_duration = {"selection": selection, "default": default}
        if selection == "range":
            low, high, step = (_whole(duration[key], 1, 3600) for key in ("min", "max", "step"))
            if high < low or not low <= default <= high or (default - low) % step:
                raise ValueError
            normalized_duration.update(min=low, max=high, step=step)
        elif selection == "enum":
            raw_values = duration["values"]
            if not isinstance(raw_values, list) or not 1 <= len(raw_values) <= 100:
                raise ValueError
            values = [_whole(item, -1, 3600) for item in raw_values]
            if 0 in values or len(set(values)) != len(values) or default not in values:
                raise ValueError
            normalized_duration["values"] = values
        else:
            raise ValueError
        video = {"references": refs, "duration": normalized_duration}
        for key in ("generateAudio", "watermark"):
            item = value[key]
            if not isinstance(item, dict) or any(
                type(item.get(field)) is not bool for field in ("supported", "default")
            ):
                raise ValueError
            video[key] = {field: item[field] for field in ("supported", "default")}
        for key, default_key, allow_empty in (
            ("ratios", "defaultRatio", True),
            ("resolutions", "defaultResolution", True),
            ("operations", "defaultOperation", False),
        ):
            options, default = (
                _strings(value[key], allow_empty=allow_empty),
                _text(value[default_key]),
            )
            if options and default not in options or not options and default:
                raise ValueError
            video[key], video[default_key] = options, default
        if "durationSupported" in value:
            if type(value["durationSupported"]) is not bool:
                raise ValueError
            video["durationSupported"] = value["durationSupported"]
        return video
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _parameters(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError
    return {
        target: text for key, target in PARAMETER_KEYS.items() if (text := _text(value.get(key)))
    }


def _options(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError
    result = {}
    for key, target in PARAMETER_KEYS.items():
        raw_options = value.get(key, [])
        if not isinstance(raw_options, list) or len(raw_options) > 100:
            raise ValueError
        choices, seen = [], set()
        for raw in raw_options:
            if not isinstance(raw, dict):
                raise ValueError
            text, label = _text(raw.get("value")), _text(raw.get("label"))
            if text and text not in seen:
                seen.add(text)
                choices.append({"value": text, **({"label": label} if label else {})})
        if choices:
            result[target] = choices
    return result


def parse_catalog(payload, api_format: str, secrets: list[str]) -> list[dict]:
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or payload.get("code") not in (None, 0)
    ):
        raise ValueError
    raw_models = payload.get("models" if api_format == "gemini" else "data")
    if not isinstance(raw_models, list) or len(raw_models) > MAX_MODELS:
        raise ValueError
    models = {}
    for raw in raw_models:
        if not isinstance(raw, dict):
            raise ValueError
        identifier = _text(raw.get("id") or raw.get("name"), 255).removeprefix("models/")
        if not identifier or identifier in models:
            continue
        item = {"id": identifier}
        if display_name := _text(raw.get("display_name")):
            item["displayName"] = display_name
        if (model_type := _text(raw.get("model_type")).lower()) in {
            "text",
            "image",
            "video",
            "audio",
        }:
            item["modelType"] = model_type
        if endpoints := _strings(raw.get("supported_endpoint_types", [])):
            item["supportedEndpointTypes"] = endpoints
        if parameters := _parameters(raw.get("default_parameters", {})):
            item["defaultParameters"] = parameters
        if options := _options(raw.get("options", {})):
            item["options"] = options
        if "supports_images" in raw:
            if type(raw["supports_images"]) is not bool:
                raise ValueError
            item["supportsImages"] = raw["supports_images"]
        for key, target in (("min_images", "minImages"), ("max_images", "maxImages")):
            if key in raw:
                item[target] = _whole(raw[key], 0, 100)
        if "minImages" in item and "maxImages" in item and item["minImages"] > item["maxImages"]:
            raise ValueError
        if video := normalize_video_capabilities(raw.get("video_capabilities")):
            item["videoCapabilities"] = video
            item["videoCapabilitiesVersion"] = _text(raw.get("video_capabilities_version"), 128)
        rendered = json.dumps(item, ensure_ascii=False, allow_nan=False)
        if any(secret and secret in rendered for secret in secrets):
            continue
        models[identifier] = item
    return [models[key] for key in sorted(models)]
