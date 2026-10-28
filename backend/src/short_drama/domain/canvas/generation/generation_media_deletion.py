"""已处置画布生成媒体的独立退役证据；不伪造上传身份。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, event
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column, object_session

from short_drama.core.exceptions import WorkflowError
from short_drama.domain.base import Base
from short_drama.domain.canvas.document.canvas import CanvasChild, canvas_parent
from short_drama.domain.options import OPTIONS


class CanvasGenerationMediaDeletion(CanvasChild, Base):
    __tablename__ = "canvas_generation_media_deletions"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    purge_receipt_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_write_receipts.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    task_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("async_tasks.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    record_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("ai_generation_records.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    # 产物索引和规范媒体会同事务删除，原ID作为墓碑保存而非外键。
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    resource_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    output_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    media_type: Mapped[str] = mapped_column(VARCHAR(16))
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    extension: Mapped[str] = mapped_column(VARCHAR(16, collation="ascii_bin", charset="ascii"))
    storage_locator: Mapped[str] = mapped_column(VARCHAR(700, collation="utf8mb4_0900_bin"))
    status: Mapped[str] = mapped_column(VARCHAR(16))
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    error_code: Mapped[str | None] = mapped_column(VARCHAR(64))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("resource_id", name="uk_canvas_generation_delete_resource"),
        UniqueConstraint("asset_id", name="uk_canvas_generation_delete_asset"),
        UniqueConstraint("record_id", "output_index", name="uk_canvas_generation_delete_output"),
        Index("idx_canvas_generation_delete_due", "status", "next_attempt_at", "id"),
        CheckConstraint(
            "media_type IN ('image','video','audio') AND output_index > 0 "
            "AND asset_id > 0 AND resource_id > 0 AND CHAR_LENGTH(checksum_sha256) = 64 "
            "AND CHAR_LENGTH(TRIM(extension)) > 0 AND CHAR_LENGTH(TRIM(storage_locator)) > 0",
            name="ck_canvas_generation_delete_origin",
        ),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL)",
            name="ck_canvas_generation_delete_state",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_canvas_generation_delete_time"),
        OPTIONS,
    )


@event.listens_for(CanvasGenerationMediaDeletion, "before_insert")
@event.listens_for(CanvasGenerationMediaDeletion, "before_update")
@event.listens_for(CanvasGenerationMediaDeletion, "before_delete")
def _internal_write(_mapper, _connection, entity: CanvasGenerationMediaDeletion) -> None:
    session = object_session(entity)
    if session is not None and session.info.get("actor"):
        raise WorkflowError(
            "canvas_generation_deletion_internal", "生成媒体回收记录仅允许内部维护写入", 403
        )
