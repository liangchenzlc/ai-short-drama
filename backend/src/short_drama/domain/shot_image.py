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
    CHAR,
    DATETIME,
    MEDIUMTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ShotImage(Base):
    __tablename__ = "shot_images"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    episode_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="所属分集，与镜头一致"
    )
    shot_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment="所属镜头")
    layout: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        nullable=False,
        server_default=text("'single'"),
        comment="单图 / 四格 / 五格 / 九格",
    )
    aspect: Mapped[str] = mapped_column(
        VARCHAR(8, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="具体画幅：16:9、9:16、1:1、4:3或3:4",
    )
    resolution: Mapped[str] = mapped_column(
        VARCHAR(32, collation="utf8mb4_0900_bin"),
        nullable=False,
        server_default=text("'2K'"),
        comment="请求清晰度，如1K、2K、4K",
    )
    prompt: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="本次生图提示词"
    )
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="用户确认采用的图片，不能为空"
    )
    state: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        nullable=False,
        server_default=text("'confirmed'"),
        comment="已确认；沿用指定字段，不表示生成任务状态",
    )
    model_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="生图配置；手动导入时可空",
    )
    context_hash: Mapped[str | None] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_bin"),
        nullable=True,
        server_default=text("NULL"),
        comment="采用时的shot-context-v1摘要",
    )
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
        UniqueConstraint("shot_id", name="uk_shot_images_shot"),
        Index("idx_shot_images_episode", "episode_id", "shot_id"),
        Index("idx_shot_images_media_id", "media_id"),
        Index("idx_shot_images_model_id", "model_id"),
        Index("idx_shot_images_shot_id_episode_id", "shot_id", "episode_id"),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_shot_images_media_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_id"],
            ["ai_model_configs.id"],
            name="fk_shot_images_model_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["shot_id", "episode_id"],
            ["shot_scripts.id", "shot_scripts.episode_id"],
            name="fk_shot_images_shot_episode",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_shot_images_audit_time",
        ),
        CheckConstraint("`state` = 'confirmed'", name="ck_shot_images_state"),
        CheckConstraint("CHAR_LENGTH(TRIM(`resolution`)) > 0", name="ck_shot_images_resolution"),
        CheckConstraint(
            "`layout` IN ('single', 'four', 'five', 'nine')", name="ck_shot_images_layout"
        ),
        CheckConstraint(
            "`aspect` IN ('16:9', '9:16', '1:1', '4:3', '3:4')", name="ck_shot_images_aspect"
        ),
        CheckConstraint(
            "`context_hash` IS NULL OR CHAR_LENGTH(`context_hash`) = 64",
            name="ck_shot_images_context_hash",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "分镜图",
        },
    )
