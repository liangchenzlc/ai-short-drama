"""共享图片组、稳定图位及私人处理执行记录。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .collaboration import OPTIONS, ResourceScope


class ImageGroup(ResourceScope, Base):
    __tablename__ = "image_groups"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    asset_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), ForeignKey("assets.id"))
    shot_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("shot_scripts.id")
    )
    active_slot: Mapped[int | None] = mapped_column(INTEGER(unsigned=True))
    kind: Mapped[str] = mapped_column(VARCHAR(24))
    status: Mapped[str] = mapped_column(VARCHAR(24), default="generating")
    expected_count: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    task_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), ForeignKey("async_tasks.id"))
    primary_index: Mapped[int | None] = mapped_column(INTEGER(unsigned=True))
    primary_mode: Mapped[str] = mapped_column(VARCHAR(16), default="pending")
    primary_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    source_context: Mapped[dict] = mapped_column(JSON)
    notice: Mapped[str | None] = mapped_column(VARCHAR(255))
    created_by: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        CheckConstraint("(asset_id IS NULL) <> (shot_id IS NULL)", name="ck_image_groups_target"),
        CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_image_groups_active"),
        CheckConstraint("expected_count BETWEEN 1 AND 25", name="ck_image_groups_count"),
        UniqueConstraint("asset_id", "active_slot", name="uk_image_groups_asset_current"),
        UniqueConstraint("shot_id", "active_slot", name="uk_image_groups_shot_current"),
        UniqueConstraint("task_id", name="uk_image_groups_task"),
        Index("idx_image_groups_scope", "project_id", "scope_user_id", "created_at"),
        OPTIONS,
    )


class ImageGroupItem(Base):
    __tablename__ = "image_group_items"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    group_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("image_groups.id"))
    output_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("media_files.id")
    )
    item_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    origin: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("group_id", "output_index", name="uk_image_group_items_position"),
        CheckConstraint("output_index BETWEEN 1 AND 25", name="ck_image_group_items_position"),
        CheckConstraint("item_version > 0", name="ck_image_group_items_version"),
        OPTIONS,
    )


class ImageOperation(ResourceScope, Base):
    __tablename__ = "image_operations"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    item_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("image_group_items.id"))
    source_media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("media_files.id")
    )
    source_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    operation_type: Mapped[str] = mapped_column(VARCHAR(24))
    parameters: Mapped[dict] = mapped_column(JSON)
    prepared_data: Mapped[dict] = mapped_column(JSON, default=dict)
    upload_checksum: Mapped[str | None] = mapped_column(VARCHAR(64))
    mask_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("media_files.id")
    )
    result_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("media_files.id")
    )
    result_group_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("image_groups.id")
    )
    task_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), ForeignKey("async_tasks.id"))
    request_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    request_hash: Mapped[str] = mapped_column(VARCHAR(64))
    status: Mapped[str] = mapped_column(VARCHAR(24), default="pending")
    created_by: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        UniqueConstraint("created_by", "request_key", name="uk_image_operations_request"),
        Index("idx_image_operations_item", "item_id", "created_at"),
        OPTIONS,
    )
