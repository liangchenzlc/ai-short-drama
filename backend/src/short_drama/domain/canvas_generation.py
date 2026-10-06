"""私人画布任务出处与不可变产物；共享图只保存已绑定的作品。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasChild, canvas_parent
from .collaboration import OPTIONS


class CanvasTaskBinding(CanvasChild, Base):
    __tablename__ = "canvas_task_bindings"
    initiated_by: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    async_task_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("async_tasks.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    # 占位节点可晚于任务入库；删除后保留原键，不以外键强迫复活节点。
    node_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    source_node_key: Mapped[str | None] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    client_operation_id: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    context_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("async_task_id", name="uk_canvas_task_async"),
        UniqueConstraint("initiated_by", "client_operation_id", name="uk_canvas_task_operation"),
        UniqueConstraint("project_id", "canvas_id", "id", name="uk_canvas_task_scope"),
        Index("idx_canvas_task_author", "initiated_by", "canvas_id", "created_at", "id"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(node_key)) > 0 AND CHAR_LENGTH(TRIM(client_operation_id)) > 0 "
            "AND CHAR_LENGTH(request_hash) = 64 AND CHAR_LENGTH(context_hash) = 64",
            name="ck_canvas_task_required",
        ),
        OPTIONS,
    )


def task_parent():
    return ForeignKeyConstraint(
        ["project_id", "canvas_id", "task_binding_id"],
        [
            "canvas_task_bindings.project_id",
            "canvas_task_bindings.canvas_id",
            "canvas_task_bindings.id",
        ],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class CanvasTaskMediaReference(CanvasChild, Base):
    __tablename__ = "canvas_task_media_references"
    task_binding_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    role: Mapped[str] = mapped_column(VARCHAR(64, collation="utf8mb4_0900_bin"))
    ordinal: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    __table_args__ = (
        task_parent(),
        UniqueConstraint("task_binding_id", "role", "ordinal", name="uk_canvas_task_media_slot"),
        Index("idx_canvas_task_media", "media_id"),
        CheckConstraint("CHAR_LENGTH(TRIM(role)) > 0", name="ck_canvas_task_media_role"),
        OPTIONS,
    )


class CanvasResult(CanvasChild, Base):
    __tablename__ = "canvas_results"
    task_binding_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    result_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    kind: Mapped[str] = mapped_column(VARCHAR(16, collation="utf8mb4_0900_bin"))
    content_json: Mapped[dict] = mapped_column(JSON)
    media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    attachment_status: Mapped[str] = mapped_column(VARCHAR(16, collation="utf8mb4_0900_bin"))
    attachment_receipt_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_write_receipts.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    attached_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    __table_args__ = (
        task_parent(),
        UniqueConstraint("task_binding_id", "result_index", name="uk_canvas_result_output"),
        Index("idx_canvas_result_author", "created_by", "canvas_id", "attachment_status", "id"),
        Index("idx_canvas_result_media", "media_id"),
        CheckConstraint("kind IN ('text','image','video','audio')", name="ck_canvas_result_kind"),
        CheckConstraint(
            "(kind = 'text' AND media_id IS NULL) OR (kind <> 'text' AND media_id IS NOT NULL)",
            name="ck_canvas_result_media",
        ),
        CheckConstraint(
            "(attachment_status = 'detached' AND attachment_receipt_id IS NULL "
            "AND attached_at IS NULL) OR (attachment_status = 'attached' "
            "AND attachment_receipt_id IS NOT NULL AND attached_at IS NOT NULL)",
            name="ck_canvas_result_attachment",
        ),
        CheckConstraint("row_version > 0", name="ck_canvas_result_version"),
        OPTIONS,
    )
