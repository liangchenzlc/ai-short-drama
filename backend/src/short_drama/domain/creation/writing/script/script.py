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

from short_drama.domain.base import Base


class ProjectScript(Base):
    __tablename__ = "project_scripts"

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
        INTEGER(unsigned=True), nullable=False, comment="同一项目内剧本排序"
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
    published_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, comment="明确采用为项目作品的时间；候选为空"
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
    confirmed_project_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        Computed("CASE WHEN `state` = 'confirmed' THEN `project_id` ELSE NULL END", persisted=True),
        nullable=True,
        comment="技术生成列：已确认时为项目ID，否则NULL；应用不写入",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "position", name="uk_scripts_creation_position"),
        UniqueConstraint("confirmed_project_id", name="uk_scripts_confirmed_creation"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_scripts_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`position` > 0", name="ck_project_scripts_position"),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_project_scripts_audit_time",
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
