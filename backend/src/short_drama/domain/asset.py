from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    CHAR,
    DATETIME,
    JSON,
    MEDIUMTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    kind: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False, comment="角色 / 场景 / 道具"
    )
    name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, comment="素材名称，允许重名")
    label: Mapped[str] = mapped_column(
        VARCHAR(120),
        nullable=False,
        server_default=text("''"),
        comment="单个标签或分类文本，不存逗号分隔的多标签",
    )
    description: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="素材描述"
    )
    prompt: Mapped[str] = mapped_column(
        MEDIUMTEXT(), nullable=False, server_default=text("('')"), comment="素材生成提示词"
    )
    model_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="使用的模型配置；手动建立、导入时可空",
    )
    media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="当前素材图片，尚未生成时可空",
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1"), comment="编辑并发版本"
    )
    state: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        nullable=False,
        server_default=text("'unconfirmed'"),
        comment="素材确认状态",
    )
    tags: Mapped[list] = mapped_column(
        JSON(), nullable=False, server_default=text("(JSON_ARRAY())"), comment="素材标签数组"
    )
    scene_time: Mapped[str] = mapped_column(
        VARCHAR(60), nullable=False, server_default=text("''"), comment="场景时间"
    )
    creation_key: Mapped[str | None] = mapped_column(
        VARCHAR(128, collation="utf8mb4_0900_bin"), nullable=True, server_default=text("NULL")
    )
    creation_hash: Mapped[str | None] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_bin"), nullable=True, server_default=text("NULL")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="创建时间；正常写入非空，历史未知可显式NULL",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="最近修改时间；正常写入非空，历史未知可显式NULL",
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="创建人；预留用户ID，暂不设外键",
    )
    updated_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="最近修改人；预留用户ID，暂不设外键",
    )

    __table_args__ = (
        Index("idx_assets_kind_name", "kind", "name"),
        Index("idx_assets_model_id", "model_id"),
        Index("idx_assets_media_id", "media_id"),
        Index("uk_assets_creation_key", "creation_key", unique=True),
        ForeignKeyConstraint(
            ["model_id"],
            ["ai_model_configs.id"],
            name="fk_assets_model_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_assets_media_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_assets_audit_time",
        ),
        CheckConstraint("`kind` IN ('character', 'scene', 'prop')", name="ck_assets_kind"),
        CheckConstraint("CHAR_LENGTH(TRIM(`name`)) > 0", name="ck_assets_name"),
        CheckConstraint("`row_version` > 0", name="ck_assets_row_version"),
        CheckConstraint(
            "`state` IN ('unconfirmed', 'confirmed')", name="ck_assets_state"
        ),
        CheckConstraint(
            "`state` <> 'confirmed' OR `media_id` IS NOT NULL", name="ck_assets_confirmed_media"
        ),
        CheckConstraint(
            "JSON_TYPE(`tags`) = 'ARRAY' AND JSON_LENGTH(`tags`) <= 20", name="ck_assets_tags"
        ),
        CheckConstraint(
            "`kind` = 'scene' OR `scene_time` = ''", name="ck_assets_scene_time"
        ),
        CheckConstraint(
            "(`creation_key` IS NULL AND `creation_hash` IS NULL) OR "
            "(`creation_key` IS NOT NULL AND CHAR_LENGTH(TRIM(`creation_key`)) > 0 "
            "AND `creation_hash` IS NOT NULL AND CHAR_LENGTH(`creation_hash`) = 64)",
            name="ck_assets_creation_pair",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "素材",
        },
    )
