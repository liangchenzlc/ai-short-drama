from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    INTEGER,
    JSON,
    MEDIUMTEXT,
    TEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AIGenerationRecord(Base):
    __tablename__ = "ai_generation_records"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, primary_key=True, autoincrement=False
    )
    task_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    call_no: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    config_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON(), nullable=False)
    request_data: Mapped[dict] = mapped_column(JSON(), nullable=False)
    credential_cipher: Mapped[str | None] = mapped_column(TEXT(), nullable=True)
    adapter: Mapped[str | None] = mapped_column(
        VARCHAR(64, collation="utf8mb4_0900_bin"), nullable=True
    )
    provider_task_id: Mapped[str | None] = mapped_column(
        VARCHAR(255, collation="utf8mb4_0900_bin"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False, server_default=text("'prepared'")
    )
    text_content: Mapped[str | None] = mapped_column(MEDIUMTEXT(), nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSON(), nullable=True)
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
        UniqueConstraint("task_id", "call_no", name="uk_ai_records_call"),
        Index("idx_ai_records_config_time", "config_id", "created_at", "id"),
        Index("idx_ai_records_provider_task", "provider_task_id"),
        ForeignKeyConstraint(
            ["task_id"],
            ["async_tasks.id"],
            name="fk_ai_records_task",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["config_id"],
            ["ai_model_configs.id"],
            name="fk_ai_records_config",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("call_no > 0", name="ck_ai_records_call_no"),
        CheckConstraint(
            "status IN ('prepared','sent','succeeded','failed','unknown')",
            name="ck_ai_records_status",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL "
            "OR finished_at IS NULL OR finished_at >= started_at)",
            name="ck_ai_records_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )
