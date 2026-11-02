"""Run-local execution identity and durable sandbox binding; no child scheduler."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _id, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentExecutionScope(Base):
    __tablename__ = "agent_execution_scopes"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    run_id: Mapped[int] = _number()
    scope_key: Mapped[str] = mapped_column(VARCHAR(96, collation="utf8mb4_0900_bin"))
    parent_scope_id: Mapped[int | None] = _number(nullable=True)
    parent_tool_call_id: Mapped[int | None] = _number(nullable=True)
    sandbox_service: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    sandbox_id: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    workspace_volume: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    image_version: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    sandbox_status: Mapped[str | None] = mapped_column(VARCHAR(24), nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_scopes_owner"),
        _fk(["run_id"], "agent_runs", name="fk_agent_scopes_run"),
        _fk(
            ["parent_scope_id", "run_id"],
            "agent_execution_scopes",
            ["id", "run_id"],
            name="fk_agent_scopes_parent",
        ),
        ForeignKeyConstraint(
            ["parent_tool_call_id", "run_id"],
            ["agent_tool_calls.id", "agent_tool_calls.run_id"],
            name="fk_agent_scopes_parent_call",
            use_alter=True,
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("run_id", "scope_key", name="uk_agent_scopes_key"),
        UniqueConstraint("id", "run_id", name="uk_agent_scopes_run"),
        CheckConstraint(
            "(scope_key = 'root' AND parent_scope_id IS NULL AND parent_tool_call_id IS NULL) OR "
            "(scope_key <> 'root' AND parent_scope_id IS NOT NULL "
            "AND parent_tool_call_id IS NOT NULL)",
            name="ck_agent_scopes_parent",
        ),
        OPTIONS,
    )
