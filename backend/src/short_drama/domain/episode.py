from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
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


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="所属项目"
    )
    position: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, comment="项目内分集排序"
    )
    title: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, comment="分集标题")
    synopsis: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="分集简介"
    )
    aspect: Mapped[str] = mapped_column(
        VARCHAR(8, collation="utf8mb4_0900_bin"), nullable=False, comment="本集画幅"
    )
    style: Mapped[str] = mapped_column(
        VARCHAR(255), nullable=False, server_default=text("''"), comment="本集风格"
    )
    editing_script_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="当前编辑剧本；由服务层校验同分集归属",
    )
    content_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
        comment="小说与剧本编辑的并发版本号",
    )
    storyboard_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
        comment="分镜集合并发版本",
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
        UniqueConstraint("project_id", "position", name="uk_episodes_project_position"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_episodes_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`position` > 0", name="ck_episodes_position"),
        CheckConstraint("`content_version` > 0", name="ck_episodes_content_version"),
        CheckConstraint("`storyboard_version` > 0", name="ck_episodes_storyboard_version"),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_episodes_audit_time",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(`title`)) > 0", name="ck_episodes_title"),
        CheckConstraint("`aspect` IN ('16:9', '9:16')", name="ck_episodes_aspect"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "分集",
        },
    )
