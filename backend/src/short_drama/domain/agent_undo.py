"""Immutable, explicitly selected inverse batches; original tool receipts stay intact."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped

from .agent import _fk, _hash, _id, _json, _number, _time
from .base import Base
from .collaboration import OPTIONS


class AgentBatchUndo(Base):
    __tablename__ = "agent_batch_undos"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    task_id: Mapped[int] = _number()
    operation_id: Mapped[int] = _number()
    request_key: Mapped[str] = _hash()
    request_hash: Mapped[str] = _hash()
    preview_hash: Mapped[str] = _hash()
    selected_keys: Mapped[list] = _json(array=True)
    changes: Mapped[list] = _json(array=True)
    result_refs: Mapped[list] = _json(array=True)
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_undo_conversation"),
        _fk(
            ["task_id", "conversation_id"],
            "agent_tasks",
            ["id", "conversation_id"],
            name="fk_agent_undo_task",
        ),
        _fk(["operation_id"], "agent_operations", name="fk_agent_undo_operation"),
        UniqueConstraint("conversation_id", "request_key", name="uk_agent_undo_request"),
        Index("idx_agent_undo_operation", "operation_id", "id"),
        CheckConstraint(
            "JSON_TYPE(selected_keys) = 'ARRAY' AND JSON_TYPE(changes) = 'ARRAY' "
            "AND JSON_TYPE(result_refs) = 'ARRAY'",
            name="ck_agent_undo_arrays",
        ),
        OPTIONS,
    )
