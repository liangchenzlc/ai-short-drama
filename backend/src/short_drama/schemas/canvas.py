"""Typed source-document boundary; IDs and database revisions use decimal strings."""

import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from .base import Identifier, InputModel, NonnegativeVersion

SourceKey = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[\w:.-]+$")]
CanvasKey = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[\w-]+$")]
CanvasTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CanvasPosition(SourceModel):
    x: Finite
    y: Finite


class CanvasViewport(CanvasPosition):
    k: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class CanvasCustomAppearance(SourceModel):
    base_theme: Literal["light", "dark"] = Field(alias="baseTheme")
    background_color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] = Field(
        alias="backgroundColor"
    )
    background_brightness: Annotated[Finite, Field(ge=-30, le=30)] = Field(
        alias="backgroundBrightness"
    )
    grid_color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] = Field(alias="gridColor")
    grid_opacity: Annotated[Finite, Field(ge=0, le=100)] = Field(alias="gridOpacity")


class CanvasAppearance(SourceModel):
    mode: Literal["light", "dark", "custom"]
    custom: CanvasCustomAppearance | None = None

    @model_validator(mode="after")
    def validate_custom(self):
        if self.mode == "custom" and self.custom is None:
            raise ValueError("custom appearance requires custom colors")
        return self


class CanvasViewPreferences(SourceModel):
    appearance: CanvasAppearance | None = None
    background_mode: Literal["dots", "lines", "blank"] = Field(
        default="dots", alias="backgroundMode"
    )
    show_image_info: bool = Field(default=False, alias="showImageInfo")


class CanvasViewPreferencesRequest(InputModel):
    expected_preferences: CanvasViewPreferences
    preferences: CanvasViewPreferences


class CanvasViewPreferencesRead(InputModel):
    row_version: NonnegativeVersion
    preferences: CanvasViewPreferences


class CanvasNodeDocument(SourceModel):
    id: SourceKey
    type: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    title: Annotated[str, Field(max_length=65535)]
    position: CanvasPosition
    width: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    height: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    parent_id: SourceKey | None = Field(default=None, alias="parentId")
    created_at: str | None = Field(default=None, alias="createdAt", max_length=64)
    updated_at: str | None = Field(default=None, alias="updatedAt", max_length=64)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CanvasEdgeDocument(SourceModel):
    id: SourceKey
    from_node_id: SourceKey = Field(alias="fromNodeId")
    to_node_id: SourceKey = Field(alias="toNodeId")
    path_d: str | None = Field(default=None, alias="pathD", max_length=65535)
    from_handle_id: str | None = Field(default=None, alias="fromHandleId", max_length=128)
    to_handle_id: str | None = Field(default=None, alias="toHandleId", max_length=128)
    from_anchor_ratio: Finite | None = Field(default=None, alias="fromAnchorRatio")
    to_anchor_ratio: Finite | None = Field(default=None, alias="toAnchorRatio")
    relation: Literal["storyboard-output", "storyboard-asset-reference", "batch-output"] | None = (
        None
    )
    storyboard_row_id: SourceKey | None = Field(default=None, alias="storyboardRowId")


class CanvasDocument(SourceModel):
    id: CanvasKey
    revision: NonnegativeVersion = 0
    remote_content_hash: str | None = Field(default=None, alias="remoteContentHash")
    workspace_project_id: CanvasKey | None = Field(default=None, alias="workspaceProjectId")
    project_id: str | None = Field(default=None, alias="projectId")
    folder_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = (
        Field(default=None, alias="folderId")
    )
    title: CanvasTitle
    canvas_title: str | None = Field(default=None, alias="canvasTitle", max_length=255)
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    nodes: list[CanvasNodeDocument] = Field(default_factory=list, max_length=50000)
    connections: list[CanvasEdgeDocument] = Field(default_factory=list, max_length=100000)
    chat_sessions: list[dict[str, JsonValue]] = Field(default_factory=list, alias="chatSessions")
    active_chat_id: str | None = Field(default=None, alias="activeChatId")
    starter_mode: str | None = Field(default=None, alias="starterMode")
    appearance: dict[str, JsonValue] | None = None
    background_mode: str = Field(default="dots", alias="backgroundMode")
    show_image_info: bool = Field(default=False, alias="showImageInfo")
    viewport: CanvasViewport = Field(default_factory=lambda: CanvasViewport(x=0, y=0, k=1))
    director_scenes: list[dict[str, JsonValue]] = Field(
        default_factory=list, alias="directorScenes"
    )
    timeline: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("duplicate node keys")
        if len({edge.id for edge in self.connections}) != len(self.connections):
            raise ValueError("duplicate edge keys")
        for node in self.nodes:
            if node.parent_id and node.parent_id not in nodes:
                raise ValueError("parent node is outside this canvas")
        finished: set[str] = set()
        for node in self.nodes:
            path: set[str] = set()
            current = node
            while current.id not in finished:
                if current.id in path:
                    raise ValueError("cyclic parent relationship")
                path.add(current.id)
                if not current.parent_id:
                    break
                current = nodes[current.parent_id]
            finished.update(path)
        for edge in self.connections:
            if edge.from_node_id == edge.to_node_id:
                raise ValueError("self connections are not supported")
            if edge.from_node_id not in nodes or edge.to_node_id not in nodes:
                raise ValueError("connection endpoint is outside this canvas")
        raw = self.model_dump(by_alias=True, mode="json", exclude_unset=True)
        validate_document_json(raw)
        if len(json.dumps(raw, ensure_ascii=False).encode()) > 32 * 1024 * 1024:
            raise ValueError("canvas document exceeds 32 MiB")
        return self


def validate_document_json(value: JsonValue, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("canvas data is nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$canvas_projection":
                raise ValueError("reserved canvas projection key")
            if key.lower().replace("_", "").replace("-", "") in {
                "apikey",
                "authorization",
                "accesstoken",
                "refreshtoken",
                "secretkey",
                "password",
            }:
                raise ValueError("credentials must never be stored in canvas documents")
            validate_document_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_document_json(item, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite canvas number")


class CanvasCreateRequest(InputModel):
    title: CanvasTitle = "未命名画布"
    source_key: CanvasKey | None = None
    source_document: CanvasDocument | None = None


class CanvasCommitRequest(InputModel):
    expected_row_version: Identifier
    schema_version: Literal[1] = 1
    source_document: CanvasDocument


class CanvasVersionRequest(InputModel):
    expected_row_version: Identifier


class CanvasRecycleRestoreRequest(InputModel):
    archive_key: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class CanvasRecycleStatusRead(InputModel):
    source_key: CanvasKey
    project_id: Identifier
    archive_key: str
    expected_row_version: Identifier
    committed_row_version: Identifier
    state: Literal["archived", "superseded", "purged"]


class CanvasRecycleItemRead(InputModel):
    source_key: CanvasKey
    project_id: Identifier
    archive_key: str
    deleted_at: str
    source_document: dict[str, JsonValue]


class CanvasRecycleListRead(InputModel):
    items: list[CanvasRecycleItemRead]


class CanvasRecyclePurgeRead(InputModel):
    source_key: CanvasKey
    archive_key: str
    state: Literal["purged"]


class CanvasDrawingHead(InputModel):
    revision: Identifier
    deleted: bool


class CanvasRestoreRequest(CanvasVersionRequest):
    expected_drawing_heads: dict[SourceKey, CanvasDrawingHead] | None = Field(
        default=None, max_length=20000
    )


class CanvasUserStateRequest(InputModel):
    expected_row_version: NonnegativeVersion
    viewport: CanvasViewport | None = None
    preferences: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_preferences(self):
        validate_document_json(self.preferences or {})
        CanvasViewPreferences.model_validate(
            {
                key: value
                for key, value in (self.preferences or {}).items()
                if key in {"appearance", "backgroundMode", "showImageInfo"}
            }
        )
        if set(self.preferences or {}) - {
            "appearance",
            "backgroundMode",
            "showImageInfo",
            "chatSessions",
            "activeChatId",
        }:
            raise ValueError("unknown personal preference field")
        return self


class CanvasViewportRequest(InputModel):
    expected_viewport: CanvasViewport
    viewport: CanvasViewport


class CanvasSummaryRead(InputModel):
    id: Identifier
    project_id: Identifier
    source_key: CanvasKey
    title: CanvasTitle
    row_version: Identifier
    schema_version: int
    created_at: str
    updated_at: str


class CanvasRead(CanvasSummaryRead):
    source_document: dict[str, JsonValue]
    resource_aliases: dict[str, list[Identifier]] = Field(default_factory=dict)


class CanvasCreationRead(CanvasSummaryRead):
    resource_map: dict[str, Identifier] = Field(default_factory=dict)
    resource_aliases: dict[str, list[Identifier]] = Field(default_factory=dict)


class CanvasListRead(InputModel):
    items: list[CanvasSummaryRead]


class CanvasWorkspaceItemRead(CanvasSummaryRead):
    folder_id: str | None = None
    canvas_title: str | None = None
    node_count: int
    preview_nodes: list[dict[str, JsonValue]]
    source_document: dict[str, JsonValue] | None = None
    resource_aliases: dict[str, list[Identifier]] | None = None


class CanvasWorkspaceListRead(InputModel):
    items: list[CanvasWorkspaceItemRead]
    page: int
    page_size: int
    total: int
    has_more: bool


class CanvasReceiptRead(InputModel):
    operation_kind: str
    result: dict[str, JsonValue]


class CanvasArchiveRead(InputModel):
    archived_canvas_id: Identifier
    next_canvas_id: Identifier | None
    project_archived: bool


class CanvasUserStateRead(InputModel):
    row_version: NonnegativeVersion
    viewport: CanvasViewport
    preferences: dict[str, JsonValue]


class CanvasRevisionSummaryRead(InputModel):
    id: Identifier
    canvas_id: Identifier
    row_version: Identifier
    title: CanvasTitle
    node_count: int
    connection_count: int
    payload_bytes: int
    reason: Literal["automatic", "before_restore"]
    created_at: str
    content_updated_at: str


class CanvasRevisionListRead(InputModel):
    items: list[CanvasRevisionSummaryRead]
    row_version: Identifier
    drawing_heads: dict[SourceKey, CanvasDrawingHead] = Field(default_factory=dict)


class CanvasRevisionRead(InputModel):
    revision: CanvasRevisionSummaryRead
    source_document: dict[str, JsonValue]
    resource_aliases: dict[str, list[Identifier]] = Field(default_factory=dict)
