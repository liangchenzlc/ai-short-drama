from typing import Literal

from pydantic import Field, model_validator

from .ai_generation import ImageParameters
from .base import Identifier, InputModel


class BatchScope(InputModel):
    library: Literal["global", "project", "episode"] = "episode"
    project_id: Identifier | None = None
    episode_id: Identifier | None = None
    asset_kind: Literal["character", "scene", "prop"] | None = None
    search: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def scope_ids(self):
        if self.library in {"project", "episode"} and not self.project_id:
            raise ValueError("Project scope requires project_id")
        if self.library == "episode" and not self.episode_id:
            raise ValueError("Episode scope requires episode_id")
        if self.library == "global" and (self.project_id or self.episode_id):
            raise ValueError("Global scope cannot contain project or episode")
        if self.library == "project" and self.episode_id:
            raise ValueError("Project scope cannot contain episode")
        return self


class BatchPreflight(InputModel):
    scene: Literal["asset_image", "shot_image", "shot_video"]
    config_id: Identifier
    scope: BatchScope
    source_ids: list[Identifier] | None = Field(default=None, min_length=1, max_length=100)
    mode: Literal["missing", "regenerate"] = "missing"
    count: int = Field(default=1, ge=1, le=4, strict=True)
    asset_parameters: ImageParameters = Field(default_factory=ImageParameters)

    @model_validator(mode="after")
    def unique_sources(self):
        if self.source_ids is not None and len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Duplicate sources")
        if self.scene != "asset_image" and self.scope.library != "episode":
            raise ValueError("Shot batches must belong to one episode")
        if self.scene == "shot_video" and self.count != 1:
            raise ValueError("Each shot generates one video")
        return self


class BatchCreate(BatchPreflight):
    preflight_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted_ids: list[Identifier] = Field(min_length=1, max_length=100)


class BatchRetry(InputModel):
    item_ids: list[Identifier] = Field(min_length=1, max_length=100)
