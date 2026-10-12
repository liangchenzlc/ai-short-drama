"""角色不可变作品版本及按用途、顺序保存的真实媒体引用。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_row_format": "DYNAMIC",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class AssetVersion(Base):
    __tablename__ = "asset_versions"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    source_row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON(), nullable=False)
    content_hash: Mapped[str] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_bin"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True, server_default=text("NULL")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_versions_asset",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("asset_id", "id", name="uk_asset_versions_owner_id"),
        UniqueConstraint("asset_id", "version", name="uk_asset_versions_version"),
        CheckConstraint("version > 0", name="ck_asset_versions_version"),
        CheckConstraint("source_row_version > 0", name="ck_asset_versions_source_version"),
        CheckConstraint("JSON_TYPE(snapshot_json) = 'OBJECT'", name="ck_asset_versions_snapshot"),
        CheckConstraint(
            "CHAR_LENGTH(content_hash) = 64",
            name="ck_asset_versions_hash",
        ),
        {**OPTIONS, "comment": "角色不可变作品版本；审计不编造旧历史"},
    )


class AssetVersionMediaReference(Base):
    __tablename__ = "asset_version_media_references"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    version_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    purpose: Mapped[str] = mapped_column(VARCHAR(24, collation="utf8mb4_0900_bin"), nullable=False)
    role: Mapped[str] = mapped_column(
        VARCHAR(32, collation="utf8mb4_0900_bin"), nullable=False, server_default=text("''")
    )
    ordinal: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    media_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id", "version_id"],
            ["asset_versions.asset_id", "asset_versions.id"],
            name="fk_asset_version_media_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_asset_version_media_file",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "asset_id", "version_id", "purpose", "ordinal", name="uk_asset_version_media_slot"
        ),
        Index("idx_asset_version_media_file", "media_id"),
        CheckConstraint("ordinal >= 0", name="ck_asset_version_media_ordinal"),
        CheckConstraint(
            "(purpose = 'representation' AND `role` IN "
            "('primary','front','side','back','turnaround_sheet','expression_sheet')) OR "
            "(purpose = 'generation_reference' AND `role` = '')",
            name="ck_asset_version_media_purpose_role",
        ),
        {**OPTIONS, "comment": "角色版本表示图及生成参考；真实媒体外键保护历史"},
    )
