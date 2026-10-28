"""RunningHub 参数绑定、槽位限制与随机字段冻结。"""

import math
import re
import secrets

from short_drama.ai.adapters.runninghub.field_sources import (
    MEDIA_SOURCES,
    PROMPT_SOURCES,
    _invalid,
    normalized_source,
    seed_field,
    unsafe_field,
)
from short_drama.ai.adapters.runninghub.values import (
    field_key,
    ratio_value,
    scalar,
)
from short_drama.ai.types import GenerationError
from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubField


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
