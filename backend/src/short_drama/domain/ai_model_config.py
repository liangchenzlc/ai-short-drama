from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    JSON,
    TEXT,
    TINYINT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AIModelConfig(Base):
    __tablename__ = "ai_model_configs"

    capability_cache: Mapped[dict | None] = mapped_column(JSON(), nullable=True)

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    service_type: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=False, comment="文本 / 生图 / 生视频"
    )
    name: Mapped[str] = mapped_column(VARCHAR(120), nullable=False, comment="配置显示名")
    model_key: Mapped[str] = mapped_column(
        VARCHAR(255, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="供应商模型编码，保留大小写；每条配置仅保存一个模型编码",
    )
    provider: Mapped[str] = mapped_column(
        VARCHAR(120, collation="utf8mb4_0900_bin"), nullable=False, comment="供应商名称"
    )
    base_url: Mapped[str] = mapped_column(
        VARCHAR(2048), nullable=False, server_default=text("''"), comment="服务基础地址"
    )
    apikey: Mapped[str | None] = mapped_column(
        TEXT(),
        nullable=True,
        server_default=text("NULL"),
        comment="加密密钥信封（含算法/密钥版本/随机数/认证标签/密文的编码串），不存明文",
    )
    enabled: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
        comment="是否允许用于新操作",
    )
    is_deleted: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("0"), comment="是否逻辑删除"
    )
    is_default: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        server_default=text("0"),
        comment="是否本类型默认配置",
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
        comment="配置修改、删除与默认切换的乐观并发版本",
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
    default_service_type: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"),
        Computed(
            "CASE WHEN `is_default` = 1 AND `is_deleted` = 0 THEN `service_type` ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
        comment="技术生成列：用于每类最多一个未删除默认配置；应用不写入",
    )

    __table_args__ = (
        UniqueConstraint("default_service_type", name="uk_ai_default_service"),
        Index("idx_ai_type_available", "service_type", "is_deleted", "enabled"),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_ai_model_configs_audit_time",
        ),
        CheckConstraint("`service_type` IN ('text', 'image', 'video')", name="ck_ai_service_type"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(`name`)) > 0 AND CHAR_LENGTH(TRIM(`model_key`)) > 0 "
            "AND CHAR_LENGTH(TRIM(`provider`)) > 0",
            name="ck_ai_required_text",
        ),
        CheckConstraint(
            "`enabled` IN (0, 1) AND `is_deleted` IN (0, 1) AND `is_default` IN (0, 1)",
            name="ck_ai_flags",
        ),
        CheckConstraint(
            "`is_default` = 0 OR (`enabled` = 1 AND `is_deleted` = 0)",
            name="ck_ai_default_available",
        ),
        CheckConstraint("`row_version` > 0", name="ck_ai_version"),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "AI模型配置",
        },
    )
