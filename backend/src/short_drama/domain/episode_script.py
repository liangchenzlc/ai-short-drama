from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    INTEGER,
    MEDIUMTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class EpisodeScript(Base):
    __tablename__ = "episode_scripts"

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
        INTEGER(unsigned=True), nullable=False, comment="同一分集内剧本排序，沿用原候选排序"
    )
    content: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="剧本正文"
    )
    state: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        nullable=False,
        server_default=text("'unconfirmed'"),
        comment="未确认 / 已确认",
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
    confirmed_episode_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        Computed("CASE WHEN `state` = 'confirmed' THEN `episode_id` ELSE NULL END", persisted=True),
        nullable=True,
        comment="技术生成列：已确认时为分集ID，否则NULL；应用不写入",
    )

    __table_args__ = (
        UniqueConstraint("episode_id", "position", name="uk_scripts_episode_position"),
        UniqueConstraint("confirmed_episode_id", name="uk_scripts_confirmed_episode"),
        ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            name="fk_episode_scripts_episode_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`position` > 0", name="ck_episode_scripts_position"),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_episode_scripts_audit_time",
        ),
        CheckConstraint("`state` IN ('unconfirmed', 'confirmed')", name="ck_scripts_state"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "剧情（剧本）",
        },
    )
