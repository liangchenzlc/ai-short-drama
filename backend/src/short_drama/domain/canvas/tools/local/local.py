"""本人本地媒体执行任务；冻结来源，与供应商生成记录独立。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.canvas.document.canvas import CanvasAudit, canvas_parent
from short_drama.domain.options import OPTIONS


class CanvasLocalJob(CanvasAudit, Base):
    __tablename__ = "canvas_local_jobs"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    project_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    canvas_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    task_type: Mapped[str] = mapped_column(VARCHAR(32, collation="utf8mb4_0900_bin"))
    client_operation_id: Mapped[str] = mapped_column(
        VARCHAR(128, charset="ascii", collation="ascii_bin")
    )
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    request_json: Mapped[dict] = mapped_column(JSON)
    source_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "media_files.id",
            name="fk_canvas_local_source",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
    )
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
            name="fk_canvas_local_disposal",
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
        UniqueConstraint("user_id", "client_operation_id", name="uk_canvas_local_operation"),
        Index("idx_canvas_local_author", "user_id", "updated_at", "id"),
        Index("idx_canvas_local_schedule", "status", "next_run_at", "id"),
        Index("idx_canvas_local_disposal", "disposal_receipt_id"),
        CheckConstraint(
            "task_type IN ('timeline_transcription','depth_capture','resource_playback')",
            name="ck_canvas_local_type",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_canvas_local_status",
        ),
        CheckConstraint(
            "progress <= 100 AND cancel_requested IN (0,1) AND message_version > 0 AND "
            "((project_id IS NULL AND canvas_id IS NULL AND canvas_version = 0) OR "
            "(project_id IS NOT NULL AND canvas_id IS NOT NULL AND canvas_version > 0))",
            name="ck_canvas_local_numbers",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND locked_until IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND locked_until IS NULL)",
            name="ck_canvas_local_lease",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR (result_json IS NOT NULL AND finished_at IS NOT NULL "
            "AND (task_type NOT IN ('depth_capture','resource_playback') "
            "OR output_media_id IS NOT NULL "
            "OR disposal_receipt_id IS NOT NULL))",
            name="ck_canvas_local_output",
        ),
        CheckConstraint(
            "source_media_id IS NOT NULL OR disposal_receipt_id IS NOT NULL",
            name="ck_canvas_local_source",
        ),
        CheckConstraint(
            "disposal_receipt_id IS NULL OR (project_id IS NOT NULL AND canvas_id IS NOT NULL "
            "AND status IN ('succeeded','failed','cancelled') AND lease_token IS NULL "
            "AND locked_until IS NULL AND JSON_LENGTH(request_json) = 0 "
            "AND JSON_LENGTH(source_snapshot) = 0 AND 0 <> COALESCE("
            "JSON_UNQUOTE(JSON_EXTRACT(error_json, '$.code')) = 'canvas_work_disposed', FALSE))",
            name="ck_canvas_local_disposal",
        ),
        CheckConstraint(
            "JSON_TYPE(request_json) = 'OBJECT' AND JSON_TYPE(source_snapshot) = 'OBJECT'",
            name="ck_canvas_local_snapshot",
        ),
        CheckConstraint(
            "(result_json IS NULL OR JSON_TYPE(result_json) = 'OBJECT') AND "
            "(error_json IS NULL OR JSON_TYPE(error_json) = 'OBJECT')",
            name="ck_canvas_local_result",
        ),
        CheckConstraint(
            "CHAR_LENGTH(client_operation_id) >= 8 AND CHAR_LENGTH(request_hash) = 64",
            name="ck_canvas_local_identity",
        ),
        {**OPTIONS, "comment": "本人媒体转写、深度捕捉与兼容播放的冻结来源及执行回执"},
    )
