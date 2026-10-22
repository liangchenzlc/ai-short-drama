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
    MEDIUMTEXT,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectNovel(Base):
    __tablename__ = "project_novels"

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
    content: Mapped[str] = mapped_column(
        MEDIUMTEXT(),
        nullable=False,
        server_default=text("('')"),
        comment="创作流程中用户输入的当前小说正文",
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
        UniqueConstraint("project_id", name="uk_novels_creation"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_novels_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_project_novels_audit_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "小说原文",
        },
    )
