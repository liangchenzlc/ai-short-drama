from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Index,
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


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    name: Mapped[str] = mapped_column(VARCHAR(120), nullable=False, comment="项目名称，允许重名")
    synopsis: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="故事梗概"
    )
    style: Mapped[str] = mapped_column(
        VARCHAR(255), nullable=False, server_default=text("''"), comment="新分集默认风格"
    )
    aspect: Mapped[str] = mapped_column(VARCHAR(8), nullable=False, comment="新分集默认画幅")
    target_ms: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, comment="项目目标时长，单位毫秒"
    )
    last_opened_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, server_default=text("NULL"), comment="最近打开时间"
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
        Index("idx_projects_last_opened", "last_opened_at", "id"),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_projects_audit_time",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(`name`)) > 0", name="ck_projects_name"),
        CheckConstraint("`aspect` IN ('16:9', '9:16')", name="ck_projects_aspect"),
        CheckConstraint("`target_ms` BETWEEN 1000 AND 3600000", name="ck_projects_target"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "项目",
        },
    )
