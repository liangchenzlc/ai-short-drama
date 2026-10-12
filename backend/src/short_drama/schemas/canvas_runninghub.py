"""固定源RunningHub内置Workflow/App字段；不接受客户端鉴权或传输配置。"""

import json
import math
import re
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, StrictBool, model_validator
from pydantic.alias_generators import to_camel

from .base import InputModel, NonnegativeVersion
from .canvas_model_catalog import validate_public_json


def _nested_value(value, keys: tuple[str, ...]):
    if isinstance(value, list):
        return next(
            (item for child in value if (item := _nested_value(child, keys)) is not None), None
        )
    if isinstance(value, dict):
        return next(
            (value[key] for key in keys if value.get(key) is not None),
            _nested_value(value.get("range"), keys),
        )
    return None


def _legacy_bool(value) -> bool:
    if type(value) is bool:
        return value
    return isinstance(value, str) and value.strip() in {"1", "t", "T", "TRUE", "true", "True"}


def _legacy_int(value) -> int:
    if type(value) in {int, float} and math.isfinite(value):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    return 0


class RunningHubField(InputModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    id: str = Field(default="", max_length=512)
    node_id: str = Field(default="", max_length=256)
    class_type: str = Field(default="", max_length=256)
    field_name: str = Field(default="", max_length=256)
    value: JsonValue = None
    field_value: JsonValue = None
    field_type: str = Field(default="", max_length=64)
    label: str = Field(default="", max_length=512)
    role: str = Field(default="", max_length=64)
    safe_to_override: StrictBool | None = None
    options_source: str = Field(default="", max_length=64)
    options: list[JsonValue] = Field(default_factory=list, max_length=2000)
    min: JsonValue = None
    max: JsonValue = None
    step: JsonValue = None
    random_enabled: StrictBool = False
    bind_prompt: StrictBool = False
    enabled: StrictBool | None = None
    # None保持缺省与明确空来源的区别，冻结时exclude_none，不能默认填空。
    source: str | None = Field(default=None, max_length=128)
    source_index: int = Field(default=0, strict=True)
    image_order: int = Field(default=0, strict=True)
    source_from_upstream: StrictBool | None = None
    required: StrictBool = False
    source_automatic: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def legacy_aliases(cls, raw):
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        aliases = {
            "nodeId": ("node", "node_id", "nodeID"),
            "classType": ("class_type", "typeName", "nodeType"),
            "fieldName": ("input", "inputName", "input_name", "name"),
            "id": ("fieldId", "field_id", "key"),
            "fieldType": ("type",),
            "fieldValue": ("defaultValue", "default_value", "default"),
            "options": ("values", "choices", "enum", "fieldOptions", "field_options"),
            "min": ("minValue", "min_value"),
            "max": ("maxValue", "max_value"),
            "step": ("stepValue", "step_value"),
            "label": ("title",),
            "source": ("bind", "from"),
            "sourceIndex": ("source_index", "index"),
            "imageOrder": ("image_order",),
            "required": ("isRequired", "is_required"),
            "randomEnabled": ("random_enabled",),
            "bindPrompt": ("bind_prompt",),
            "sourceFromUpstream": ("source_from_upstream",),
        }
        string_fields = {"nodeId", "classType", "fieldName", "id", "fieldType", "label", "source"}
        nested_containers = [
            raw.get(key)
            for key in ("options", "values", "choices", "range", "fieldOptions", "field_options")
        ]
        for target in ("min", "max", "step"):
            keys = (target, f"{target}Value", f"{target}_value")
            if data.get(target) is None:
                data[target] = next((raw[key] for key in keys if raw.get(key) is not None), None)
            if data[target] is None:
                data[target] = next(
                    (
                        found
                        for container in nested_containers
                        if (found := _nested_value(container, keys)) is not None
                    ),
                    None,
                )
        for target, names in aliases.items():
            snake_name = next((key for key in cls.model_fields if to_camel(key) == target), target)
            if target not in data and snake_name in data and snake_name not in names:
                data[target] = data.pop(snake_name)
            if target not in data or target in string_fields and not data[target]:
                for name in names:
                    if raw.get(name) is not None:
                        item = raw[name]
                        if target in string_fields:
                            item = str(item).strip()
                        elif target in {"sourceIndex", "imageOrder"}:
                            item = _legacy_int(item)
                        elif target in {
                            "required",
                            "randomEnabled",
                            "bindPrompt",
                            "sourceFromUpstream",
                        }:
                            item = _legacy_bool(item)
                        data[target] = item
                        break
            for name in names:
                data.pop(name, None)
        if isinstance(data.get("options"), dict):
            container = data["options"]
            data["options"] = next(
                (
                    container[name]
                    for name in ("choices", "options", "values", "enum")
                    if isinstance(container.get(name), list)
                ),
                [],
            )
        data.pop("range", None)
        if "source" in data and data["source"] is None:
            data["source"] = ""
        if not data.get("id") and data.get("nodeId") and data.get("fieldName"):
            data["id"] = data["nodeId"] + "::" + data["fieldName"]
        return data


class RunningHubWorkflow(InputModel):
    """只从本人保存的运行profile读取；前端仅传身份和字段值。"""

    kind: Literal["workflow", "app"] = "workflow"
    workflow_id: str = Field(default="", max_length=256)
    webapp_id: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=120)
    workflow_json: dict[str, JsonValue] = Field(default_factory=dict)
    fields: list[RunningHubField] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def public_descriptor(self):
        data = self.model_dump(mode="json", exclude_none=True)
        validate_public_json(data)
        if len(json.dumps(data, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
            raise ValueError("RunningHub workflow exceeds 2 MiB")
        return self


class RunningHubExecution(InputModel):
    """客户端仅声明所选条目；实际协议、字段和传输由本人保存配置决定。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    provider: Literal["runninghub"]
    kind: Literal["workflow", "app"]
    interface_type: Literal[
        "runninghub-workflow-image", "runninghub-workflow-video", "runninghub-workflow-audio"
    ]
    name: str = Field(default="", max_length=120)
    workflow_id: str = Field(default="", max_length=256)
    webapp_id: str = Field(default="", max_length=256)


class RunningHubSchemaRequest(InputModel):
    row_version: NonnegativeVersion
