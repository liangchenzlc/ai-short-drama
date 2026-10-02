"""Durable orchestration; child AsyncTasks remain the generation authority."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .collaboration import ResourceScope

OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
    "mysql_row_format": "DYNAMIC",
}


class GenerationBatchJob(ResourceScope, Base):
    __tablename__ = "generation_batches"
    initiated_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    scene: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    config_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "ai_model_configs.id", name="fk_batch_config", ondelete="RESTRICT", onupdate="RESTRICT"
        ),
        nullable=False,
    )
    config_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        VARCHAR(128, collation="utf8mb4_0900_bin"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), nullable=False)
    retry_of_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "(scope_user_id IS NULL) <> (project_id IS NULL)", name="ck_generation_batch_scope"
        ),
        UniqueConstraint("idempotency_key", name="uk_generation_batch_key"),
        Index("idx_generation_batch_schedule", "config_id", "status", "id"),
        CheckConstraint(
            "scene IN ('asset_image','shot_image','shot_video')", name="ck_generation_batch_scene"
        ),
        CheckConstraint(
            "status IN ('running','paused','needs_review',"
            "'succeeded','partial','failed','cancelled')",
            name="ck_generation_batch_status",
        ),
        OPTIONS,
    )


class GenerationBatchItem(Base):
    __tablename__ = "generation_batch_items"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    batch_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "generation_batches.id",
            name="fk_batch_item_batch",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    task_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "async_tasks.id", name="fk_batch_item_task", ondelete="RESTRICT", onupdate="RESTRICT"
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (
        UniqueConstraint("batch_id", "source_id", name="uk_batch_item_source"),
        UniqueConstraint("task_id", name="uk_batch_item_task"),
        Index("idx_batch_item_source", "source_id", "status"),
        CheckConstraint(
            "status IN ('waiting','active','blocked','succeeded',"
            "'failed','cancelled','needs_review')",
            name="ck_batch_item_status",
        ),
        OPTIONS,
    )
