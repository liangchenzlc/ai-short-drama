"""Project-shared candidates; these contracts never expose private Agent origins."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from short_drama.schemas.asset import AssetPatch
from short_drama.schemas.base import (
    Identifier,
    InputModel,
    MediumText,
    PositiveUInt64,
    ReadModel,
)

ArtifactKind = Literal[
    "novel_proposal",
    "text_proposal",
    "script_candidate",
    "asset_patch",
    "shot_patch",
    "extraction_candidate",
    "storyboard_candidate",
    "image_candidate",
    "video_candidate",
]
ArtifactStatus = Literal["ready", "applied", "rejected", "archived"]


class ArtifactSource(ReadModel):
    episode_id: Identifier
    content_version: PositiveUInt64
    storyboard_version: PositiveUInt64
    episode_row_version: PositiveUInt64
    target_kind: Literal["episode", "asset", "shot"] = "episode"
    target_id: Identifier | None = None
    target_row_version: PositiveUInt64 | None = None
    model_name: str | None = None
    generated_at: datetime | None = None


class PatchDifference(ReadModel):
    field: str
    before: Any
    after: Any


class ArtifactSummary(ReadModel):
    id: Identifier
    project_id: Identifier
    episode_id: Identifier
    kind: ArtifactKind
    status: ArtifactStatus
    row_version: PositiveUInt64
    preview: str
    source_snapshot: ArtifactSource
    script_id: Identifier | None
    parent_script_id: Identifier | None
    generation_task_id: Identifier | None
    media_asset_id: Identifier | None
    media_id: Identifier | None
    target_asset_id: Identifier | None
    target_shot_id: Identifier | None
    created_by: Identifier
    applied_by: Identifier | None
    applied_at: datetime | None
    apply_receipt: dict | None
    created_at: datetime
    updated_at: datetime


class ArtifactRead(ArtifactSummary):
    content: str | None
    patch: dict | None
    diff: list[PatchDifference]


class ArtifactAdopt(InputModel):
    row_version: PositiveUInt64
    content_version: PositiveUInt64
    storyboard_version: PositiveUInt64 | None = None
    target_row_version: PositiveUInt64 | None = None
    confirm_shared: bool = False
    native_review: dict | None = None


class CandidateAssetPatch(AssetPatch):
    """Reuse public normalization while keeping authorization out of proposed fields."""

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, values):
        if isinstance(values, dict) and any(value is None for value in values.values()):
            raise ValueError("Proposed fields cannot be null")
        return values


class CandidateShotPatch(InputModel):
    script: MediumText = None
    duration_ms: int = Field(default=None, strict=True, ge=1000, le=10000)
    video_prompt: MediumText = None

    @model_validator(mode="before")
    @classmethod
    def nonempty_fields(cls, values):
        if isinstance(values, dict) and (not values or any(v is None for v in values.values())):
            raise ValueError("Provide non-null fields to propose")
        return values
