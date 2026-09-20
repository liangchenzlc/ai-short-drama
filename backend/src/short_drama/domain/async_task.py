from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import (
    BIGINT,
    CHAR,
    DATETIME,
    JSON,
    TINYINT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AsyncTask(Base):
    __tablename__ = "async_tasks"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, primary_key=True, autoincrement=False
    )
    service_type: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False, server_default=text("'queued'")
    )
    idempotency_key: Mapped[str] = mapped_column(
        VARCHAR(128, collation="utf8mb4_0900_bin"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(
        CHAR(64, collation="ascii_bin", charset="ascii"), nullable=False
    )
    retry_of_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    next_action: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    message_status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False, server_default=text("'pending'")
    )
    message_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    publish_count: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("0")
    )
    lock_token: Mapped[str | None] = mapped_column(
        VARCHAR(64, collation="ascii_bin", charset="ascii"), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    cancel_requested: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("0")
    )
    error: Mapped[dict | None] = mapped_column(JSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_async_tasks_idempotency"),
        Index("idx_async_tasks_publish", "message_status", "next_run_at", "id"),
        Index("idx_async_tasks_lock", "message_status", "locked_until", "id"),
        Index("idx_async_tasks_history", "service_type", "status", "created_at", "id"),
        Index("idx_async_tasks_retry", "retry_of_id"),
        ForeignKeyConstraint(
            ["retry_of_id"],
            ["async_tasks.id"],
            name="fk_async_tasks_retry",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("service_type IN ('text','image','video')", name="ck_async_tasks_type"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_async_tasks_status",
        ),
        CheckConstraint(
            "next_action IS NULL OR next_action IN ('submit','poll','save')",
            name="ck_async_tasks_action",
        ),
        CheckConstraint(
            "message_status IN ('pending','publishing','published','idle')",
            name="ck_async_tasks_message",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(idempotency_key)) > 0 AND CHAR_LENGTH(request_hash) = 64 AND "
            "message_version > 0",
            name="ck_async_tasks_required",
        ),
        CheckConstraint(
            "retry_of_id IS NULL OR retry_of_id <> id", name="ck_async_tasks_retry_self"
        ),
        CheckConstraint("cancel_requested IN (0,1)", name="ck_async_tasks_cancel"),
        CheckConstraint(
            "(lock_token IS NULL AND locked_until IS NULL) OR (lock_token IS NOT NULL AND "
            "locked_until IS NOT NULL)",
            name="ck_async_tasks_lock_pair",
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL) OR "
            "(status IN ('queued','running') AND finished_at IS NULL)",
            name="ck_async_tasks_terminal_time",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL "
            "OR finished_at IS NULL OR finished_at >= started_at)",
            name="ck_async_tasks_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )
