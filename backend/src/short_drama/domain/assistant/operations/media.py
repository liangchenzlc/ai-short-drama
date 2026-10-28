"""Private frozen media intents link one assistant operation to native generation tasks."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentMediaRequest(Base):
    __tablename__ = "agent_media_requests"

    id: Mapped[int] = _id()
    task_id: Mapped[int] = _number()
    operation_id: Mapped[int] = _number()
    item_index: Mapped[int] = _number()
    media_kind: Mapped[str] = mapped_column(VARCHAR(16))
    requested_count: Mapped[int] = _number()
    generation_task_id: Mapped[int | None] = _number(nullable=True)
    frozen_target: Mapped[dict] = _json()
    frozen_request: Mapped[dict] = _json()
    reservation_state: Mapped[str] = mapped_column(VARCHAR(16))
    status: Mapped[str] = mapped_column(VARCHAR(24))
    result: Mapped[dict | None] = _json(nullable=True)
    error: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_media_requests_task"),
        _fk(["operation_id"], "agent_operations", name="fk_agent_media_requests_operation"),
        _fk(["generation_task_id"], "async_tasks", name="fk_agent_media_requests_generation"),
        UniqueConstraint("operation_id", "item_index", name="uk_agent_media_requests_item"),
        UniqueConstraint("generation_task_id", name="uk_agent_media_requests_generation"),
        Index("idx_agent_media_requests_task", "task_id", "id"),
        CheckConstraint("media_kind IN ('image','video','audio')", name="ck_agent_media_kind"),
        CheckConstraint(
            "item_index > 0 AND requested_count BETWEEN 1 AND 4", name="ck_agent_media_quantity"
        ),
        CheckConstraint(
            "reservation_state IN ('reserved','committed','released')",
            name="ck_agent_media_reservation",
        ),
        CheckConstraint(
            "status IN ('reserved','accepted','unknown','succeeded','failed','conflict',"
            "'not_submitted')",
            name="ck_agent_media_status",
        ),
        OPTIONS,
    )
