"""本人 BeefAPI 设备授权状态；密钥与 device code 均独立加密。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit
from .collaboration import OPTIONS


class CanvasBeefAPIConnection(CanvasAudit, Base):
    __tablename__ = "canvas_beefapi_connections"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    state_json: Mapped[dict] = mapped_column(JSON)
    secrets_cipher: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    next_poll_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    __table_args__ = (
        UniqueConstraint("user_id", name="uk_canvas_beefapi_user"),
        Index("idx_canvas_beefapi_poll", "next_poll_at", "lease_until", "id"),
        CheckConstraint("row_version > 0", name="ck_canvas_beefapi_version"),
        OPTIONS,
    )
