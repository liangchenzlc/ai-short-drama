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


class ScriptShotRecord(Base):
    __tablename__ = "script_shot_records"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    script_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="作为生成来源的剧本"
    )
    shot_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="生成得到的分镜"
    )
    batch_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="一次生成的批次标识；不是外键"
    )
    model_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="本次使用的文本模型配置；演示数据可空",
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
        UniqueConstraint("batch_id", "shot_id", name="uk_script_shot_batch_shot"),
        Index("idx_script_shot_source", "script_id", "batch_id"),
        Index("idx_script_shot_records_shot_id", "shot_id"),
        Index("idx_script_shot_records_model_id", "model_id"),
        ForeignKeyConstraint(
            ["script_id"],
            ["episode_scripts.id"],
            name="fk_script_shot_records_script_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["shot_id"],
            ["shot_scripts.id"],
            name="fk_script_shot_records_shot_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_id"],
            ["ai_model_configs.id"],
            name="fk_script_shot_records_model_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`batch_id` > 0", name="ck_script_shot_records_batch"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "剧本生成分镜脚本记录",
        },
    )
