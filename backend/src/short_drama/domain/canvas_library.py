"""Private source asset-library records; distinct from standard character/scene assets."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit
from .collaboration import OPTIONS


class CanvasLibraryAsset(CanvasAudit, Base):
    __tablename__ = "canvas_library_assets"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    project_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("projects.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    source_key: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    title: Mapped[str] = mapped_column(VARCHAR(255))
    category: Mapped[str] = mapped_column(VARCHAR(32))
    status: Mapped[str] = mapped_column(VARCHAR(16))
    payload_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uk_canvas_library_asset"),
        Index("idx_canvas_library_user", "user_id", "updated_at", "id"),
        CheckConstraint(
            "kind IN ('text','image','video','audio','model','entity')",
            name="ck_canvas_library_kind",
        ),
        OPTIONS,
    )


class CanvasLibraryAssetReference(Base):
    __tablename__ = "canvas_library_asset_references"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    library_asset_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_library_assets.id", ondelete="CASCADE", onupdate="RESTRICT"),
    )
    media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    binary_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_binary_resources.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        UniqueConstraint("library_asset_id", "media_id", name="uk_canvas_library_media_ref"),
        UniqueConstraint("library_asset_id", "binary_id", name="uk_canvas_library_binary_ref"),
        CheckConstraint(
            "(media_id IS NULL) <> (binary_id IS NULL)", name="ck_canvas_library_resource_ref"
        ),
        OPTIONS,
    )


class CanvasLibraryFolder(CanvasAudit, Base):
    __tablename__ = "canvas_library_folders"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    name: Mapped[str] = mapped_column(VARCHAR(40))
    name_key: Mapped[str] = mapped_column(VARCHAR(40, collation="utf8mb4_0900_bin"))
    position: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    __table_args__ = (
        UniqueConstraint("user_id", "name_key", name="uk_canvas_library_folder_name"),
        Index("idx_canvas_library_folder_order", "user_id", "position", "id"),
        OPTIONS,
    )


class CanvasLibraryFolderItem(Base):
    __tablename__ = "canvas_library_folder_items"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    library_asset_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_library_assets.id", ondelete="CASCADE", onupdate="RESTRICT"),
    )
    folder_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("canvas_library_folders.id", ondelete="CASCADE", onupdate="RESTRICT"),
    )
    __table_args__ = (
        UniqueConstraint("library_asset_id", name="uk_canvas_library_folder_item"),
        Index("idx_canvas_library_folder_items", "folder_id", "library_asset_id"),
        OPTIONS,
    )
