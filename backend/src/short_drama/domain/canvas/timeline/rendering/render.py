"""私人画布成片任务；与AI供应商及分集合成任务独立。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.canvas.document.canvas import CanvasChild, canvas_parent
from short_drama.domain.options import OPTIONS


class CanvasRenderJob(CanvasChild, Base):
    __tablename__ = "canvas_render_jobs"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    client_operation_id: Mapped[str] = mapped_column(
        VARCHAR(128, charset="ascii", collation="ascii_bin")
    )
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    request_json: Mapped[dict] = mapped_column(JSON)
    plan_json: Mapped[dict] = mapped_column(JSON)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    canvas_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    status: Mapped[str] = mapped_column(VARCHAR(16), default="queued")
    stage: Mapped[str] = mapped_column(VARCHAR(64), default="排队中")
    progress: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=5)
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    message_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    next_run_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    lease_token: Mapped[str | None] = mapped_column(
        CHAR(32, charset="ascii", collation="ascii_bin")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    cancel_requested: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    output_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    disposal_receipt_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "canvas_write_receipts.id",
            name="fk_canvas_render_disposal",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
    )
    result_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    error_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("user_id", "client_operation_id", name="uk_canvas_render_operation"),
        UniqueConstraint("id", "user_id", "project_id", "canvas_id", name="uk_canvas_render_scope"),
        Index("idx_canvas_render_author", "user_id", "updated_at", "id"),
        Index("idx_canvas_render_schedule", "status", "next_run_at", "id"),
        Index("idx_canvas_render_disposal", "disposal_receipt_id"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_canvas_render_status",
        ),
        CheckConstraint(
            "progress <= 100 AND cancel_requested IN (0,1) AND message_version > 0 "
            "AND canvas_version > 0",
            name="ck_canvas_render_numbers",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND locked_until IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND locked_until IS NULL)",
            name="ck_canvas_render_lease",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR ((output_media_id IS NOT NULL "
            "OR disposal_receipt_id IS NOT NULL) AND result_json IS NOT NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_canvas_render_output",
        ),
        CheckConstraint(
            "disposal_receipt_id IS NULL OR (status IN ('succeeded','failed','cancelled') "
            "AND lease_token IS NULL AND locked_until IS NULL AND JSON_LENGTH(request_json) = 0 "
            "AND JSON_LENGTH(source_snapshot) = 0 AND JSON_LENGTH(plan_json) = 0 AND 0 <> COALESCE("
            "JSON_UNQUOTE(JSON_EXTRACT(error_json, '$.code')) = 'canvas_work_disposed', FALSE))",
            name="ck_canvas_render_disposal",
        ),
        CheckConstraint(
            "JSON_TYPE(request_json) = 'OBJECT' AND JSON_TYPE(plan_json) = 'OBJECT' "
            "AND JSON_TYPE(source_snapshot) = 'OBJECT'",
            name="ck_canvas_render_snapshot",
        ),
        CheckConstraint(
            "(result_json IS NULL OR JSON_TYPE(result_json) = 'OBJECT') AND "
            "(error_json IS NULL OR JSON_TYPE(error_json) = 'OBJECT')",
            name="ck_canvas_render_result",
        ),
        CheckConstraint(
            "CHAR_LENGTH(client_operation_id) >= 8 AND CHAR_LENGTH(request_hash) = 64",
            name="ck_canvas_render_identity",
        ),
        {**OPTIONS, "comment": "本人画布时间线快照与独立本地成片任务"},
    )


class CanvasRenderMediaReference(CanvasChild, Base):
    __tablename__ = "canvas_render_media_references"
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    job_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    ordinal: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        canvas_parent(),
        ForeignKeyConstraint(
            ["job_id", "user_id", "project_id", "canvas_id"],
            [
                "canvas_render_jobs.id",
                "canvas_render_jobs.user_id",
                "canvas_render_jobs.project_id",
                "canvas_render_jobs.canvas_id",
            ],
            name="fk_canvas_render_source_job",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("job_id", "source_key", name="uk_canvas_render_source"),
        UniqueConstraint("job_id", "ordinal", name="uk_canvas_render_source_order"),
        {**OPTIONS, "comment": "冻结渲染来源的稳定媒体引用，保护排队与历史任务"},
    )
