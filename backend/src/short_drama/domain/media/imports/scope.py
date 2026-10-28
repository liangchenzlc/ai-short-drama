from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class ResourceScope:
    scope_user_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )


class ResourceImport(Base):
    __tablename__ = "resource_imports"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    initiated_by: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), unique=True)
    source_type: Mapped[str] = mapped_column(VARCHAR(16))
    source_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(VARCHAR(16), default="pending")
    result_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    cleanup_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    __table_args__ = (
        Index("idx_import_schedule", "status", "created_at"),
        CheckConstraint("source_type = 'media'", name="ck_import_type"),
        CheckConstraint(
            "status IN ('pending','copying','succeeded','failed','cancelled')",
            name="ck_import_status",
        ),
        OPTIONS,
    )
