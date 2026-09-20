from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    CHAR,
    DATETIME,
    INTEGER,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="应用雪花算法生成；稳定且不可变的记录标识",
    )
    format_code: Mapped[str] = mapped_column(
        VARCHAR(127, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="直接保存规范 MIME，例如 image/png、video/mp4；演示资源可用 demo:image",
    )
    storage_locator: Mapped[str] = mapped_column(
        VARCHAR(700, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="稳定存储定位值，不保存临时签名 URL 或 Blob URL",
    )
    original_name: Mapped[str] = mapped_column(
        VARCHAR(255), nullable=False, server_default=text("''"), comment="原始文件名"
    )
    byte_size: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True, server_default=text("NULL"), comment="实际文件字节数"
    )
    width: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True), nullable=True, server_default=text("NULL"), comment="实际像素宽度"
    )
    height: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True), nullable=True, server_default=text("NULL"), comment="实际像素高度"
    )
    duration_ms: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="实际视频时长，单位毫秒；图片为空",
    )
    checksum_sha256: Mapped[str | None] = mapped_column(
        CHAR(64, collation="ascii_bin", charset="ascii"),
        nullable=True,
        server_default=text("NULL"),
        comment="文件校验值，不作为文件身份",
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
        UniqueConstraint("storage_locator", name="uk_media_locator"),
        CheckConstraint(
            "`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`",
            name="ck_media_files_audit_time",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(`storage_locator`)) > 0", name="ck_media_locator"),
        CheckConstraint(
            "`format_code` = 'demo:image' OR `format_code` LIKE 'image/%' "
            "OR `format_code` LIKE 'video/%'",
            name="ck_media_format",
        ),
        CheckConstraint(
            "(`width` IS NULL OR `width` > 0) AND (`height` IS NULL OR `height` > 0)",
            name="ck_media_dimensions",
        ),
        CheckConstraint(
            "(`duration_ms` IS NULL OR `duration_ms` > 0) "
            "AND (`format_code` LIKE 'video/%' OR `duration_ms` IS NULL)",
            name="ck_media_duration",
        ),
        CheckConstraint(
            "`checksum_sha256` IS NULL OR CHAR_LENGTH(`checksum_sha256`) = 64",
            name="ck_media_checksum",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "媒体元数据",
        },
    )
