"""固定源 RunningHub 字段推断、槽位、参数覆盖和 nodeInfoList。"""

import math
import re
import secrets
from functools import cmp_to_key

from short_drama.schemas.canvas_runninghub import RunningHubField

from .canvas_runninghub_values import (
    aspect_ratio,
    dimensions,
    field_key,
    field_options,
    numeric,
    option,
    ratio_value,
    scalar,
    selected_value,
)
from .types import GenerationError

PROMPT_SOURCES = {"prompt", "text", "positiveprompt", "positive"}
MEDIA_SOURCES = {
    "referenceimage": "referenceImages",
    "image": "referenceImages",
    "referenceimages": "referenceImages",
    "referencevideo": "referenceVideos",
    "video": "referenceVideos",
    "referencevideos": "referenceVideos",
    "referenceaudio": "referenceAudios",
    "audio": "referenceAudios",
    "referenceaudios": "referenceAudios",
    "mask": "mask",
}
BUSINESS_FIELDS = set(
    (
        "aspectratio ratio duration durationseconds seconds videoseconds quality resolution seed "
        "noiseseed steps step sigmapoints cfg cfgscale guidance guidancescale sampler samplername "
        "scheduler fps count batch batchsize generateaudio watermark negativeprompt systemprompt"
    ).split()
)


def _invalid():
    raise GenerationError("unsupported_workflow_parameters")


def dimension_source(key: str) -> str:
    for prefix in (
        "",
        "image",
        "video",
        "size",
        "output",
        "target",
        "latent",
        "frame",
        "canvas",
        "source",
        "resolution",
        "final",
    ):
        for suffix in ("width", "height"):
            if key == prefix + suffix:
                return suffix
    return {
        "pixelwidth": "width",
        "widthpixels": "width",
        "pixelheight": "height",
        "heightpixels": "height",
    }.get(key, "")


def named_source(name: str, mode="") -> str:
    key = field_key(name)
    if "mask" in key:
        return "mask"
    if source := dimension_source(key):
        return source
    groups = {
        "aspectRatio": {
            "ratio",
            "aspectratio",
            "imageaspectratio",
            "imageratio",
            "videoaspectratio",
            "videoratio",
        },
        "vquality": {"videoresolution", "videoquality", "vquality"},
        "size": {"size", "imagesize", "imageresolution"},
        "count": {
            "batch",
            "batchsize",
            "count",
            "numimages",
            "numberofimages",
            "imagecount",
            "imagescount",
        },
        "quality": {"quality"},
        "videoSeconds": {
            "duration",
            "seconds",
            "durationseconds",
            "videoseconds",
            "videoduration",
            "videodurationseconds",
            "videolength",
            "clipduration",
        },
        "videoGenerateAudio": {"generateaudio", "videogenerateaudio"},
        "videoWatermark": {"watermark", "videowatermark"},
        "audioFormat": {"audioformat"},
        "audioVoice": {"voice", "audiovoice"},
        "audioSpeed": {"audiospeed"},
        "audioInstructions": {"audioinstructions"},
        "transparentBackground": {"transparentbackground", "transparent"},
    }
    if key == "resolution" and mode in {"image", "video"}:
        return "vquality" if mode == "video" else "size"
    return next((source for source, names in groups.items() if key in names), "")


def dynamic_source(name: str, field_type: str, mode="") -> str:
    return named_source(name, mode) or {
        "IMAGE": "referenceImage",
        "VIDEO": "referenceVideo",
        "AUDIO": "referenceAudio",
    }.get(field_type.strip().upper(), "")


def normalized_source(field: RunningHubField) -> str:
    source = (field.source or "").strip().lower()
    if source:
        return field_key(source)
    if field.bind_prompt:
        return "prompt"
    if not field.source_from_upstream:
        return ""
    if field.field_name.strip().lower() in {"prompt", "text", "positive_prompt", "positiveprompt"}:
        return "prompt"
    return {
        "image": "referenceimage",
        "img": "referenceimage",
        "photo": "referenceimage",
        "picture": "referenceimage",
        "video": "referencevideo",
        "movie": "referencevideo",
        "audio": "referenceaudio",
        "sound": "referenceaudio",
        "music": "referenceaudio",
        "voice": "referenceaudio",
    }.get(field.field_type.strip().lower(), "")


def fields_for_mode(fields: list[RunningHubField], mode: str) -> list[RunningHubField]:
    result = []
    for original in fields:
        field = original.model_copy(deep=True)
        configured = field.source is not None or field.source_from_upstream is not None
        if not configured:
            field.source = (
                "prompt"
                if field.bind_prompt
                else dynamic_source(field.field_name, field.field_type)
            )
            if field.field_type.strip().lower() in {"image", "video", "audio"}:
                field.source_from_upstream = True
        canonical = named_source(field.source or "", mode)
        if canonical:
            field.source = canonical
        inferred = dynamic_source(field.field_name, field.field_type, mode)
        if not (field.source or "").strip() and not configured and not field.bind_prompt:
            field.source = inferred
            if inferred:
                field.source_automatic = True
        source_key, inferred_key, name_key = (
            field_key(field.source or ""),
            field_key(inferred),
            field_key(field.field_name),
        )
        if source_key and inferred_key and source_key != inferred_key:
            was_media = source_key in {"referenceimage", "referencevideo", "referenceaudio"}
            repair_media = was_media and (
                dimension_source(name_key) or inferred_key in {"aspectratio", "vquality"}
            )
            repair_resolution = name_key == "resolution" and (
                (mode == "video" and source_key == "size")
                or (mode == "image" and source_key == "vquality")
            )
            if (
                repair_media
                or repair_resolution
                or name_key == "quality"
                and source_key == "vquality"
            ):
                field.source = inferred
        result.append(field)
    return result


def is_link(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and scalar(value[0]) != ""
        and type(value[1]) in {int, float}
        and math.isfinite(value[1])
        and int(value[1]) == value[1]
    )


def unsafe_field(field: RunningHubField, workflow: dict) -> bool:
    node = workflow.get(field.node_id.strip())
    class_type = node.get("class_type", "") if isinstance(node, dict) else field.class_type
    class_type, name = str(class_type).strip().lower(), field.field_name.strip().lower()
    return (
        field.safe_to_override is False
        or class_type == "int"
        and name == "value"
        or class_type == "imageresize+"
        and name in {"width", "height", "multiple_of"}
    )


def prompt_candidate(name: str, class_type: str, title: str) -> bool:
    if field_key(name) in {
        "text",
        "prompt",
        "positiveprompt",
        "positive",
        "caption",
        "description",
    }:
        return True
    return name.strip().lower() == "value" and any(
        marker in class_type or marker in title
        for marker in ("text", "string", "prompt", "提示词", "文本")
    )


def _node_compare(left: str, right: str) -> int:
    def parsed(value):
        if re.fullmatch(r"[+-]?\d+", value.strip()):
            number = int(value)
            if -(2**63) <= number < 2**63:
                return number
        return None

    a, b = parsed(left), parsed(right)
    a, b = (a, b) if a is not None and b is not None else (left, right)
    return (a > b) - (a < b)


def prompt_fallback(workflow: dict, prompt: str) -> list[dict]:
    best, score = None, -1000
    for node_id in sorted(workflow, key=cmp_to_key(_node_compare)):
        node = workflow[node_id]
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        class_type = str(node.get("class_type", "")).strip().lower()
        meta = node.get("_meta") or {}
        title = str(meta.get("title", "")).strip().lower() if isinstance(meta, dict) else ""
        for name, value in sorted(node["inputs"].items()):
            if is_link(value) or not prompt_candidate(name, class_type, title):
                continue
            descriptor = (name + " " + title).lower()
            candidate_score = (
                -100
                if any(
                    marker in descriptor
                    for marker in ("negative", "neg prompt", "负面", "反向", "负向")
                )
                else 0
            )
            candidate_score += (
                20 if any(marker in descriptor for marker in ("positive", "正面", "正向")) else 0
            )
            candidate_score += 5 if "cliptextencode" in class_type else 0
            candidate_score += (
                2
                if field_key(name)
                in {"text", "prompt", "positiveprompt", "positive", "caption", "description"}
                else 0
            )
            if best is None or candidate_score > score:
                best, score = (
                    {"nodeId": node_id, "fieldName": name, "fieldValue": prompt},
                    candidate_score,
                )
    return [best] if best is not None and prompt.strip() else []


def _media_field_type(name: str, value, class_type: str) -> str:
    key, class_key = field_key(name), field_key(class_type)
    if "mask" in key:
        return "IMAGE"
    for kind in ("image", "video", "audio"):
        if "load" + kind in class_key and key in {"file", "path"}:
            return kind.upper()
    scalar_type = (
        "BOOLEAN" if type(value) is bool else "NUMBER" if type(value) in {int, float} else "TEXT"
    )
    if named_source(name) or type(value) in {bool, int, float}:
        return scalar_type
    for kind in ("image", "video", "audio"):
        names = {
            prefix + kind
            for prefix in (
                "",
                "input",
                "reference",
                "ref",
                "source",
                "init",
                "start",
                "end",
                "first",
                "last",
                "control",
                "controlnet",
                "style",
                "subject",
            )
        }
        names |= {kind + suffix for suffix in ("file", "path", "filename", "upload")}
        if kind == "image":
            names |= {"firstframe", "lastframe", "startframe", "endframe"}
        if key in names:
            return kind.upper()
    media_value = value[0] if isinstance(value, list) and value else value
    text = re.split(r"[?#]", scalar(media_value).strip().lower(), maxsplit=1)[0]
    for kind, extensions in (
        ("IMAGE", (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif")),
        ("VIDEO", (".mp4", ".webm", ".mov", ".m4v", ".mkv")),
        ("AUDIO", (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")),
    ):
        if text.endswith(extensions):
            return kind
    return "TEXT"


def seed_field(name: str) -> bool:
    key = field_key(name)
    return key.endswith("seed") or "noiseseed" in key


def collect_fields(workflow: dict, mode: str) -> list[RunningHubField]:
    prompt = prompt_fallback(workflow, "prompt")
    prompt_identity = (prompt[0]["nodeId"], prompt[0]["fieldName"]) if prompt else None
    fields = []
    orders = {"IMAGE": 0, "VIDEO": 0, "AUDIO": 0}
    for node_id in sorted(workflow, key=cmp_to_key(_node_compare)):
        node = workflow[node_id]
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        class_type = str(node.get("class_type") or "").strip()
        meta = node.get("_meta") or {}
        title = str(meta.get("title", "")).strip() if isinstance(meta, dict) else ""
        for name, value in sorted(node["inputs"].items()):
            if is_link(value):
                continue
            field_type = _media_field_type(name, value, class_type)
            field = RunningHubField(
                nodeId=node_id,
                fieldName=name,
                classType=class_type,
                fieldType=field_type,
                fieldValue=value,
                label=(title + " · " if title else "") + name,
                source="",
                sourceAutomatic=False,
                randomEnabled=seed_field(name),
            )
            field.safe_to_override = not unsafe_field(field, workflow)
            media_source = (
                "mask"
                if "mask" in field_key(name)
                else {
                    "IMAGE": "referenceImage",
                    "VIDEO": "referenceVideo",
                    "AUDIO": "referenceAudio",
                }.get(field_type, "")
            )
            field.role = (
                "media"
                if field_type in orders
                else "internal"
                if not field.safe_to_override
                else "business"
                if field_key(name) in BUSINESS_FIELDS or not class_type
                else "internal"
            )
            field.enabled = field.safe_to_override and field.role != "internal"
            if (node_id, name) == prompt_identity:
                field.source, field.source_automatic = "prompt", True
                field.required, field.role, field.enabled = True, "prompt", True
            elif media_source:
                field.source, field.source_automatic = media_source, True
                field.role, field.enabled = "media", True
                if media_source != "mask":
                    field.source_index = orders[field_type]
                    field.required = orders[field_type] == 0
                    orders[field_type] += 1
                    if field_type == "IMAGE":
                        field.image_order = orders[field_type]
            fields.append(field)
    return fields_for_mode(fields, mode)


def collect_app_fields(raw, mode: str) -> list[RunningHubField]:
    """AI App 只返回公开输入；按源排序和缺省槽位归一化。"""
    fields = []
    names = set(RunningHubField.model_fields)
    from pydantic.alias_generators import to_camel

    names.update(to_camel(name) for name in RunningHubField.model_fields)
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        field = RunningHubField.model_validate(
            {key: value for key, value in item.items() if key in names}
        )
        field.node_id, field.field_name = field.node_id.strip(), field.field_name.strip()
        if not field.node_id or not field.field_name:
            continue
        if not field.field_type.strip():
            field.field_type = _media_field_type(field.field_name, field.field_value, "")
        if not (field.source or "").strip():
            field.source = (
                "prompt"
                if field_key(field.field_name) in PROMPT_SOURCES
                else dynamic_source(field.field_name, field.field_type, mode)
            )
            field.source_automatic = bool(field.source)
        if field.enabled is None:
            field.enabled = True
        field.safe_to_override = True
        field.role = (
            "prompt"
            if normalized_source(field) in PROMPT_SOURCES
            else "media"
            if normalized_source(field) in MEDIA_SOURCES
            or field.field_type.upper() in {"IMAGE", "VIDEO", "AUDIO"}
            else "business"
        )
        if seed_field(field.field_name):
            field.random_enabled = True
        fields.append(field)
    fields.sort(key=lambda field: (cmp_to_key(_node_compare)(field.node_id), field.field_name))
    orders = {"referenceImages": 0, "referenceVideos": 0, "referenceAudios": 0}
    for field in fields:
        source = normalized_source(field)
        group = MEDIA_SOURCES.get(source)
        if source in PROMPT_SOURCES and "required" not in field.model_fields_set:
            field.required = True
        elif group in orders:
            if group == "referenceImages":
                field.image_order = (
                    field.image_order if field.image_order > 0 else orders[group] + 1
                )
                index = field.image_order - 1
            else:
                index = (
                    field.source_index
                    if "source_index" in field.model_fields_set
                    else orders[group]
                )
            if "source_index" not in field.model_fields_set:
                field.source_index = index
            orders[group] = max(orders[group], index + 1)
            if "required" not in field.model_fields_set:
                field.required = index == 0
    return fields_for_mode(fields, mode)


def validate_slots(fields: list[RunningHubField], inputs: dict):
    capacities = dict.fromkeys(("referenceImages", "referenceVideos", "referenceAudios"), 0)
    mask = False
    for field in fields:
        if field.enabled is False:
            continue
        group = MEDIA_SOURCES.get(normalized_source(field))
        if group == "mask":
            mask = True
        elif group:
            index = field.image_order - 1 if field.image_order > 0 else field.source_index
            capacities[group] = max(capacities[group], max(0, index) + 1)
    if (
        any(len(inputs.get(group, [])) > count for group, count in capacities.items())
        or inputs.get("mask")
        and not mask
    ):
        raise GenerationError("workflow_media_slots_exceeded")


def parameter_fields(fields: list[RunningHubField], workflow: dict) -> list[RunningHubField]:
    return [
        field
        for field in fields
        if field.enabled is not False
        and field.node_id
        and field.field_name
        and not unsafe_field(field, workflow)
        and normalized_source(field) not in PROMPT_SOURCES | MEDIA_SOURCES.keys()
        and field.field_type.upper() not in {"IMAGE", "VIDEO", "AUDIO"}
    ]


def apply_parameters(
    fields: list[RunningHubField], workflow: dict, values: dict
) -> list[RunningHubField]:
    if not isinstance(values, dict):
        _invalid()
    result = [field.model_copy(deep=True) for field in fields]
    allowed = set()
    for field in parameter_fields(result, workflow):
        key, random_key = (
            f"field:{field.node_id.strip()}:{field.field_name.strip()}",
            f"random:{field.node_id.strip()}:{field.field_name.strip()}",
        )
        legacy_keys = parameter_legacy_keys(field)
        node_key = field_key(field.node_id)
        field_keys = {
            field_key(value) for value in (field.field_name, field.source or "", field.label)
        } - {""}
        compatible_keys = []
        for stored in values:
            match = re.fullmatch(r"field:([^:]+):(.+)", stored, re.IGNORECASE)
            if match and field_key(match[1]) == node_key and field_key(match[2]) in field_keys:
                compatible_keys.append(stored)
        candidates = [key, *legacy_keys, *compatible_keys]
        allowed.update((key, random_key, *legacy_keys, *compatible_keys))
        selected = next((candidate for candidate in candidates if candidate in values), None)
        has_value = selected is not None
        has_random = random_key in values
        if not has_value and not has_random:
            continue
        value = (
            values[selected]
            if has_value
            else field.field_value
            if field.field_value is not None
            else field.value
        )
        if has_value:
            if (
                type(value) not in {str, int, float, bool}
                or isinstance(value, float)
                and not math.isfinite(value)
            ):
                _invalid()
            if field_key(field.field_name) == "aspectratio":
                value = ratio_value(field, scalar(value))
            field.field_value = value
            field.value = value
        if has_random and type(values[random_key]) is not bool:
            _invalid()
        field.source, field.source_automatic, field.source_from_upstream = "", False, False
        field.random_enabled = (
            values[random_key] if has_random else False if has_value else field.random_enabled
        )
    if values.keys() - allowed:
        _invalid()
    return result


def parameter_legacy_keys(field: RunningHubField) -> list[str]:
    keys = {field_key(value) for value in (field.field_name, field.source or "", field.label)}
    groups = (
        (
            {
                "aspectratio",
                "ratio",
                "imageaspectratio",
                "imageratio",
                "videoaspectratio",
                "videoratio",
            },
            ["source:aspectRatio", "source:aspect_ratio", "source:ratio"],
        ),
        ({"size", "imagesize", "imageresolution"}, ["source:size"]),
        ({"quality", "imagequality"}, ["source:quality"]),
        (
            {"vquality", "videoresolution", "videoquality"},
            ["source:vquality", "source:videoResolution"],
        ),
        (
            {"videoseconds", "duration", "durationseconds", "seconds"},
            ["source:videoSeconds", "source:duration", "source:duration_seconds"],
        ),
        ({"count", "batch", "batchsize"}, ["source:count", "source:batch"]),
    )
    return next((values for names, values in groups if keys & names), [])


def freeze_random_fields(fields: list[RunningHubField]) -> list[RunningHubField]:
    result = [field.model_copy(deep=True) for field in fields]
    for field in result:
        if field.enabled is False or not field.random_enabled or normalized_source(field):
            continue

        def integer_bound(value, fallback):
            text = scalar(value).strip()
            if value is None or not text:
                return fallback
            if not re.fullmatch(r"[+-]?\d+", text):
                _invalid()
            number = int(text)
            if not -(2**63) <= number < 2**63:
                _invalid()
            return number

        lower = integer_bound(field.min, 0)
        upper = integer_bound(field.max, 2**53 - 1)
        if seed_field(field.field_name):
            upper = min(upper, 2**32 - 1)
        if upper < lower:
            _invalid()
        field.field_value = lower + secrets.randbelow(upper - lower + 1)
        field.random_enabled = False
    return result


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
