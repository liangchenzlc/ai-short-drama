from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AssetImageCandidate(Base):
    __tablename__ = "asset_image_candidates"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    media_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )

    __table_args__ = (
        UniqueConstraint("asset_id", "media_id", name="uk_asset_image_candidates_media"),
        Index("idx_asset_image_candidates_time", "asset_id", "created_at", "id"),
        Index("idx_asset_image_candidates_media", "media_id"),
        ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_image_candidates_asset",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_asset_image_candidates_media",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "素材参考图片候选",
        },
    )
