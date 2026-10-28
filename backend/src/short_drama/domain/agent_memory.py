"""Versioned, owner-scoped memory, immutable provenance and durable maintenance jobs."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import CHAR, MEDIUMTEXT, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .agent import _fk, _hash, _id, _json, _number, _time
from .base import Base
from .collaboration import OPTIONS


def scope_check(name):
    return CheckConstraint(
        "(scope_type = 'user' AND scope_key = 'user' AND conversation_id IS NULL "
        "AND project_id IS NULL) OR (scope_type = 'conversation' AND conversation_id IS NOT NULL "
        "AND project_id IS NULL AND scope_key = CONCAT(conversation_id)) OR "
        "(scope_type = 'project' AND project_id IS NOT NULL AND conversation_id IS NULL "
        "AND scope_key = CONCAT(project_id))",
        name=name,
    )


class AgentMemoryItem(Base):
    __tablename__ = "agent_memory_items"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    scope_type: Mapped[str] = mapped_column(VARCHAR(16))
    scope_key: Mapped[str] = mapped_column(VARCHAR(128))
    conversation_id: Mapped[int | None] = _number(nullable=True)
    project_id: Mapped[int | None] = _number(nullable=True)
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    memory_key: Mapped[str] = mapped_column(VARCHAR(160, collation="utf8mb4_0900_bin"))
    title: Mapped[str] = mapped_column(VARCHAR(200))
    aliases: Mapped[list] = _json(array=True)
    tags: Mapped[list] = _json(array=True)
    content: Mapped[str] = mapped_column(MEDIUMTEXT())
    search_text: Mapped[str] = mapped_column(MEDIUMTEXT())
    content_version: Mapped[int] = _number(1)
    state: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'active'"))
    origin: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'explicit'"))
    pinned: Mapped[int] = mapped_column(TINYINT(unsigned=True), server_default=text("0"))
    last_user_edit_at: Mapped[datetime | None] = _time(nullable=True)
    hit_count: Mapped[int] = _number(0)
    last_used_at: Mapped[datetime | None] = _time(nullable=True)
    create_key: Mapped[str | None] = _hash(nullable=True)
    create_hash: Mapped[str | None] = _hash(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_memory_items_owner"),
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_memory_items_conversation"),
        _fk(["project_id"], "projects", name="fk_agent_memory_items_project"),
        UniqueConstraint(
            "owner_user_id",
            "scope_type",
            "scope_key",
            "kind",
            "memory_key",
            name="uk_agent_memory_identity",
        ),
        UniqueConstraint("owner_user_id", "create_key", name="uk_agent_memory_create"),
        UniqueConstraint("id", "owner_user_id", name="uk_agent_memory_owner"),
        Index(
            "idx_agent_memory_catalog",
            "owner_user_id",
            "state",
            "scope_type",
            "scope_key",
            "pinned",
            "id",
        ),
        Index(
            "idx_agent_memory_fulltext",
            "search_text",
            mysql_prefix="FULLTEXT",
            mysql_with_parser="ngram",
        ),
        scope_check("ck_agent_memory_scope"),
        CheckConstraint(
            "kind IN ('preference','correction','setting','decision','reference')",
            name="ck_agent_memory_kind",
        ),
        CheckConstraint(
            "state IN ('active','forgotten','superseded') AND origin IN ('explicit','extracted')",
            name="ck_agent_memory_state",
        ),
        CheckConstraint(
            "content_version > 0 AND pinned IN (0,1) AND CHAR_LENGTH(TRIM(content)) > 0 "
            "AND CHAR_LENGTH(TRIM(title)) > 0",
            name="ck_agent_memory_values",
        ),
        CheckConstraint(
            "JSON_TYPE(aliases) = 'ARRAY' AND JSON_TYPE(tags) = 'ARRAY'",
            name="ck_agent_memory_arrays",
        ),
        CheckConstraint(
            "(create_key IS NULL) = (create_hash IS NULL)", name="ck_agent_memory_create_pair"
        ),
        OPTIONS,
    )


class AgentMemoryRevision(Base):
    __tablename__ = "agent_memory_revisions"

    id: Mapped[int] = _id()
    memory_id: Mapped[int] = _number()
    content_version: Mapped[int] = _number()
    change: Mapped[str] = mapped_column(VARCHAR(16))
    snapshot: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["memory_id"], "agent_memory_items", name="fk_agent_memory_revisions_item"),
        UniqueConstraint("memory_id", "content_version", name="uk_agent_memory_revision"),
        CheckConstraint(
            "content_version > 0 AND `change` IN "
            "('create','edit','forget','restore','extract','invalidate')",
            name="ck_agent_memory_revision",
        ),
        CheckConstraint("JSON_TYPE(snapshot) = 'OBJECT'", name="ck_agent_memory_revision_snapshot"),
        OPTIONS,
    )


class AgentMemorySource(Base):
    __tablename__ = "agent_memory_sources"

    id: Mapped[int] = _id()
    memory_id: Mapped[int] = _number()
    content_version: Mapped[int] = _number()
    conversation_id: Mapped[int] = _number()
    message_id: Mapped[int | None] = _number(nullable=True)
    source_seq: Mapped[int] = _number()
    source_hash: Mapped[str] = _hash()
    subject_keys: Mapped[list] = _json(array=True)
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["memory_id"], "agent_memory_items", name="fk_agent_memory_sources_item"),
        _fk(
            ["conversation_id"], "agent_conversations", name="fk_agent_memory_sources_conversation"
        ),
        _fk(
            ["message_id", "conversation_id"],
            "agent_messages",
            ["id", "conversation_id"],
            name="fk_agent_memory_sources_message",
        ),
        UniqueConstraint(
            "memory_id",
            "content_version",
            "conversation_id",
            "source_seq",
            name="uk_agent_memory_source",
        ),
        Index(
            "idx_agent_memory_sources_conversation",
            "conversation_id",
            "memory_id",
            "content_version",
        ),
        CheckConstraint(
            "content_version > 0 AND source_seq > 0 AND JSON_TYPE(subject_keys) = 'ARRAY'",
            name="ck_agent_memory_source",
        ),
        OPTIONS,
    )


class AgentMemoryForgetRule(Base):
    __tablename__ = "agent_memory_forget_rules"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    memory_id: Mapped[int] = _number()
    scope_type: Mapped[str] = mapped_column(VARCHAR(16))
    scope_key: Mapped[str] = mapped_column(VARCHAR(128))
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    memory_key: Mapped[str] = mapped_column(VARCHAR(160, collation="utf8mb4_0900_bin"))
    forget_version: Mapped[int] = _number()
    source_fingerprints: Mapped[list] = _json(array=True)
    active: Mapped[int] = mapped_column(TINYINT(unsigned=True), server_default=text("1"))
    created_at: Mapped[datetime] = _time()
    restored_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(
            ["memory_id", "owner_user_id"],
            "agent_memory_items",
            ["id", "owner_user_id"],
            name="fk_agent_memory_forget_item",
        ),
        UniqueConstraint("memory_id", name="uk_agent_memory_forget_item"),
        Index(
            "idx_agent_memory_forget_identity",
            "owner_user_id",
            "scope_type",
            "scope_key",
            "kind",
            "memory_key",
            "active",
        ),
        CheckConstraint(
            "forget_version > 0 AND active IN (0,1) AND JSON_TYPE(source_fingerprints) = 'ARRAY'",
            name="ck_agent_memory_forget",
        ),
        OPTIONS,
    )


class AgentConversationSummary(Base):
    __tablename__ = "agent_conversation_summaries"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    conversation_id: Mapped[int] = _number()
    from_seq: Mapped[int] = _number()
    to_seq: Mapped[int] = _number()
    base_summary_id: Mapped[int | None] = _number(nullable=True)
    base_generation: Mapped[int] = _number(0)
    source_hash: Mapped[str] = _hash()
    content: Mapped[dict] = _json()
    token_estimate: Mapped[int] = _number(0)
    model_config_id: Mapped[int | None] = _number(nullable=True)
    usage: Mapped[dict] = _json()
    status: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'ready'"))
    created_at: Mapped[datetime] = _time()
    activated_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_summaries_owner"),
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_summaries_conversation"),
        _fk(["base_summary_id"], "agent_conversation_summaries", name="fk_agent_summaries_base"),
        _fk(["model_config_id"], "ai_model_configs", name="fk_agent_summaries_model"),
        UniqueConstraint("id", "conversation_id", name="uk_agent_summaries_parent"),
        Index("idx_agent_summaries_range", "conversation_id", "from_seq", "to_seq", "status"),
        CheckConstraint(
            "from_seq > 0 AND to_seq >= from_seq "
            "AND status IN ('ready','active','stale','invalidated')",
            name="ck_agent_summaries_range",
        ),
        CheckConstraint(
            "JSON_TYPE(content) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT'",
            name="ck_agent_summaries_content",
        ),
        OPTIONS,
    )


class AgentMemoryJob(Base):
    __tablename__ = "agent_memory_jobs"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    conversation_id: Mapped[int] = _number()
    run_id: Mapped[int | None] = _number(nullable=True)
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    request_key: Mapped[str] = _hash()
    from_seq: Mapped[int] = _number()
    to_seq: Mapped[int] = _number()
    base_summary_id: Mapped[int | None] = _number(nullable=True)
    base_generation: Mapped[int] = _number(0)
    source_hash: Mapped[str] = _hash()
    source_snapshot: Mapped[dict] = _json()
    status: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'pending'"))
    provider_state: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'not_started'"))
    raw_result: Mapped[dict | None] = _json(nullable=True)
    usage: Mapped[dict] = _json()
    error: Mapped[dict | None] = _json(nullable=True)
    attempts: Mapped[int] = _number(0)
    message_version: Mapped[int] = _number(1)
    message_status: Mapped[str] = mapped_column(VARCHAR(16), server_default=text("'queued'"))
    publish_token: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    publish_until: Mapped[datetime | None] = _time(nullable=True)
    lease_token: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    lease_until: Mapped[datetime | None] = _time(nullable=True)
    next_run_at: Mapped[datetime] = _time()
    created_at: Mapped[datetime] = _time()
    finished_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_memory_jobs_owner"),
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_memory_jobs_conversation"),
        _fk(
            ["run_id", "conversation_id"],
            "agent_runs",
            ["id", "conversation_id"],
            name="fk_agent_memory_jobs_run",
        ),
        _fk(
            ["base_summary_id"], "agent_conversation_summaries", name="fk_agent_memory_jobs_summary"
        ),
        UniqueConstraint("owner_user_id", "request_key", name="uk_agent_memory_job_request"),
        Index("idx_agent_memory_jobs_due", "status", "message_status", "next_run_at", "id"),
        CheckConstraint(
            "kind IN ('summary','extract','task_state','purge') "
            "AND from_seq > 0 AND to_seq >= from_seq",
            name="ck_agent_memory_job_kind",
        ),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','stale','cancelled','abandoned') "
            "AND provider_state IN ('not_started','sending','received','unknown')",
            name="ck_agent_memory_job_status",
        ),
        CheckConstraint(
            "message_version > 0 AND message_status IN ('queued','publishing','published','idle')",
            name="ck_agent_memory_job_message",
        ),
        CheckConstraint(
            "JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT'",
            name="ck_agent_memory_job_content",
        ),
        OPTIONS,
    )
