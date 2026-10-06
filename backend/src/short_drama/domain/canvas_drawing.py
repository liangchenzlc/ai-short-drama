"""已保存绘图的稳定身份、不可变版本及媒体保护。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasChild, canvas_parent
from .collaboration import OPTIONS


class CanvasDrawing(CanvasChild, Base):
    __tablename__ = "canvas_drawings"
    source_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("canvas_id", "source_key", name="uk_canvas_drawing_key"),
        UniqueConstraint("canvas_id", "id", name="uk_canvas_drawing_id"),
        CheckConstraint("row_version > 0", name="ck_canvas_drawing_version"),
        CheckConstraint("CHAR_LENGTH(TRIM(source_key)) > 0", name="ck_canvas_drawing_key"),
        Index("idx_canvas_drawing_active", "canvas_id", "archived_at", "updated_at"),
        OPTIONS,
    )


class CanvasDrawingVersion(CanvasChild, Base):
    __tablename__ = "canvas_drawing_versions"
    drawing_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    document_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        canvas_parent(),
        ForeignKeyConstraint(
            ["canvas_id", "drawing_id"],
            ["canvas_drawings.canvas_id", "canvas_drawings.id"],
            name="fk_canvas_drawing_version_parent",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("drawing_id", "row_version", name="uk_canvas_drawing_version"),
        UniqueConstraint("canvas_id", "id", name="uk_canvas_drawing_version_id"),
        CheckConstraint("row_version > 0", name="ck_canvas_drawing_snapshot_version"),
        OPTIONS,
    )


class CanvasDrawingMediaReference(CanvasChild, Base):
    __tablename__ = "canvas_drawing_media_references"
    drawing_version_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        canvas_parent(),
        ForeignKeyConstraint(
            ["canvas_id", "drawing_version_id"],
            ["canvas_drawing_versions.canvas_id", "canvas_drawing_versions.id"],
            name="fk_canvas_drawing_media_version",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("drawing_version_id", "media_id", name="uk_canvas_drawing_media"),
        OPTIONS,
    )


class CanvasRevisionDrawingReference(CanvasChild, Base):
    __tablename__ = "canvas_revision_drawing_references"
    revision_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    drawing_version_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    __table_args__ = (
        canvas_parent(),
        ForeignKeyConstraint(
            ["canvas_id", "revision_id"],
            ["canvas_revisions.canvas_id", "canvas_revisions.id"],
            name="fk_canvas_revision_drawing_owner",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["canvas_id", "drawing_version_id"],
            ["canvas_drawing_versions.canvas_id", "canvas_drawing_versions.id"],
            name="fk_canvas_revision_drawing_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("revision_id", "drawing_version_id", name="uk_canvas_revision_drawing"),
        OPTIONS,
    )
