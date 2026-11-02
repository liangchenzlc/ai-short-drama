from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    object_type: Mapped[str] = mapped_column(VARCHAR(64))
    object_id: Mapped[str] = mapped_column(VARCHAR(32))
    action: Mapped[str] = mapped_column(VARCHAR(32))
    request_id: Mapped[str] = mapped_column(VARCHAR(64))
    source: Mapped[str | None] = mapped_column(VARCHAR(16), nullable=True)
    token_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("mcp_access_tokens.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        Index("idx_audit_project", "project_id", "created_at", "id"),
        CheckConstraint("source IN ('browser','assistant','mcp')", name="ck_audit_source"),
        OPTIONS,
    )
