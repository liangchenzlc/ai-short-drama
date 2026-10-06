"""本人项目文件夹与画布归属；不同于素材库分类。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit
from .collaboration import OPTIONS


class CanvasProjectFolder(CanvasAudit, Base):
    __tablename__ = "canvas_project_folders"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    source_key: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    name: Mapped[str] = mapped_column(VARCHAR(80))
    cover_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    cover_binary_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_binary_resources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uk_canvas_project_folder_key"),
        Index("idx_canvas_project_folder_list", "user_id", "tombstoned_at", "updated_at", "id"),
        CheckConstraint(
            "cover_media_id IS NULL OR cover_binary_id IS NULL", name="ck_canvas_folder_cover"
        ),
        CheckConstraint(
            "tombstoned_at IS NULL OR (cover_media_id IS NULL AND cover_binary_id IS NULL)",
            name="ck_canvas_folder_deleted_cover",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_canvas_folder_name"),
        OPTIONS,
    )


class CanvasProjectFolderItem(Base):
    __tablename__ = "canvas_project_folder_items"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    canvas_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("project_canvases.id", ondelete="CASCADE", onupdate="RESTRICT"),
    )
    folder_key: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    __table_args__ = (
        UniqueConstraint("user_id", "canvas_id", name="uk_canvas_project_folder_item"),
        ForeignKeyConstraint(
            ["user_id", "folder_key"],
            ["canvas_project_folders.user_id", "canvas_project_folders.source_key"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("idx_canvas_project_folder_items", "user_id", "folder_key", "canvas_id"),
        OPTIONS,
    )
