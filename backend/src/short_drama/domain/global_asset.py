from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
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


class GlobalAsset(Base):
    __tablename__ = "global_assets"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment="素材本体")
    position: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, comment="全局库排序"
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
        UniqueConstraint("asset_id", name="uk_global_assets_asset"),
        UniqueConstraint("position", name="uk_global_assets_position"),
        ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_global_assets_asset_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`position` > 0", name="ck_global_assets_position"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "全部素材（全局库关联）",
        },
    )
