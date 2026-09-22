from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    CHAR,
    DATETIME,
    INTEGER,
    JSON,
    MEDIUMTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ShotScript(Base):
    __tablename__ = "shot_scripts"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    episode_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="所属分集"
    )
    position: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, comment="分集内镜头顺序"
    )
    script: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="分镜脚本正文"
    )
    duration_ms: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        server_default=text("3000"),
        comment="建议镜头时长（毫秒）",
    )
    source_excerpt: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="生成分镜的连续剧本原文依据"
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1"), comment="单镜头并发版本"
    )
    image_settings: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
        server_default=text("NULL"),
        comment="下一次生图设置",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, server_default=text("NULL"), comment="归档时间"
    )
    active_position: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        Computed("CASE WHEN `deleted_at` IS NULL THEN `position` ELSE NULL END", persisted=True),
        nullable=True,
    )
    creation_key: Mapped[str | None] = mapped_column(
        VARCHAR(128, collation="utf8mb4_0900_bin"), nullable=True, server_default=text("NULL")
    )
    creation_hash: Mapped[str | None] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_bin"), nullable=True, server_default=text("NULL")
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
        UniqueConstraint("episode_id", "active_position", name="uk_shots_episode_active_position"),
        UniqueConstraint("creation_key", name="uk_shots_creation_key"),
        UniqueConstraint("id", "episode_id", name="uk_shots_id_episode"),
        Index(
            "idx_shots_episode_deleted_position",
            "episode_id",
            "deleted_at",
            "position",
            "id",
        ),
        ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            name="fk_shot_scripts_episode_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`position` > 0", name="ck_shot_scripts_position"),
        CheckConstraint(
            "`duration_ms` BETWEEN 1000 AND 10000", name="ck_shot_scripts_duration_ms"
        ),
        CheckConstraint("`row_version` > 0", name="ck_shot_scripts_row_version"),
        CheckConstraint(
            "`image_settings` IS NULL OR JSON_TYPE(`image_settings`) = 'OBJECT'",
            name="ck_shot_scripts_image_settings",
        ),
        CheckConstraint(
            "`deleted_at` IS NULL OR `created_at` IS NULL OR `deleted_at` >= `created_at`",
            name="ck_shot_scripts_deleted_time",
        ),
        CheckConstraint(
            "(`creation_key` IS NULL AND `creation_hash` IS NULL) OR "
            "(`creation_key` IS NOT NULL AND CHAR_LENGTH(TRIM(`creation_key`)) > 0 "
            "AND `creation_hash` IS NOT NULL AND CHAR_LENGTH(`creation_hash`) = 64)",
            name="ck_shot_scripts_creation",
        ),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_shot_scripts_audit_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "分镜脚本",
        },
    )
