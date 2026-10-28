"""Server-authoritative delivery requirements, frozen per task.

``effective_state.task_contract`` stays readable for frozen requests, but this
table is the source of truth: the executor may confirm drafts and raise counts,
it cannot delete a requirement or invent one without a real quote offset.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .agent import _fk, _hash, _id, _number, _time
from .base import Base
from .collaboration import OPTIONS


class AgentTaskRequirement(Base):
    __tablename__ = "agent_task_requirements"

    id: Mapped[int] = _id()
    task_id: Mapped[int] = _number()
    requirement_key: Mapped[str] = mapped_column(VARCHAR(80))
    description: Mapped[str] = mapped_column(VARCHAR(500))
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    origin: Mapped[str] = mapped_column(VARCHAR(16))
    state: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'draft'"))
    minimum_count: Mapped[int] = _number(1)
    artifact_kind: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    asset_kind: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    source_message_id: Mapped[int] = _number()
    quote_start: Mapped[int] = _number(0)
    quote_end: Mapped[int] = _number(0)
    definition_hash: Mapped[str] = _hash()
    attempts: Mapped[int] = _number(0)
    input_revision: Mapped[int] = _number(0)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_task_requirements_task"),
        _fk(["source_message_id"], "agent_messages", name="fk_agent_task_requirements_message"),
        UniqueConstraint("task_id", "requirement_key", name="uk_agent_task_requirements_key"),
        Index("idx_agent_task_requirements_state", "task_id", "state"),
        CheckConstraint(
            "kind IN ('answer','read','write','image','skill','observation','contract')",
            name="ck_agent_task_requirements_kind",
        ),
        CheckConstraint(
            "origin IN ('server_draft','server_derived','agent','user')",
            name="ck_agent_task_requirements_origin",
        ),
        CheckConstraint(
            "state IN ('draft','confirmed','frozen','superseded')",
            name="ck_agent_task_requirements_state",
        ),
        CheckConstraint("quote_end >= quote_start", name="ck_agent_task_requirements_quote"),
        CheckConstraint("minimum_count >= 1", name="ck_agent_task_requirements_count"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(requirement_key)) > 0", name="ck_agent_task_requirements_name"
        ),
        OPTIONS,
    )
