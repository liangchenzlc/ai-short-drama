from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(VARCHAR(32, collation="utf8mb4_0900_bin"), unique=True)
    display_name: Mapped[str] = mapped_column(VARCHAR(80))
    email: Mapped[str] = mapped_column(VARCHAR(254, collation="utf8mb4_0900_bin"), unique=True)
    password_hash: Mapped[str] = mapped_column(VARCHAR(255))
    status: Mapped[str] = mapped_column(VARCHAR(16), default="active")
    email_verified_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="ck_users_status"),
        OPTIONS,
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), unique=True)
    csrf_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (Index("idx_sessions_user", "user_id", "expires_at"), OPTIONS)
