"""个人风格原件与来源项目快照独立存储，引用阻止媒体被误清理。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit
from .collaboration import OPTIONS


class CanvasStyleProfile(CanvasAudit, Base):
    __tablename__ = "canvas_style_profiles"
    scope_user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    profile_json: Mapped[dict] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    import_key: Mapped[str | None] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    favorite: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    deleted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("scope_user_id", "import_key", name="uk_canvas_style_import"),
        Index("idx_canvas_personal_styles", "scope_user_id", "deleted_at", "updated_at"),
        CheckConstraint("row_version > 0 AND favorite IN (0,1)", name="ck_canvas_style_version"),
        CheckConstraint("JSON_TYPE(profile_json) = 'OBJECT'", name="ck_canvas_style_profile"),
        OPTIONS,
    )


class CanvasStyleMediaReference(CanvasAudit, Base):
    __tablename__ = "canvas_style_media_references"
    style_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_style_profiles.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        UniqueConstraint("style_id", "media_id", name="uk_canvas_style_media"),
        OPTIONS,
    )


class CanvasStylePreference(CanvasAudit, Base):
    __tablename__ = "canvas_style_preferences"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    preset_id: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    favorite: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("user_id", "preset_id", name="uk_canvas_style_preference"),
        CheckConstraint("favorite IN (0,1)", name="ck_canvas_style_preference"),
        OPTIONS,
    )
