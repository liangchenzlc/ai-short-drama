"""Identity, project membership and revocable email proofs. No plaintext bearer tokens."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, TEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
    "mysql_row_format": "DYNAMIC",
}


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


class EmailChallenge(Base):
    __tablename__ = "email_challenges"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(VARCHAR(24))
    invitation_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("project_invitations.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(VARCHAR(254))
    proof_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"))
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    consumed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        Index("idx_challenges_user", "user_id", "purpose", "created_at"),
        CheckConstraint(
            "purpose IN ('registration','invitation','password_reset')", name="ck_challenge_purpose"
        ),
        CheckConstraint(
            "(purpose = 'invitation') = (invitation_id IS NOT NULL)", name="ck_challenge_invitation"
        ),
        OPTIONS,
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    status: Mapped[str] = mapped_column(VARCHAR(16), default="active")
    joined_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    removed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uk_project_member"),
        Index("idx_members_user", "user_id", "status", "project_id"),
        CheckConstraint("status IN ('active','removed','left')", name="ck_member_status"),
        OPTIONS,
    )


class ProjectInvitation(Base):
    __tablename__ = "project_invitations"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    invited_by: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    target_user_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    target_email: Mapped[str | None] = mapped_column(VARCHAR(254), nullable=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), unique=True)
    status: Mapped[str] = mapped_column(VARCHAR(16), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    accepted_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    accepted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    __table_args__ = (
        Index("idx_invitations_project", "project_id", "status"),
        CheckConstraint(
            "(target_user_id IS NULL) <> (target_email IS NULL)", name="ck_invitation_target"
        ),
        CheckConstraint("status IN ('pending','accepted','revoked')", name="ck_invitation_status"),
        OPTIONS,
    )


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
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (Index("idx_audit_project", "project_id", "created_at", "id"), OPTIONS)


class UserModelPreference(Base):
    __tablename__ = "user_model_preferences"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    context_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    config_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("ai_model_configs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        UniqueConstraint("user_id", "context_key", name="uk_model_preference"),
        OPTIONS,
    )


class UserProjectState(Base):
    __tablename__ = "user_project_states"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    last_opened_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uk_user_project_state"),
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
        CheckConstraint("source_type IN ('asset','media')", name="ck_import_type"),
        CheckConstraint(
            "status IN ('pending','copying','succeeded','failed','cancelled')",
            name="ck_import_status",
        ),
        OPTIONS,
    )
