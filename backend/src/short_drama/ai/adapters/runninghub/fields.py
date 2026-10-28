"""从工作流或应用定义收集可用字段。"""

from functools import cmp_to_key

from short_drama.ai.adapters.runninghub.field_parameters import apply_parameters as apply_parameters
from short_drama.ai.adapters.runninghub.field_parameters import (
    freeze_random_fields as freeze_random_fields,
)
from short_drama.ai.adapters.runninghub.field_parameters import parameter_fields as parameter_fields
from short_drama.ai.adapters.runninghub.field_parameters import (
    parameter_legacy_keys as parameter_legacy_keys,
)
from short_drama.ai.adapters.runninghub.field_parameters import validate_slots as validate_slots
from short_drama.ai.adapters.runninghub.field_sources import (
    BUSINESS_FIELDS,
    MEDIA_SOURCES,
    PROMPT_SOURCES,
    _media_field_type,
    _node_compare,
    dynamic_source,
    fields_for_mode,
    is_link,
    normalized_source,
    prompt_fallback,
    seed_field,
    unsafe_field,
)
from short_drama.ai.adapters.runninghub.field_sources import _invalid as _invalid
from short_drama.ai.adapters.runninghub.field_sources import dimension_source as dimension_source
from short_drama.ai.adapters.runninghub.field_sources import named_source as named_source
from short_drama.ai.adapters.runninghub.field_sources import prompt_candidate as prompt_candidate
from short_drama.ai.adapters.runninghub.field_values import node_info as node_info
from short_drama.ai.adapters.runninghub.field_values import resolve_value as resolve_value
from short_drama.ai.adapters.runninghub.field_values import validate_value as validate_value
from short_drama.ai.adapters.runninghub.values import (
    field_key,
)
from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubField


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
