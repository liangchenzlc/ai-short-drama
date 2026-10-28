from typing import Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.generations.admission.generation import ImageParameters


class BatchScope(InputModel):
    library: Literal["project"] = "project"
    project_id: Identifier
    asset_kind: Literal["character", "scene", "prop"] | None = None
    search: str = Field(default="", max_length=255)


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
        if self.scene == "shot_video" and self.count != 1:
            raise ValueError("Each shot generates one video")
        return self


class BatchCreate(BatchPreflight):
    preflight_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted_ids: list[Identifier] = Field(min_length=1, max_length=100)


class BatchRetry(InputModel):
    item_ids: list[Identifier] = Field(min_length=1, max_length=100)
