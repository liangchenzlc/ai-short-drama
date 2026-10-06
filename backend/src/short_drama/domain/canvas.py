"""Canvas works, private editing state and durable write receipts."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, DOUBLE, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .collaboration import OPTIONS


class CanvasAudit:
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    created_by: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    updated_by: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )


class CanvasChild(CanvasAudit):
    project_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    canvas_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))


def canvas_parent():
    return ForeignKeyConstraint(
        ["project_id", "canvas_id"],
        ["project_canvases.project_id", "project_canvases.id"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class ProjectCanvas(CanvasAudit, Base):
    __tablename__ = "project_canvases"
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # A client creates its stable key before the first successful server response.
    source_key: Mapped[str] = mapped_column(VARCHAR(64, collation="utf8mb4_0900_bin"))
    title: Mapped[str] = mapped_column(VARCHAR(255))
    position: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    schema_version: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=1)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    properties_json: Mapped[dict] = mapped_column(JSON, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uk_canvas_project_id"),
        UniqueConstraint("source_key", name="uk_canvas_source_key"),
        Index("idx_canvas_project_order", "project_id", "archived_at", "position", "id"),
        CheckConstraint("row_version > 0 AND schema_version > 0", name="ck_canvas_version"),
        CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="ck_canvas_title"),
        OPTIONS,
    )


class ProjectCanvasSettings(CanvasAudit, Base):
    __tablename__ = "project_canvas_settings"
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "projects.id",
            name="fk_canvas_settings_owning_project",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
    )
    primary_canvas_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    workspace_key: Mapped[str] = mapped_column(VARCHAR(64, collation="utf8mb4_0900_bin"))
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        UniqueConstraint("project_id", name="uk_canvas_settings_project"),
        UniqueConstraint("workspace_key", name="uk_canvas_workspace_key"),
        ForeignKeyConstraint(
            ["project_id", "primary_canvas_id"],
            ["project_canvases.project_id", "project_canvases.id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("row_version > 0", name="ck_canvas_settings_version"),
        OPTIONS,
    )


class CanvasNode(CanvasChild, Base):
    __tablename__ = "canvas_nodes"
    node_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    kind: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    parent_node_key: Mapped[str | None] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    x: Mapped[float] = mapped_column(DOUBLE(asdecimal=False))
    y: Mapped[float] = mapped_column(DOUBLE(asdecimal=False))
    width: Mapped[float] = mapped_column(DOUBLE(asdecimal=False))
    height: Mapped[float] = mapped_column(DOUBLE(asdecimal=False))
    z_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    content_json: Mapped[dict] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    content_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("canvas_id", "node_key", name="uk_canvas_node_key"),
        ForeignKeyConstraint(
            ["canvas_id", "parent_node_key"],
            ["canvas_nodes.canvas_id", "canvas_nodes.node_key"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("width > 0 AND height > 0", name="ck_canvas_node_dimensions"),
        CheckConstraint("row_version > 0 AND content_version > 0", name="ck_canvas_node_version"),
        Index("idx_canvas_node_order", "canvas_id", "archived_at", "z_index"),
        OPTIONS,
    )


class CanvasEdge(CanvasChild, Base):
    __tablename__ = "canvas_edges"
    edge_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    from_node_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    to_node_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    from_port: Mapped[str | None] = mapped_column(VARCHAR(128))
    to_port: Mapped[str | None] = mapped_column(VARCHAR(128))
    relation: Mapped[str | None] = mapped_column(VARCHAR(64))
    position: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    context_json: Mapped[dict] = mapped_column(JSON)
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("canvas_id", "edge_key", name="uk_canvas_edge_key"),
        ForeignKeyConstraint(
            ["canvas_id", "from_node_key"],
            ["canvas_nodes.canvas_id", "canvas_nodes.node_key"],
            name="fk_canvas_edge_from",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["canvas_id", "to_node_key"],
            ["canvas_nodes.canvas_id", "canvas_nodes.node_key"],
            name="fk_canvas_edge_to",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("from_node_key <> to_node_key", name="ck_canvas_edge_endpoints"),
        Index("idx_canvas_edge_order", "canvas_id", "archived_at", "position"),
        OPTIONS,
    )


class CanvasWriteReceipt(Base):
    __tablename__ = "canvas_write_receipts"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    actor_user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "projects.id",
            name="fk_canvas_receipt_owning_project",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
    )
    canvas_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    idempotency_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    operation_kind: Mapped[str] = mapped_column(VARCHAR(64))
    request_hash: Mapped[str] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_general_ci")
    )
    expected_row_version: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    committed_row_version: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("actor_user_id", "idempotency_key", name="uk_canvas_write_receipt"),
        OPTIONS,
    )


class CanvasRevision(CanvasChild, Base):
    __tablename__ = "canvas_revisions"
    canvas_row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    schema_version: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_general_ci")
    )
    reason: Mapped[str] = mapped_column(VARCHAR(32))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("canvas_id", "canvas_row_version", name="uk_canvas_revision_version"),
        UniqueConstraint("canvas_id", "id", name="uk_canvas_revision_id"),
        Index("idx_canvas_revision_time", "canvas_id", "created_at", "id"),
        CheckConstraint(
            "reason IN ('automatic','before_restore')", name="ck_canvas_revision_reason"
        ),
        OPTIONS,
    )


class CanvasUserState(CanvasChild, Base):
    __tablename__ = "canvas_user_states"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    viewport_json: Mapped[dict] = mapped_column(JSON)
    preferences_json: Mapped[dict] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("user_id", "canvas_id", name="uk_canvas_user_state"),
        CheckConstraint("row_version > 0", name="ck_canvas_user_state_version"),
        OPTIONS,
    )


class CanvasNodeUserState(CanvasChild, Base):
    __tablename__ = "canvas_node_user_states"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    node_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    draft_json: Mapped[dict] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("user_id", "canvas_id", "node_key", name="uk_canvas_node_user_state"),
        ForeignKeyConstraint(
            ["canvas_id", "node_key"],
            ["canvas_nodes.canvas_id", "canvas_nodes.node_key"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("row_version > 0", name="ck_canvas_node_user_state_version"),
        OPTIONS,
    )


class CanvasRevisionUserState(CanvasChild, Base):
    __tablename__ = "canvas_revision_user_states"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    revision_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    state_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("user_id", "revision_id", name="uk_canvas_revision_user_state"),
        ForeignKeyConstraint(
            ["canvas_id", "revision_id"],
            ["canvas_revisions.canvas_id", "canvas_revisions.id"],
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        OPTIONS,
    )


class CanvasMediaReference(CanvasChild, Base):
    __tablename__ = "canvas_media_references"
    owner_kind: Mapped[str] = mapped_column(VARCHAR(32))
    owner_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    node_key: Mapped[str | None] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    slot: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    ordinal: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint(
            "canvas_id", "owner_kind", "owner_key", "slot", "ordinal", name="uk_canvas_media_slot"
        ),
        ForeignKeyConstraint(
            ["canvas_id", "node_key"],
            ["canvas_nodes.canvas_id", "canvas_nodes.node_key"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        OPTIONS,
    )


class CanvasRevisionMediaReference(CanvasChild, Base):
    __tablename__ = "canvas_revision_media_references"
    revision_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("revision_id", "media_id", name="uk_canvas_revision_media"),
        ForeignKeyConstraint(
            ["canvas_id", "revision_id"],
            ["canvas_revisions.canvas_id", "canvas_revisions.id"],
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        OPTIONS,
    )


class CanvasUserMediaReference(CanvasChild, Base):
    __tablename__ = "canvas_user_media_references"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    revision_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    owner_kind: Mapped[str] = mapped_column(VARCHAR(32))
    owner_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    slot: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    ordinal: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        canvas_parent(),
        ForeignKeyConstraint(
            ["canvas_id", "revision_id"],
            ["canvas_revisions.canvas_id", "canvas_revisions.id"],
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "user_id",
            "canvas_id",
            "owner_kind",
            "owner_key",
            "slot",
            "ordinal",
            name="uk_canvas_user_media_slot",
        ),
        OPTIONS,
    )


class CanvasTimeline(CanvasChild, Base):
    __tablename__ = "canvas_timelines"
    schema_version: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=1)
    document_json: Mapped[dict] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("canvas_id", name="uk_canvas_timeline"),
        CheckConstraint(
            "row_version > 0 AND schema_version > 0", name="ck_canvas_timeline_version"
        ),
        OPTIONS,
    )


class CanvasDirectorScene(CanvasChild, Base):
    __tablename__ = "canvas_director_scenes"
    scene_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    position: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    schema_version: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=1)
    scene_json: Mapped[dict] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("canvas_id", "scene_key", name="uk_canvas_director_scene"),
        CheckConstraint("row_version > 0 AND schema_version > 0", name="ck_canvas_scene_version"),
        OPTIONS,
    )


class CanvasWorkspaceUserState(CanvasAudit, Base):
    __tablename__ = "canvas_workspace_user_states"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    preferences_json: Mapped[dict] = mapped_column(JSON, default=dict)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        UniqueConstraint("user_id", name="uk_canvas_workspace_user"),
        CheckConstraint("row_version > 0", name="ck_canvas_workspace_user_version"),
        OPTIONS,
    )


CANVAS_PRIVATE_TABLES = frozenset(
    {
        "canvas_task_bindings",
        "canvas_task_media_references",
        "canvas_task_text_deltas",
        "canvas_results",
        "canvas_write_receipts",
        "canvas_user_states",
        "canvas_node_user_states",
        "canvas_revision_user_states",
        "canvas_user_media_references",
        "canvas_workspace_user_states",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_beefapi_connections",
        "canvas_resource_uploads",
        "canvas_resource_chunks",
        "canvas_resource_copy_sources",
        "canvas_resource_deletions",
        "canvas_creation_attempts",
        "canvas_creation_resources",
        "canvas_user_binary_references",
        "canvas_library_assets",
        "canvas_library_asset_references",
        "canvas_library_folders",
        "canvas_library_folder_items",
        "canvas_project_folders",
        "canvas_project_folder_items",
    }
)
CANVAS_TABLES = CANVAS_PRIVATE_TABLES | frozenset(
    {
        "project_canvases",
        "project_canvas_settings",
        "canvas_nodes",
        "canvas_edges",
        "canvas_revisions",
        "canvas_media_references",
        "canvas_revision_media_references",
        "canvas_timelines",
        "canvas_director_scenes",
        "canvas_drawings",
        "canvas_drawing_versions",
        "canvas_drawing_media_references",
        "canvas_revision_drawing_references",
        "canvas_binary_resources",
        "canvas_binary_references",
    }
)
