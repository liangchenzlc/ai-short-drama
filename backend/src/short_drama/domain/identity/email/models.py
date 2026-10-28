from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, TEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class EmailChallenge(Base):
    __tablename__ = "email_challenges"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(VARCHAR(24))
    email: Mapped[str] = mapped_column(VARCHAR(254))
    proof_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"))
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    consumed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        Index("idx_challenges_user", "user_id", "purpose", "created_at"),
        CheckConstraint(
            "purpose IN ('registration','password_reset')", name="ck_challenge_purpose"
        ),
        OPTIONS,
    )


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    challenge_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("email_challenges.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    # Encrypted envelope includes recipient and code; wiped after sending or expiry.
    payload_cipher: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    status: Mapped[str] = mapped_column(VARCHAR(16), default="pending")
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    next_run_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (Index("idx_email_schedule", "status", "next_run_at"), OPTIONS)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    count: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    __table_args__ = (OPTIONS,)
