"""Durable before/after snapshots for destructive script operations."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class ProjectScriptRevision(Base):
    __tablename__ = "project_script_revisions"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    script_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    before_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    after_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source: Mapped[str] = mapped_column(VARCHAR(24))
    before_content: Mapped[str] = mapped_column(MEDIUMTEXT())
    after_content: Mapped[str] = mapped_column(MEDIUMTEXT())
    created_by: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="RESTRICT", name="fk_script_revisions_project"
        ),
        ForeignKeyConstraint(
            ["script_id"],
            ["project_scripts.id"],
            ondelete="RESTRICT",
            name="fk_script_revisions_script",
        ),
        UniqueConstraint("project_id", "after_version", name="uk_script_revisions_version"),
        CheckConstraint("after_version > before_version", name="ck_script_revisions_version"),
        OPTIONS,
    )
