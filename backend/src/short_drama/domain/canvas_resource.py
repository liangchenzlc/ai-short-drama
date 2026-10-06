"""Durable upload identities and non-media canvas files; payloads live in MinIO."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit, CanvasChild, canvas_parent
from .collaboration import OPTIONS, ResourceScope


class CanvasBinaryResource(ResourceScope, CanvasAudit, Base):
    __tablename__ = "canvas_binary_resources"
    published_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    resource_kind: Mapped[str] = mapped_column(VARCHAR(16))
    mime_type: Mapped[str] = mapped_column(VARCHAR(127, collation="utf8mb4_0900_bin"))
    storage_locator: Mapped[str] = mapped_column(VARCHAR(700, collation="utf8mb4_0900_bin"))
    original_name: Mapped[str] = mapped_column(VARCHAR(255))
    byte_size: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    __table_args__ = (
        UniqueConstraint("storage_locator", name="uk_canvas_binary_locator"),
        CheckConstraint(
            "(scope_user_id IS NULL) <> (project_id IS NULL)", name="ck_canvas_binary_scope"
        ),
        CheckConstraint("resource_kind = 'file' AND byte_size > 0", name="ck_canvas_binary_kind"),
        CheckConstraint("CHAR_LENGTH(checksum_sha256) = 64", name="ck_canvas_binary_checksum"),
        OPTIONS,
    )


class CanvasResourceUpload(ResourceScope, CanvasAudit, Base):
    __tablename__ = "canvas_resource_uploads"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    canvas_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    idempotency_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    mode: Mapped[str] = mapped_column(VARCHAR(16))
    status: Mapped[str] = mapped_column(VARCHAR(16))
    declared_json: Mapped[dict] = mapped_column(JSON)
    reserved_resource_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    storage_locator: Mapped[str] = mapped_column(VARCHAR(700, collation="utf8mb4_0900_bin"))
    byte_size: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    binary_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_binary_resources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "canvas_id"],
            ["project_canvases.project_id", "project_canvases.id"],
            name="fk_canvas_upload_owning_canvas",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("user_id", "idempotency_hash", name="uk_canvas_upload_identity"),
        UniqueConstraint("reserved_resource_id", name="uk_canvas_upload_resource"),
        Index("idx_canvas_upload_expiry", "status", "expires_at"),
        CheckConstraint(
            "(scope_user_id IS NULL) <> (project_id IS NULL)", name="ck_canvas_upload_scope"
        ),
        CheckConstraint(
            "canvas_id IS NULL OR project_id IS NOT NULL", name="ck_canvas_upload_canvas"
        ),
        CheckConstraint("mode IN ('multipart','chunked','copy')", name="ck_canvas_upload_mode"),
        CheckConstraint(
            "(status = 'pending' AND media_id IS NULL AND binary_id IS NULL) OR "
            "(status = 'ready' AND ((media_id IS NULL) <> (binary_id IS NULL)))",
            name="ck_canvas_upload_result",
        ),
        CheckConstraint("byte_size > 0", name="ck_canvas_upload_size"),
        OPTIONS,
    )


class CanvasResourceCopySource(Base):
    """Private provenance; source FKs pin bytes only while a copy is pending."""

    __tablename__ = "canvas_resource_copy_sources"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    upload_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_resource_uploads.id", ondelete="CASCADE", onupdate="RESTRICT"),
    )
    original_resource_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    source_binary_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_binary_resources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    released_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("upload_id", name="uk_canvas_copy_upload"),
        Index("idx_canvas_copy_origin", "original_resource_id"),
        CheckConstraint(
            "original_resource_id > 0 AND "
            "((released_at IS NULL AND ((source_media_id IS NULL) <> "
            "(source_binary_id IS NULL))) OR (released_at IS NOT NULL AND "
            "source_media_id IS NULL AND source_binary_id IS NULL))",
            name="ck_canvas_copy_source_pin",
        ),
        OPTIONS,
    )


class CanvasResourceChunk(Base):
    __tablename__ = "canvas_resource_chunks"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    upload_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_resource_uploads.id", ondelete="CASCADE", onupdate="RESTRICT"),
    )
    chunk_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    storage_locator: Mapped[str] = mapped_column(VARCHAR(700, collation="utf8mb4_0900_bin"))
    byte_size: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("upload_id", "chunk_index", name="uk_canvas_upload_chunk"),
        CheckConstraint("byte_size > 0 AND byte_size <= 8388608", name="ck_canvas_chunk_size"),
        OPTIONS,
    )


class CanvasResourceDeletion(CanvasAudit, Base):
    """Durable deletion outbox and tombstone for a retired upload identity."""

    __tablename__ = "canvas_resource_deletions"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    resource_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    upload_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    idempotency_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    storage_locator: Mapped[str] = mapped_column(VARCHAR(700, collation="utf8mb4_0900_bin"))
    chunk_count: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    status: Mapped[str] = mapped_column(VARCHAR(16))
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    error_code: Mapped[str | None] = mapped_column(VARCHAR(64))
    __table_args__ = (
        UniqueConstraint("resource_id", name="uk_canvas_deletion_resource"),
        UniqueConstraint("upload_id", name="uk_canvas_deletion_upload"),
        UniqueConstraint("user_id", "idempotency_hash", name="uk_canvas_deletion_identity"),
        Index("idx_canvas_deletion_due", "status", "next_attempt_at", "id"),
        CheckConstraint(
            "status IN ('pending','completed','retained')", name="ck_canvas_deletion_status"
        ),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status IN ('completed','retained') AND completed_at IS NOT NULL)",
            name="ck_canvas_deletion_completion",
        ),
        OPTIONS,
    )


class BinaryReferenceFields(CanvasChild):
    revision_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    owner_kind: Mapped[str] = mapped_column(VARCHAR(32))
    owner_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    slot: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    ordinal: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    binary_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_binary_resources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )


def binary_revision_parent():
    return ForeignKeyConstraint(
        ["canvas_id", "revision_id"],
        ["canvas_revisions.canvas_id", "canvas_revisions.id"],
        ondelete="CASCADE",
        onupdate="RESTRICT",
    )


class CanvasBinaryReference(BinaryReferenceFields, Base):
    __tablename__ = "canvas_binary_references"
    node_key: Mapped[str | None] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    __table_args__ = (
        canvas_parent(),
        binary_revision_parent(),
        UniqueConstraint(
            "canvas_id",
            "owner_kind",
            "owner_key",
            "slot",
            "ordinal",
            name="uk_canvas_binary_slot",
        ),
        ForeignKeyConstraint(
            ["canvas_id", "node_key"],
            ["canvas_nodes.canvas_id", "canvas_nodes.node_key"],
            name="fk_canvas_binary_owning_node",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        OPTIONS,
    )


class CanvasUserBinaryReference(BinaryReferenceFields, Base):
    __tablename__ = "canvas_user_binary_references"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    __table_args__ = (
        canvas_parent(),
        binary_revision_parent(),
        UniqueConstraint(
            "user_id",
            "canvas_id",
            "owner_kind",
            "owner_key",
            "slot",
            "ordinal",
            name="uk_canvas_user_binary_slot",
        ),
        OPTIONS,
    )
