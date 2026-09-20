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
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class NovelScriptRecord(Base):
    __tablename__ = "novel_script_records"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的生成记录标识",
    )
    novel_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="作为生成来源的分集小说"
    )
    script_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="本次生成得到的剧本；一份剧本最多一条生成来源记录",
    )
    batch_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="一次小说生成剧本操作的批次标识；不是外键"
    )
    model_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="本次使用的文本模型配置；演示或来源未知时可空",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="生成结果入库时间；正常写入非空，历史未知可显式NULL",
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="发起本次生成的用户；预留用户ID，暂不设外键",
    )

    __table_args__ = (
        UniqueConstraint("script_id", name="uk_novel_script_result"),
        Index("idx_novel_script_source", "novel_id", "batch_id"),
        Index("idx_novel_script_batch", "batch_id"),
        Index("idx_novel_script_records_model_id", "model_id"),
        ForeignKeyConstraint(
            ["novel_id"],
            ["episode_novels.id"],
            name="fk_novel_script_records_novel_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["script_id"],
            ["episode_scripts.id"],
            name="fk_novel_script_records_script_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_id"],
            ["ai_model_configs.id"],
            name="fk_novel_script_records_model_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`batch_id` > 0", name="ck_novel_script_records_batch"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "小说生成剧本记录",
        },
    )
