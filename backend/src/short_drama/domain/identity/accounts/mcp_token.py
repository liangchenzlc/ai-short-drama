"""Revocable named account credentials with explicit project and tool scopes."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class McpAccessToken(Base):
    __tablename__ = "mcp_access_tokens"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    owner_user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    name: Mapped[str] = mapped_column(VARCHAR(80))
    token_hash: Mapped[str] = mapped_column(
        CHAR(64, charset="ascii", collation="ascii_bin"), unique=True
    )
    token_hint: Mapped[str] = mapped_column(VARCHAR(24))
    project_ids: Mapped[list[str]] = mapped_column(JSON)
    tool_names: Mapped[list[str]] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    allowed_ips: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    deleted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    __table_args__ = (
        Index("idx_mcp_tokens_owner", "owner_user_id", "deleted_at", "id"),
        CheckConstraint(
            "JSON_TYPE(project_ids) = 'ARRAY' AND JSON_LENGTH(project_ids) > 0 "
            "AND JSON_TYPE(tool_names) = 'ARRAY' AND JSON_LENGTH(tool_names) > 0 "
            "AND JSON_TYPE(allowed_ips) = 'ARRAY'",
            name="ck_mcp_tokens_scopes",
        ),
        OPTIONS,
    )
