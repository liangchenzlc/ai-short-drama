from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import Identifier, InputModel

ClipKey = Annotated[
    str,
    Field(
        pattern=r"^(?:[1-9][0-9]{0,19}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
    ),
]


class AssemblyClipEdit(InputModel):
    id: ClipKey
    source_clip_id: ClipKey | None = None
    included: bool
    muted: bool
    trim_in_ms: int = Field(ge=0, le=3600000, strict=True)
    trim_out_ms: int | None = Field(default=None, ge=1, le=3600000, strict=True)

    @model_validator(mode="after")
    def range_order(self):
        if self.trim_out_ms is not None and self.trim_out_ms <= self.trim_in_ms:
            raise ValueError("裁剪终点必须晚于起点")
        return self


class AssemblyEdit(InputModel):
    row_version: Identifier
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    resolution: Literal["720p", "1080p"]
    clips: list[AssemblyClipEdit] = Field(max_length=300)

    @model_validator(mode="after")
    def unique_clips(self):
        if len({c.id for c in self.clips}) != len(self.clips):
            raise ValueError("片段 ID 不能重复")
        return self


class AssemblyVersion(InputModel):
    row_version: Identifier
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class AssemblyExport(AssemblyVersion):
    acknowledge_stale_source: bool = False


class AssemblyApply(InputModel):
    row_version: Identifier
    context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_stale_source: bool = False
