"""视频请求输入冻结及供应商能力校验。"""

import math
import re
from copy import deepcopy

from short_drama.ai.adapters.image.agnes_adapters import (
    is_agnes_video,
    validate_agnes_video_structure,
)
from short_drama.ai.adapters.runninghub.workflow import (
    RUNNINGHUB_WORKFLOW,
)
from short_drama.ai.adapters.video.config import (
    NEWAPI_VIDEO_GENERATIONS,
    REFERENCE_GROUPS,
    SAFE_OPERATIONS,
    VIDEO_ADAPTERS,
    _boolean,
    _duration_value,
    _invalid,
    canvas_video_endpoint,
    is_seedance_2,
    is_seedance_25,
    is_seedance_model,
    normalize_canvas_video_config,
)
from short_drama.ai.gateway.transport import validated_url


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
    seedance2 = adapter != RUNNINGHUB_WORKFLOW and is_seedance_2(snapshot.get("model_key", ""))
    seedance25 = adapter != RUNNINGHUB_WORKFLOW and is_seedance_25(snapshot.get("model_key", ""))
    refs = {"maxImages": 9, "maxVideos": 0, "maxAudios": 0}
    operations = {"text_to_video", "image_to_video"}
    if is_agnes_video(snapshot, adapter):
        refs.update(maxImages=5, maxVideos=0, maxAudios=3)
        operations.update({"reference_to_video", "audio_to_video"})
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
    return {
        "references": refs,
        "operations": operations,
        **{key: val for key, val in saved.items() if key not in {"references", "operations"}},
    }


def validate_canvas_video_input(snapshot: dict, request: dict, adapter: str) -> dict:
    value = frozen_video_input(snapshot, request)
    if adapter not in VIDEO_ADAPTERS:
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


def validate_agnes_canvas_video_request(snapshot: dict, request: dict, adapter: str) -> dict:
    if not is_agnes_video(snapshot, adapter):
        _invalid("unsupported_protocol")
    value = validate_canvas_video_input(snapshot, request, adapter)
    canvas_video_endpoint(snapshot["base_url"], "/v1/videos")
    return validate_agnes_video_structure(value, request.get("input") or {})
