"""RunningHub 字段值校验、解析及 nodeInfo 构造。"""

from short_drama.ai.adapters.runninghub.field_sources import (
    MEDIA_SOURCES,
    PROMPT_SOURCES,
    _invalid,
    is_link,
    normalized_source,
    prompt_fallback,
    unsafe_field,
)
from short_drama.ai.adapters.runninghub.values import (
    aspect_ratio,
    dimensions,
    field_options,
    numeric,
    option,
    ratio_value,
    scalar,
    selected_value,
)
from short_drama.ai.types import GenerationError
from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubField


def validate_value(field: RunningHubField, value):
    field_type = field.field_type.strip().upper()
    if field_type in {"IMAGE", "VIDEO", "AUDIO"}:
        # 上传回执的文件名不受上游静态文件列表限制。
        return
    if field_type in {"NUMBER", "FLOAT", "INTEGER", "INT", "SLIDER"}:
        number, lower, upper, step = (
            numeric(value),
            numeric(field.min),
            numeric(field.max),
            numeric(field.step),
        )
        if (
            number is None
            or lower is not None
            and upper is not None
            and lower > upper
            or step is not None
            and step <= 0
        ):
            _invalid()
        if lower is not None and number < lower or upper is not None and number > upper:
            _invalid()
        if step is not None:
            steps = (number - (lower or 0)) / step
            if abs(steps - round(steps)) > 1e-7:
                _invalid()
    if field_type in {"BOOL", "BOOLEAN"} and not (
        type(value) is bool or isinstance(value, str) and value.strip().lower() in {"true", "false"}
    ):
        _invalid()

    def range_option(item):
        return isinstance(item, dict) and (
            bool(item.keys() & {"min", "max", "step", "minValue", "maxValue", "stepValue"})
            or range_option(item.get("range"))
        )

    options = [
        option(item).strip()
        for item in field_options(field)
        if not range_option(item) and option(item).strip()
    ]
    if options and scalar(value).strip() not in options:
        _invalid()


def resolve_value(field: RunningHubField, files: dict, inputs: dict):
    source, config = normalized_source(field), inputs.get("config") or {}
    if not source and field.field_name.strip().lower() == "aspect_ratio":
        requested = scalar(config.get("size")).strip()
        if requested and aspect_ratio(requested) != "1:1":
            value = ratio_value(field, requested)
            if value.strip():
                return value
        raw = option(field.field_value if field.field_value is not None else field.value).strip()
        if raw:
            return ratio_value(field, raw)
    if source:
        if source in PROMPT_SOURCES:
            value = inputs.get("prompt", "")
        elif source in {
            "aspectratio",
            "ratio",
            "imageaspectratio",
            "imageratio",
            "videoaspectratio",
            "videoratio",
        }:
            value = ratio_value(field, scalar(config.get("size")))
        elif source in {
            "sizewidth",
            "width",
            "imagewidth",
            "videowidth",
            "sizeheight",
            "height",
            "imageheight",
            "videoheight",
        }:
            pixels = dimensions(
                inputs["mode"], scalar(config.get("size")), scalar(config.get("vquality"))
            )
            value = str(pixels[1 if "height" in source else 0]) if pixels else ""
        elif (
            source == "resolution"
            and inputs["mode"] == "video"
            or source in {"vquality", "videoquality"}
        ):
            value = selected_value(field, scalar(config.get("vquality")))
        elif source in {"videoseconds", "duration"}:
            value = selected_value(field, scalar(config.get("videoSeconds")), duration=True)
        elif source in MEDIA_SOURCES:
            group = MEDIA_SOURCES[source]
            index = field.image_order - 1 if field.image_order > 0 else field.source_index
            references = [inputs.get("mask")] if group == "mask" else inputs.get(group, [])
            if index < 0 or index >= len(references) or not references[index]:
                return None
            media = references[index]
            value = files.get(media.get("id") or media.get("storageKey") or media.get("url"))
            if not value:
                raise GenerationError("workflow_reference_not_uploaded")
        else:
            key = {
                "size": "size",
                "imagesize": "size",
                "resolution": "size",
                "quality": "quality",
                "count": "count",
                "batch": "count",
                "batchsize": "count",
                "videogenerateaudio": "videoGenerateAudio",
                "generateaudio": "videoGenerateAudio",
                "videowatermark": "videoWatermark",
                "watermark": "videoWatermark",
                "audioformat": "audioFormat",
                "systemprompt": "systemPrompt",
                "transparentbackground": "transparentBackground",
                "audiovoice": "audioVoice",
                "voice": "audioVoice",
                "audiospeed": "audioSpeed",
                "audioinstructions": "audioInstructions",
            }.get(source)
            if key is None:
                _invalid()
            value = config.get(key, "")
        return value if scalar(value).strip() else None
    if field.random_enabled:
        # 随机值只能在准入时冻结；验证、重放和 Worker 不能重新抽样。
        _invalid()
    return field.field_value if field.field_value is not None else field.value


def node_info(
    fields: list[RunningHubField], files: dict, inputs: dict, workflow: dict
) -> list[dict]:
    result = []
    for field in fields:
        if (
            field.enabled is False
            or not field.node_id.strip()
            or not field.field_name.strip()
            or unsafe_field(field, workflow)
        ):
            continue
        value = resolve_value(field, files, inputs)
        if value is None:
            if field.required:
                raise GenerationError("workflow_required_field_missing")
            continue
        validate_value(field, value)
        node = workflow.get(field.node_id.strip())
        original_inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if isinstance(original_inputs, dict):
            original = original_inputs.get(field.field_name.strip())
            if is_link(original):
                continue
            if (
                not normalized_source(field)
                and not field.random_enabled
                and field.field_name.strip() in original_inputs
                and scalar(value) == scalar(original)
            ):
                continue
        result.append(
            {"nodeId": field.node_id, "fieldName": field.field_name, "fieldValue": scalar(value)}
        )
    if not any(
        field.enabled is not False
        and field.node_id.strip()
        and field.field_name.strip()
        and normalized_source(field) in PROMPT_SOURCES
        for field in fields
    ):
        for fallback in prompt_fallback(workflow, inputs.get("prompt", "")):
            result = [
                item
                for item in result
                if (item["nodeId"], item["fieldName"])
                != (fallback["nodeId"], fallback["fieldName"])
            ]
            result.append(fallback)
    return result
