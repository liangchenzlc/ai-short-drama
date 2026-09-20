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
    MEDIUMTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class MediaRecycleBin(Base):
    __tablename__ = "media_recycle_bin"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；本次进入回收站的记录标识",
    )
    shot_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="来源镜头；分集、项目通过镜头联查"
    )
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="被废弃的图片或视频；进入回收站不删除文件"
    )
    model_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="生成该结果时使用的模型配置；手动导入或未知时可空",
    )
    prompt: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="生成该结果时的提示词"
    )
    resolution: Mapped[str] = mapped_column(
        VARCHAR(32, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="生成该结果时的请求清晰度",
    )
    layout: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        nullable=True,
        server_default=text("NULL"),
        comment="图片布局：single / four / five / nine；视频为空",
    )
    aspect: Mapped[str | None] = mapped_column(
        VARCHAR(8, collation="utf8mb4_0900_bin"),
        nullable=True,
        server_default=text("NULL"),
        comment="图片请求画幅：16:9、9:16、1:1、4:3或3:4；视频为空",
    )
    duration: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="视频请求时长，单位毫秒；图片为空",
    )
    reason: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="用户废弃 / 被新确认结果替换",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="本次进入回收站的时间，不是媒体生成时间；正常写入非空，历史未知可显式NULL",
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="执行废弃或确认替换的用户；预留用户ID，暂不设外键",
    )

    __table_args__ = (
        UniqueConstraint("shot_id", "media_id", name="uk_recycle_shot_media"),
        Index("idx_recycle_created", "created_at", "id"),
        Index("idx_media_recycle_bin_media_id", "media_id"),
        Index("idx_media_recycle_bin_model_id", "model_id"),
        ForeignKeyConstraint(
            ["shot_id"],
            ["shot_scripts.id"],
            name="fk_media_recycle_bin_shot_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_media_recycle_bin_media_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_id"],
            ["ai_model_configs.id"],
            name="fk_media_recycle_bin_model_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`reason` IN ('discarded', 'replaced')", name="ck_recycle_reason"),
        CheckConstraint("CHAR_LENGTH(TRIM(`resolution`)) > 0", name="ck_recycle_resolution"),
        CheckConstraint(
            "`layout` IS NULL OR `layout` IN ('single', 'four', 'five', 'nine')",
            name="ck_recycle_layout",
        ),
        CheckConstraint(
            "`aspect` IS NULL OR `aspect` IN ('16:9', '9:16', '1:1', '4:3', '3:4')",
            name="ck_recycle_aspect",
        ),
        CheckConstraint("`duration` IS NULL OR `duration` > 0", name="ck_recycle_duration"),
        CheckConstraint(
            "(`layout` IS NOT NULL AND `aspect` IS NOT NULL AND `duration` IS NULL) "
            "OR (`layout` IS NULL AND `aspect` IS NULL AND `duration` IS NOT NULL)",
            name="ck_recycle_parameter_shape",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "分镜媒体回收站",
        },
    )
