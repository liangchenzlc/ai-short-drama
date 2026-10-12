"""内部角色版本快照合同；不向 HTTP 输入暴露版本指针或声音状态。"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from .base import Identifier, InputModel, MediumText, nonblank

RepresentationRole = Literal[
    "primary", "front", "side", "back", "turnaround_sheet", "expression_sheet"
]


class AssetVersionAsset(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["character"]
    name: Annotated[str, Field(max_length=255)]
    label: Annotated[str, Field(max_length=120)]
    description: MediumText
    prompt: MediumText
    tags: list[Annotated[str, Field(max_length=40)]] = Field(max_length=20)
    scene_time: Literal[""]
    media_id: Identifier | None

    @model_validator(mode="after")
    def validate_text(self):
        nonblank(self.name)
        for tag in self.tags:
            nonblank(tag)
        return self


class AssetVersionRepresentation(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: RepresentationRole
    media_id: Identifier


class AssetVersionSnapshot(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    asset: AssetVersionAsset
    definition: dict[str, JsonValue]
    definition_origin: Literal["legacy_current", "structured"]
    representations: list[AssetVersionRepresentation]
    generation_reference_ids: list[Identifier]
    source_audit: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be the integer 1")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.asset.media_id is not None and not any(
            row.media_id == self.asset.media_id for row in self.representations
        ):
            raise ValueError("current character media must have an explicit representation slot")
        # Refuse NaN/infinity rather than producing a non-JSON canonical hash.
        json.dumps(self.model_dump(mode="json"), allow_nan=False)
        return self


def validate_snapshot(value: object) -> AssetVersionSnapshot:
    """验证完整 definition 和独立媒体槽位；不删未知 definition 扩展。"""
    if isinstance(value, AssetVersionSnapshot):
        value = value.model_dump(mode="json")
    return AssetVersionSnapshot.model_validate(value)


def snapshot_content_hash(value: object) -> str:
    """仅排除来源审计；数组顺序、完整定义及定义权威类型均进入摘要。"""
    snapshot = validate_snapshot(value).model_dump(mode="json", exclude={"source_audit"})
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_media_references(value: object) -> list[dict]:
    """每个用途独立从零排序；同媒体的不同用途和角色不会被去重。"""
    snapshot = validate_snapshot(value)
    return [
        {
            "purpose": "representation",
            "role": row.role,
            "ordinal": ordinal,
            "media_id": str(row.media_id),
        }
        for ordinal, row in enumerate(snapshot.representations)
    ] + [
        {"purpose": "generation_reference", "role": "", "ordinal": ordinal, "media_id": str(mid)}
        for ordinal, mid in enumerate(snapshot.generation_reference_ids)
    ]
