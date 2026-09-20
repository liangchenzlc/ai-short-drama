from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    INTEGER,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectAsset(Base):
    __tablename__ = "project_assets"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    project_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment="项目")
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment="素材本体")
    position: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, comment="项目内排序"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="创建时间；正常写入非空，历史未知可显式NULL",
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="创建人；预留用户ID，暂不设外键",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "asset_id", name="uk_project_assets_asset"),
        UniqueConstraint("project_id", "position", name="uk_project_assets_position"),
        Index("idx_project_assets_asset_id", "asset_id"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_assets_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_project_assets_asset_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`position` > 0", name="ck_project_assets_position"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "项目素材关联",
        },
    )
