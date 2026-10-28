"""私人供应商正文增量；序号属于稳定画布任务，不属于浏览器连接。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.canvas.document.canvas import CanvasChild
from short_drama.domain.canvas.generation.generation import task_parent
from short_drama.domain.options import OPTIONS


class CanvasTaskTextDelta(CanvasChild, Base):
    __tablename__ = "canvas_task_text_deltas"
    task_binding_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    generation_record_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("ai_generation_records.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    sequence: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    content: Mapped[str] = mapped_column(MEDIUMTEXT)
    byte_count: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        task_parent(),
        UniqueConstraint("task_binding_id", "sequence", name="uk_canvas_text_sequence"),
        Index("idx_canvas_text_expiry", "expires_at", "id"),
        Index("idx_canvas_text_author", "created_by", "expires_at"),
        CheckConstraint(
            "sequence BETWEEN 1 AND 4096 AND byte_count BETWEEN 1 AND 65536 "
            "AND OCTET_LENGTH(content) = byte_count",
            name="ck_canvas_text_delta",
        ),
        OPTIONS,
    )
