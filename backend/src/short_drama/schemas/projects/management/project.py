from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_serializer

from short_drama.schemas.base import (
    Identifier,
    InputModel,
    ReadModel,
    nonblank,
)


class ProjectCreate(InputModel):
    workspace_mode: Literal["standard", "infinite_canvas"] = "standard"
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]


class ProjectUpdate(InputModel):
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)] = None


class ProjectRead(ReadModel):
    cover_url: str | None = None
    workspace_mode: Literal["standard", "infinite_canvas"] = "standard"
    primary_canvas_id: Identifier | None = None
    canvas_count: int = 0
    owner_user_id: Identifier | None = None
    row_version: Identifier = 1
    capabilities: dict[str, bool] = Field(default_factory=dict)
    id: Identifier
    name: Annotated[str, Field(max_length=120), AfterValidator(nonblank)]
    editing_script_id: Identifier | None = None
    content_version: Identifier = 1
    storyboard_version: Identifier = 1
    last_opened_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: Identifier | None = None
    updated_by: Identifier | None = None

    @field_serializer("last_opened_at", "created_at", "updated_at", when_used="json")
    def serialize_utc(self, value: datetime | None):
        if value is None:
            return None
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat().replace("+00:00", "Z")


class ProjectSummary(ProjectRead):
    pass
