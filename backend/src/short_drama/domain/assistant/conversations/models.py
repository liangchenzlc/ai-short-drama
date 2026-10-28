from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _hash, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    project_id: Mapped[int | None] = _number(nullable=True)
    stage: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=True
    )
    subject_type: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=True
    )
    subject_id: Mapped[int | None] = _number(nullable=True)
    task_type: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=True
    )
    scope_version: Mapped[int] = _number(0)
    title: Mapped[str] = mapped_column(VARCHAR(120), server_default=text("'新对话'"))
    status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), server_default=text("'active'")
    )
    row_version: Mapped[int] = _number(1)
    next_message_seq: Mapped[int] = _number(1)
    next_event_seq: Mapped[int] = _number(1)
    next_item_seq: Mapped[int] = _number(1)
    fixed_requirements: Mapped[dict] = _json()
    summary_covers_until_seq: Mapped[int] = _number(0)
    active_summary_id: Mapped[int | None] = _number(nullable=True)
    summary_generation: Mapped[int] = _number(0)
    create_key: Mapped[str | None] = mapped_column(
        VARCHAR(64, charset="ascii", collation="ascii_bin"), nullable=True
    )
    create_hash: Mapped[str | None] = _hash(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        ForeignKeyConstraint(
            ["active_summary_id", "id"],
            ["agent_conversation_summaries.id", "agent_conversation_summaries.conversation_id"],
            name="fk_agent_conversations_summary",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            use_alter=True,
        ),
        _fk(["owner_user_id"], "users", name="fk_agent_conversations_owner"),
        _fk(["project_id"], "projects", name="fk_agent_conversations_project"),
        UniqueConstraint("owner_user_id", "create_key", name="uk_agent_conversation_create"),
        Index(
            "idx_agent_conversations_owner_creation",
            "owner_user_id",
            "project_id",
            "updated_at",
            "id",
        ),
        Index("idx_agent_conversations_project", "project_id", "owner_user_id", "id"),
        Index(
            "idx_agent_conversations_project_chat",
            "owner_user_id",
            "project_id",
            "scope_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "idx_agent_conversations_scope",
            "owner_user_id",
            "project_id",
            "scope_version",
            "stage",
            "subject_type",
            "subject_id",
            "task_type",
            "status",
            "updated_at",
            "id",
        ),
        CheckConstraint(
            "(scope_version = 3 AND stage IS NULL "
            "AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR "
            "(scope_version = 2 AND project_id IS NOT NULL AND stage IS NULL "
            "AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR "
            "(scope_version = 0 AND project_id IS NOT NULL AND stage IS NULL "
            "AND subject_type IS NULL "
            "AND subject_id IS NULL AND task_type IS NULL) OR "
            "(scope_version = 1 AND project_id IS NOT NULL AND stage IS NOT NULL "
            "AND subject_type IS NOT NULL "
            "AND subject_id IS NOT NULL AND subject_id > 0 AND task_type IS NOT NULL AND ("
            "(stage = 'source' AND subject_type = 'project' AND subject_id = project_id "
            "AND task_type = 'writing') OR "
            "(stage = 'assets' AND subject_type = 'project' AND subject_id = project_id "
            "AND task_type IN ('extraction','batch')) OR "
            "(stage = 'assets' AND subject_type = 'asset' "
            "AND task_type IN ('creation','image')) OR "
            "(stage = 'storyboard' AND subject_type = 'project' AND subject_id = project_id "
            "AND task_type IN ('planning','batch')) OR "
            "(stage = 'storyboard' AND subject_type = 'shot' "
            "AND task_type IN ('creation','image','video'))))",
            name="ck_agent_conversations_scope",
        ),
        CheckConstraint(
            "status IN ('active','archived','deleting','deleted')",
            name="ck_agent_conversations_status",
        ),
        Index("idx_agent_conversations_account", "owner_user_id", "status", "updated_at", "id"),
        CheckConstraint(
            "row_version > 0 AND next_message_seq > 0 AND next_event_seq > 0",
            name="ck_agent_conversations_numbers",
        ),
        CheckConstraint(
            "JSON_TYPE(fixed_requirements) = 'OBJECT'", name="ck_agent_conversations_requirements"
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="ck_agent_conversations_title"),
        CheckConstraint(
            "(create_key IS NULL) = (create_hash IS NULL)",
            name="ck_agent_conversations_create_pair",
        ),
        CheckConstraint(
            "create_key IS NULL OR (CHAR_LENGTH(create_key) = 64 AND "
            "CHAR_LENGTH(create_hash) = 64)",
            name="ck_agent_conversations_create_hash",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_agent_conversations_time"),
        OPTIONS,
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    task_id: Mapped[int | None] = _number(nullable=True)
    seq: Mapped[int] = _number()
    item_seq: Mapped[int | None] = _number(nullable=True)
    row_version: Mapped[int] = _number(1)
    role: Mapped[str] = mapped_column(VARCHAR(16, collation="utf8mb4_0900_bin"))
    kind: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'reply'"))
    content: Mapped[str] = mapped_column(MEDIUMTEXT(), server_default=text("('')"))
    references: Mapped[list] = _json(array=True)
    artifacts: Mapped[list] = _json(array=True)
    idempotency_key: Mapped[str | None] = _hash(nullable=True)
    request_hash: Mapped[str | None] = _hash(nullable=True)
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_messages_conversation"),
        _fk(
            ["task_id", "conversation_id"],
            "agent_tasks",
            ["id", "conversation_id"],
            name="fk_agent_messages_task",
        ),
        Index("idx_agent_messages_task", "task_id", "seq"),
        Index("idx_agent_messages_item", "conversation_id", "item_seq"),
        UniqueConstraint("conversation_id", "seq", name="uk_agent_messages_seq"),
        UniqueConstraint("conversation_id", "idempotency_key", name="uk_agent_messages_request"),
        UniqueConstraint("id", "conversation_id", name="uk_agent_messages_parent"),
        CheckConstraint("seq > 0", name="ck_agent_messages_seq"),
        CheckConstraint("row_version > 0", name="ck_agent_messages_version"),
        CheckConstraint("role IN ('user','assistant')", name="ck_agent_messages_role"),
        CheckConstraint("kind IN ('reply','narration','notice')", name="ck_agent_messages_kind"),
        CheckConstraint(
            "JSON_TYPE(`references`) = 'ARRAY' AND JSON_TYPE(artifacts) = 'ARRAY'",
            name="ck_agent_messages_arrays",
        ),
        CheckConstraint("OCTET_LENGTH(content) <= 1048576", name="ck_agent_messages_content_size"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(content)) > 0 OR JSON_LENGTH(`references`) > 0 OR "
            "JSON_LENGTH(artifacts) > 0",
            name="ck_agent_messages_nonempty",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL) = (request_hash IS NULL)",
            name="ck_agent_messages_request_pair",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR (role = 'user' AND CHAR_LENGTH(idempotency_key) = 64 "
            "AND CHAR_LENGTH(request_hash) = 64)",
            name="ck_agent_messages_request_hash",
        ),
        OPTIONS,
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    run_id: Mapped[int | None] = _number(nullable=True)
    seq: Mapped[int] = _number()
    event_type: Mapped[str] = mapped_column(VARCHAR(64, collation="utf8mb4_0900_bin"))
    payload: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_events_conversation"),
        _fk(
            ["run_id", "conversation_id"],
            "agent_runs",
            ["id", "conversation_id"],
            name="fk_agent_events_run",
        ),
        UniqueConstraint("conversation_id", "seq", name="uk_agent_events_seq"),
        Index("idx_agent_events_run", "run_id", "seq"),
        CheckConstraint(
            "seq > 0 AND CHAR_LENGTH(TRIM(event_type)) > 0", name="ck_agent_events_required"
        ),
        CheckConstraint("JSON_TYPE(payload) = 'OBJECT'", name="ck_agent_events_payload"),
        OPTIONS,
    )
