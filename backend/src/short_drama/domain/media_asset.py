from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    INTEGER,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, primary_key=True, autoincrement=False
    )
    record_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    output_index: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    media_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    media_type: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False
    )
    name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )

    __table_args__ = (
        UniqueConstraint("record_id", "output_index", name="uk_media_assets_record_output"),
        UniqueConstraint("media_id", name="uk_media_assets_media"),
        Index("idx_media_assets_type_time", "media_type", "created_at", "id"),
        ForeignKeyConstraint(
            ["record_id"],
            ["ai_generation_records.id"],
            name="fk_media_assets_record",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_media_assets_media",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("media_type IN ('image','video')", name="ck_media_assets_type"),
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_media_assets_name"),
        CheckConstraint("output_index > 0 AND row_version > 0", name="ck_media_assets_numbers"),
        CheckConstraint("updated_at >= created_at", name="ck_media_assets_time"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )
