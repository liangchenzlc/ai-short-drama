"""Private preparation state for an atomic first canvas document."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit
from .collaboration import OPTIONS


class CanvasCreationAttempt(CanvasAudit, Base):
    __tablename__ = "canvas_creation_attempts"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(
        VARCHAR(128, charset="ascii", collation="ascii_bin")
    )
    operation_kind: Mapped[str] = mapped_column(VARCHAR(32))
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    request_json: Mapped[dict] = mapped_column(JSON)
    source_key: Mapped[str] = mapped_column(VARCHAR(64, collation="utf8mb4_0900_bin"))
    target_project_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    status: Mapped[str] = mapped_column(VARCHAR(16))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    result_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uk_canvas_creation_identity"),
        UniqueConstraint("user_id", "source_key", name="uk_canvas_creation_source_key"),
        UniqueConstraint("id", "user_id", name="uk_canvas_creation_owner"),
        Index("idx_canvas_creation_expiry", "status", "expires_at"),
        CheckConstraint(
            "(operation_kind = 'canvas.workspace.create' AND target_project_id IS NULL) OR "
            "(operation_kind = 'canvas.create' AND target_project_id IS NOT NULL)",
            name="ck_canvas_creation_destination",
        ),
        CheckConstraint(
            "(status = 'pending' AND result_json IS NULL) OR "
            "(status = 'ready' AND result_json IS NOT NULL)",
            name="ck_canvas_creation_result",
        ),
        CheckConstraint(
            "CHAR_LENGTH(request_hash) = 64 AND CHAR_LENGTH(source_key) > 0",
            name="ck_canvas_creation_identity",
        ),
        OPTIONS,
    )


class CanvasCreationResource(CanvasAudit, Base):
    __tablename__ = "canvas_creation_resources"
    attempt_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source_resource_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    source_binary_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_binary_resources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    target_resource_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    storage_locator: Mapped[str] = mapped_column(VARCHAR(700, collation="utf8mb4_0900_bin"))
    status: Mapped[str] = mapped_column(VARCHAR(16))
    copied_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    released_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id", "user_id"],
            ["canvas_creation_attempts.id", "canvas_creation_attempts.user_id"],
            name="fk_canvas_creation_resource_owner",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("attempt_id", "source_resource_id", name="uk_canvas_creation_resource"),
        UniqueConstraint("target_resource_id", name="uk_canvas_creation_target"),
        UniqueConstraint("storage_locator", name="uk_canvas_creation_locator"),
        CheckConstraint(
            "(released_at IS NULL AND ((source_media_id IS NULL) <> "
            "(source_binary_id IS NULL))) OR (released_at IS NOT NULL AND "
            "source_media_id IS NULL AND source_binary_id IS NULL)",
            name="ck_canvas_creation_source_pin",
        ),
        CheckConstraint(
            "(status = 'pending' AND copied_at IS NULL) OR "
            "(status IN ('copied','attached') AND copied_at IS NOT NULL "
            "AND released_at IS NOT NULL)",
            name="ck_canvas_creation_resource_state",
        ),
        CheckConstraint(
            "source_resource_id > 0 AND target_resource_id > 0",
            name="ck_canvas_creation_resource_ids",
        ),
        OPTIONS,
    )
