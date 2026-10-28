"""One editable story outline per standard creation project."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectStoryOutline(Base):
    __tablename__ = "project_story_outlines"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    content: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="当前故事大纲正文"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    created_by: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", name="uk_story_outlines_project"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_story_outlines_project",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_story_outlines_audit_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "故事大纲；每个作品一份当前正文",
        },
    )
