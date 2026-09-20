from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ShotAsset(Base):
    __tablename__ = "shot_assets"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    episode_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="必须等于镜头所属分集"
    )
    asset_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="镜头引用的素材"
    )
    shot_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment="所属镜头")
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="创建时间；正常写入非空，历史未知可显式NULL",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="最近修改时间；正常写入非空，历史未知可显式NULL",
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="创建人；预留用户ID，暂不设外键",
    )
    updated_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="最近修改人；预留用户ID，暂不设外键",
    )

    __table_args__ = (
        UniqueConstraint("shot_id", "asset_id", name="uk_shot_assets_asset"),
        Index("idx_shot_assets_episode", "episode_id", "shot_id"),
        Index("idx_shot_assets_asset_id", "asset_id"),
        Index("idx_shot_assets_shot_id_episode_id", "shot_id", "episode_id"),
        ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_shot_assets_asset_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["shot_id", "episode_id"],
            ["shot_scripts.id", "shot_scripts.episode_id"],
            name="fk_shot_assets_shot_episode",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_shot_assets_audit_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "分镜素材关联",
        },
    )
