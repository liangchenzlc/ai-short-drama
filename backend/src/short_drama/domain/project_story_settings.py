"""Structured story settings, versioned independently from the script."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectStorySettings(Base):
    __tablename__ = "project_story_settings"

    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "projects.id",
            name="fk_story_settings_project",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        primary_key=True,
        autoincrement=False,
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    logline: Mapped[str] = mapped_column(VARCHAR(200), nullable=False, server_default=text("''"))
    synopsis: Mapped[str] = mapped_column(MEDIUMTEXT(), nullable=False)
    genre: Mapped[str] = mapped_column(VARCHAR(100), nullable=False, server_default=text("''"))
    character_bios: Mapped[str] = mapped_column(MEDIUMTEXT(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)

    __table_args__ = (
        CheckConstraint("row_version > 0", name="ck_story_settings_version"),
        CheckConstraint("updated_at >= created_at", name="ck_story_settings_time"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "项目故事设定；独立版本，不修改剧本或历史大纲",
        },
    )
