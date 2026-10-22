"""Immutable discussion revision requests and the exact versions they replaced."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .agent import _fk, _hash, _id, _json, _number, _time
from .base import Base
from .collaboration import OPTIONS


class AgentDiscussionRevision(Base):
    __tablename__ = "agent_discussion_revisions"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    source_message_id: Mapped[int] = _number()
    source_version: Mapped[int] = _number()
    reply_message_id: Mapped[int] = _number()
    run_id: Mapped[int] = _number()
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    request_key: Mapped[str] = _hash()
    request_hash: Mapped[str] = _hash()
    source_snapshot: Mapped[dict] = _json()
    reply_snapshot: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_discussion_conversation"),
        _fk(
            ["source_message_id", "conversation_id"],
            "agent_messages",
            ["id", "conversation_id"],
            name="fk_agent_discussion_source",
        ),
        _fk(
            ["reply_message_id", "conversation_id"],
            "agent_messages",
            ["id", "conversation_id"],
            name="fk_agent_discussion_reply",
        ),
        _fk(["run_id"], "agent_runs", name="fk_agent_discussion_run"),
        UniqueConstraint("conversation_id", "request_key", name="uk_agent_discussion_request"),
        Index("idx_agent_discussion_source", "source_message_id", "id"),
        Index("idx_agent_discussion_reply", "reply_message_id", "id"),
        CheckConstraint("source_version > 0", name="ck_agent_discussion_version"),
        CheckConstraint("kind IN ('edit','regenerate')", name="ck_agent_discussion_kind"),
        CheckConstraint(
            "JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(reply_snapshot) = 'OBJECT'",
            name="ck_agent_discussion_snapshots",
        ),
        OPTIONS,
    )
