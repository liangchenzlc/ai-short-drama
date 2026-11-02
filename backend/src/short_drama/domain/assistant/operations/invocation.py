"""Account-scoped business receipts, independent of assistant execution records."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _hash, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class BusinessToolInvocation(Base):
    __tablename__ = "business_tool_invocations"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    tool_name: Mapped[str] = mapped_column(VARCHAR(80, charset="ascii", collation="ascii_bin"))
    request_id: Mapped[str] = mapped_column(VARCHAR(160, charset="ascii", collation="ascii_bin"))
    arguments_hash: Mapped[str] = _hash()
    source: Mapped[str] = mapped_column(VARCHAR(16, charset="ascii", collation="ascii_bin"))
    token_id: Mapped[int | None] = _number(nullable=True)
    status: Mapped[str] = mapped_column(VARCHAR(24), server_default=text("'running'"))
    result: Mapped[dict | None] = _json(nullable=True)
    result_refs: Mapped[list] = _json(array=True)
    changes: Mapped[list] = _json(array=True)
    created_at: Mapped[datetime] = _time()
    finished_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_business_invocations_owner"),
        UniqueConstraint(
            "owner_user_id", "tool_name", "request_id", name="uk_business_invocations_request"
        ),
        Index("idx_business_invocations_owner", "owner_user_id", "id"),
        CheckConstraint("source IN ('assistant','mcp')", name="ck_business_invocations_source"),
        CheckConstraint(
            "status IN ('running','succeeded','reconciling')", name="ck_business_invocations_status"
        ),
        CheckConstraint(
            "(status = 'succeeded') = (finished_at IS NOT NULL)",
            name="ck_business_invocations_terminal",
        ),
        CheckConstraint(
            "JSON_TYPE(result_refs) = 'ARRAY' AND JSON_TYPE(changes) = 'ARRAY'",
            name="ck_business_invocations_objects",
        ),
        OPTIONS,
    )
