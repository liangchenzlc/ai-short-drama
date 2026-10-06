"""Private Agent execution records and candidate references.

Conversation ownership is deliberately separate from ResourceScope: membership
alone must not expose another member's messages, model turns, or tool arguments.
Artifacts stay with their creator; only adopted business work is shared.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .collaboration import OPTIONS

AGENT_PRIVATE_TABLES = frozenset(
    {
        "agent_conversations",
        "agent_messages",
        "agent_runs",
        "agent_turns",
        "agent_tool_calls",
        "agent_events",
    }
)
AGENT_TABLES = AGENT_PRIVATE_TABLES | {"agent_artifacts", "agent_attachments", "agent_skills"}


def _id():
    return mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)


def _number(default=None, *, nullable=False):
    return mapped_column(
        BIGINT(unsigned=True),
        nullable=nullable,
        server_default=text(str(default)) if default is not None else None,
    )


def _time(*, nullable=False):
    return mapped_column(
        DATETIME(fsp=6),
        nullable=nullable,
        server_default=None if nullable else text("CURRENT_TIMESTAMP(6)"),
    )


def _json(*, nullable=False, array=False):
    return mapped_column(
        JSON(),
        nullable=nullable,
        server_default=None if nullable else text("(JSON_ARRAY())" if array else "(JSON_OBJECT())"),
    )


def _hash(*, nullable=False):
    return mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"), nullable=nullable)


def _fk(fields, table, targets=None, *, name):
    return ForeignKeyConstraint(
        fields,
        [f"{table}.{field}" for field in (targets or ["id"])],
        name=name,
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    project_id: Mapped[int] = _number()
    episode_id: Mapped[int] = _number()
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
    fixed_requirements: Mapped[dict] = _json()
    create_key: Mapped[str | None] = mapped_column(
        VARCHAR(64, charset="ascii", collation="ascii_bin"), nullable=True
    )
    create_hash: Mapped[str | None] = _hash(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_conversations_owner"),
        _fk(["project_id"], "projects", name="fk_agent_conversations_project"),
        _fk(["episode_id"], "episodes", name="fk_agent_conversations_episode"),
        UniqueConstraint("owner_user_id", "create_key", name="uk_agent_conversation_create"),
        Index(
            "idx_agent_conversations_owner_episode",
            "owner_user_id",
            "episode_id",
            "updated_at",
            "id",
        ),
        Index("idx_agent_conversations_project", "project_id", "owner_user_id", "id"),
        Index(
            "idx_agent_conversations_scope",
            "owner_user_id",
            "episode_id",
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
            "(scope_version = 0 AND stage IS NULL AND subject_type IS NULL "
            "AND subject_id IS NULL AND task_type IS NULL) OR "
            "(scope_version = 1 AND stage IS NOT NULL AND subject_type IS NOT NULL "
            "AND subject_id IS NOT NULL AND subject_id > 0 AND task_type IS NOT NULL AND ("
            "(stage = 'source' AND subject_type = 'episode' AND subject_id = episode_id "
            "AND task_type = 'writing') OR "
            "(stage = 'assets' AND subject_type = 'episode' AND subject_id = episode_id "
            "AND task_type IN ('extraction','batch')) OR "
            "(stage = 'assets' AND subject_type = 'asset' "
            "AND task_type IN ('creation','image')) OR "
            "(stage = 'storyboard' AND subject_type = 'episode' AND subject_id = episode_id "
            "AND task_type IN ('planning','batch')) OR "
            "(stage = 'storyboard' AND subject_type = 'shot' "
            "AND task_type IN ('creation','image','video'))))",
            name="ck_agent_conversations_scope",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_agent_conversations_status"),
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
    seq: Mapped[int] = _number()
    role: Mapped[str] = mapped_column(VARCHAR(16, collation="utf8mb4_0900_bin"))
    content: Mapped[str] = mapped_column(MEDIUMTEXT(), server_default=text("('')"))
    references: Mapped[list] = _json(array=True)
    artifacts: Mapped[list] = _json(array=True)
    idempotency_key: Mapped[str | None] = _hash(nullable=True)
    request_hash: Mapped[str | None] = _hash(nullable=True)
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_messages_conversation"),
        UniqueConstraint("conversation_id", "seq", name="uk_agent_messages_seq"),
        UniqueConstraint("conversation_id", "idempotency_key", name="uk_agent_messages_request"),
        UniqueConstraint("id", "conversation_id", name="uk_agent_messages_parent"),
        CheckConstraint("seq > 0", name="ck_agent_messages_seq"),
        CheckConstraint("role IN ('user','assistant')", name="ck_agent_messages_role"),
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


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    trigger_message_id: Mapped[int] = _number()
    initiated_by: Mapped[int] = _number()
    model_config_id: Mapped[int] = _number()
    status: Mapped[str] = mapped_column(
        VARCHAR(24, collation="utf8mb4_0900_bin"), server_default=text("'queued'")
    )
    phase: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), server_default=text("'model'")
    )
    active_conversation_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        Computed(
            "CASE WHEN status IN ('queued','running','waiting_generation','waiting_review') "
            "THEN conversation_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    row_version: Mapped[int] = _number(1)
    checkpoint_schema_version: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), server_default=text("1")
    )
    checkpoint: Mapped[dict] = _json()
    config_snapshot: Mapped[dict] = _json()
    budget: Mapped[dict] = _json()
    usage: Mapped[dict] = _json()
    message_status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), server_default=text("'pending'")
    )
    message_version: Mapped[int] = _number(1)
    next_run_at: Mapped[datetime | None] = _time(nullable=True)
    lease_token: Mapped[str | None] = mapped_column(
        CHAR(32, charset="ascii", collation="ascii_bin"), nullable=True
    )
    lease_until: Mapped[datetime | None] = _time(nullable=True)
    cancel_requested: Mapped[int] = mapped_column(INTEGER(unsigned=True), server_default=text("0"))
    error: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()
    started_at: Mapped[datetime | None] = _time(nullable=True)
    finished_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_runs_conversation"),
        _fk(
            ["trigger_message_id", "conversation_id"],
            "agent_messages",
            ["id", "conversation_id"],
            name="fk_agent_runs_trigger_message",
        ),
        _fk(["initiated_by"], "users", name="fk_agent_runs_initiator"),
        _fk(["model_config_id"], "ai_model_configs", name="fk_agent_runs_model"),
        UniqueConstraint("trigger_message_id", name="uk_agent_runs_trigger"),
        Index("idx_agent_runs_active", "active_conversation_id"),
        UniqueConstraint("id", "conversation_id", name="uk_agent_runs_parent"),
        Index("idx_agent_runs_schedule", "message_status", "next_run_at", "id"),
        Index("idx_agent_runs_lease", "lease_until", "status", "id"),
        Index("idx_agent_runs_history", "conversation_id", "created_at", "id"),
        CheckConstraint(
            "status IN ('queued','running','waiting_generation','waiting_review','succeeded',"
            "'failed','cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("phase IN ('model','tools','wait')", name="ck_agent_runs_phase"),
        CheckConstraint(
            "message_status IN ('pending','publishing','published','idle')",
            name="ck_agent_runs_message_status",
        ),
        CheckConstraint(
            "row_version > 0 AND message_version > 0 AND checkpoint_schema_version > 0",
            name="ck_agent_runs_versions",
        ),
        CheckConstraint("cancel_requested IN (0,1)", name="ck_agent_runs_cancel"),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL)", name="ck_agent_runs_lease_pair"
        ),
        CheckConstraint(
            "JSON_TYPE(checkpoint) = 'OBJECT' AND JSON_TYPE(config_snapshot) = 'OBJECT' AND "
            "JSON_TYPE(budget) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT'",
            name="ck_agent_runs_objects",
        ),
        CheckConstraint("error IS NULL OR JSON_TYPE(error) = 'OBJECT'", name="ck_agent_runs_error"),
        CheckConstraint(
            "(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="ck_agent_runs_terminal_time",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL "
            "OR finished_at IS NULL OR finished_at >= started_at)",
            name="ck_agent_runs_time",
        ),
        OPTIONS,
    )


class AgentTurn(Base):
    __tablename__ = "agent_turns"

    id: Mapped[int] = _id()
    run_id: Mapped[int] = _number()
    turn_no: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), server_default=text("'prepared'")
    )
    request_messages: Mapped[list] = _json(array=True)
    response: Mapped[dict | None] = _json(nullable=True)
    usage: Mapped[dict] = _json()
    provider_response_id: Mapped[str | None] = mapped_column(
        VARCHAR(255, collation="utf8mb4_0900_bin"), nullable=True
    )
    error: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()
    started_at: Mapped[datetime | None] = _time(nullable=True)
    finished_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["run_id"], "agent_runs", name="fk_agent_turns_run"),
        UniqueConstraint("run_id", "turn_no", name="uk_agent_turns_number"),
        UniqueConstraint("id", "run_id", name="uk_agent_turns_parent"),
        CheckConstraint("turn_no > 0", name="ck_agent_turns_number"),
        CheckConstraint(
            "status IN ('prepared','sent','succeeded','failed','unknown')",
            name="ck_agent_turns_status",
        ),
        CheckConstraint(
            "JSON_TYPE(request_messages) = 'ARRAY' AND JSON_TYPE(`usage`) = 'OBJECT'",
            name="ck_agent_turns_payload",
        ),
        CheckConstraint(
            "(response IS NULL OR JSON_TYPE(response) = 'OBJECT') AND (error IS NULL OR "
            "JSON_TYPE(error) = 'OBJECT')",
            name="ck_agent_turns_result",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL "
            "OR finished_at IS NULL OR finished_at >= started_at)",
            name="ck_agent_turns_time",
        ),
        OPTIONS,
    )


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = _id()
    run_id: Mapped[int] = _number()
    turn_id: Mapped[int] = _number()
    call_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    provider_call_id: Mapped[str] = mapped_column(VARCHAR(255, collation="utf8mb4_0900_bin"))
    tool_name: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    arguments: Mapped[dict] = _json()
    arguments_hash: Mapped[str] = _hash()
    idempotency_key: Mapped[str] = _hash()
    status: Mapped[str] = mapped_column(
        VARCHAR(24, collation="utf8mb4_0900_bin"), server_default=text("'prepared'")
    )
    result: Mapped[dict | None] = _json(nullable=True)
    generation_task_id: Mapped[int | None] = _number(nullable=True)
    review_payload: Mapped[dict | None] = _json(nullable=True)
    review_hash: Mapped[str | None] = _hash(nullable=True)
    review_version: Mapped[int] = _number(1)
    review_decision: Mapped[str | None] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), nullable=True
    )
    reviewed_by: Mapped[int | None] = _number(nullable=True)
    reviewed_at: Mapped[datetime | None] = _time(nullable=True)
    error: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["run_id"], "agent_runs", name="fk_agent_tool_calls_run"),
        _fk(
            ["turn_id", "run_id"], "agent_turns", ["id", "run_id"], name="fk_agent_tool_calls_turn"
        ),
        _fk(["generation_task_id"], "async_tasks", name="fk_agent_tool_calls_generation"),
        _fk(["reviewed_by"], "users", name="fk_agent_tool_calls_reviewer"),
        UniqueConstraint("turn_id", "provider_call_id", name="uk_agent_tool_calls_provider"),
        UniqueConstraint("turn_id", "call_index", name="uk_agent_tool_calls_order"),
        UniqueConstraint("idempotency_key", name="uk_agent_tool_calls_request"),
        UniqueConstraint("generation_task_id", name="uk_agent_tool_calls_generation"),
        Index("idx_agent_tool_calls_run_status", "run_id", "status", "id"),
        CheckConstraint(
            "call_index > 0 AND review_version > 0", name="ck_agent_tool_calls_numbers"
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(provider_call_id)) > 0 AND CHAR_LENGTH(TRIM(tool_name)) > 0 "
            "AND CHAR_LENGTH(arguments_hash) = 64 AND CHAR_LENGTH(idempotency_key) = 64",
            name="ck_agent_tool_calls_required",
        ),
        CheckConstraint(
            "status IN ('prepared','waiting_review','ready','running','waiting_generation',"
            "'succeeded','failed','rejected','cancelled')",
            name="ck_agent_tool_calls_status",
        ),
        CheckConstraint(
            "JSON_TYPE(arguments) = 'OBJECT' AND (result IS NULL OR JSON_TYPE(result) = "
            "'OBJECT') AND (error IS NULL OR JSON_TYPE(error) = 'OBJECT')",
            name="ck_agent_tool_calls_objects",
        ),
        CheckConstraint(
            "(review_payload IS NULL) = (review_hash IS NULL)",
            name="ck_agent_tool_calls_review_pair",
        ),
        CheckConstraint(
            "review_payload IS NULL OR (JSON_TYPE(review_payload) = 'OBJECT' AND "
            "CHAR_LENGTH(review_hash) = 64)",
            name="ck_agent_tool_calls_review_payload",
        ),
        CheckConstraint(
            "review_decision IS NULL OR review_decision IN ('approved','rejected')",
            name="ck_agent_tool_calls_review_decision",
        ),
        CheckConstraint(
            "(review_decision IS NULL AND reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(review_decision IS NOT NULL AND review_payload IS NOT NULL AND reviewed_by IS "
            "NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_agent_tool_calls_review_actor",
        ),
        CheckConstraint(
            "status <> 'waiting_review' OR (review_payload IS NOT NULL AND review_decision "
            "IS NULL)",
            name="ck_agent_tool_calls_review_pending",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (reviewed_at IS NULL OR reviewed_at >= created_at)",
            name="ck_agent_tool_calls_time",
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


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = _id()
    project_id: Mapped[int] = _number()
    episode_id: Mapped[int] = _number()
    tool_call_id: Mapped[int] = _number()
    result_index: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    kind: Mapped[str] = mapped_column(VARCHAR(32, collation="utf8mb4_0900_bin"))
    status: Mapped[str] = mapped_column(
        VARCHAR(16, collation="utf8mb4_0900_bin"), server_default=text("'ready'")
    )
    row_version: Mapped[int] = _number(1)
    source_snapshot: Mapped[dict] = _json()
    source_content: Mapped[str | None] = mapped_column(MEDIUMTEXT(), nullable=True)
    metadata_json: Mapped[dict] = _json()
    proposed_patch: Mapped[dict | None] = _json(nullable=True)
    script_id: Mapped[int | None] = _number(nullable=True)
    parent_script_id: Mapped[int | None] = _number(nullable=True)
    generation_task_id: Mapped[int | None] = _number(nullable=True)
    media_asset_id: Mapped[int | None] = _number(nullable=True)
    media_id: Mapped[int | None] = _number(nullable=True)
    target_asset_id: Mapped[int | None] = _number(nullable=True)
    target_shot_id: Mapped[int | None] = _number(nullable=True)
    created_by: Mapped[int] = _number()
    applied_by: Mapped[int | None] = _number(nullable=True)
    applied_at: Mapped[datetime | None] = _time(nullable=True)
    apply_receipt: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["project_id"], "projects", name="fk_agent_artifacts_project"),
        _fk(["episode_id"], "episodes", name="fk_agent_artifacts_episode"),
        _fk(["tool_call_id"], "agent_tool_calls", name="fk_agent_artifacts_tool"),
        _fk(["script_id"], "episode_scripts", name="fk_agent_artifacts_script"),
        _fk(["parent_script_id"], "episode_scripts", name="fk_agent_artifacts_parent_script"),
        _fk(["generation_task_id"], "async_tasks", name="fk_agent_artifacts_generation"),
        _fk(["media_asset_id"], "media_assets", name="fk_agent_artifacts_media_asset"),
        _fk(["media_id"], "media_files", name="fk_agent_artifacts_media"),
        _fk(["target_asset_id"], "assets", name="fk_agent_artifacts_asset"),
        _fk(["target_shot_id"], "shot_scripts", name="fk_agent_artifacts_shot"),
        _fk(["created_by"], "users", name="fk_agent_artifacts_creator"),
        _fk(["applied_by"], "users", name="fk_agent_artifacts_adopter"),
        UniqueConstraint("tool_call_id", "result_index", name="uk_agent_artifacts_result"),
        UniqueConstraint("script_id", name="uk_agent_artifacts_script"),
        Index("idx_agent_artifacts_episode", "episode_id", "kind", "status", "created_at", "id"),
        Index("idx_agent_artifacts_generation", "generation_task_id", "id"),
        CheckConstraint(
            "kind IN ('novel_proposal','text_proposal','script_candidate','asset_patch',"
            "'shot_patch','extraction_candidate','storyboard_candidate','image_candidate',"
            "'video_candidate')",
            name="ck_agent_artifacts_kind",
        ),
        CheckConstraint(
            "status IN ('ready','applied','rejected','archived')", name="ck_agent_artifacts_status"
        ),
        CheckConstraint("row_version > 0 AND result_index > 0", name="ck_agent_artifacts_numbers"),
        CheckConstraint(
            "JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(metadata_json) = 'OBJECT' "
            "AND (proposed_patch IS NULL OR JSON_TYPE(proposed_patch) = 'OBJECT') AND "
            "(apply_receipt IS NULL OR JSON_TYPE(apply_receipt) = 'OBJECT')",
            name="ck_agent_artifacts_objects",
        ),
        CheckConstraint(
            "source_content IS NULL OR OCTET_LENGTH(source_content) <= 1048576",
            name="ck_agent_artifacts_source_size",
        ),
        CheckConstraint(
            "kind NOT IN ('novel_proposal','text_proposal') OR (source_content IS NOT NULL "
            "AND CHAR_LENGTH(TRIM(source_content)) > 0)",
            name="ck_agent_artifacts_text_proposal",
        ),
        CheckConstraint(
            "kind <> 'script_candidate' OR script_id IS NOT NULL",
            name="ck_agent_artifacts_script_candidate",
        ),
        CheckConstraint(
            "kind <> 'asset_patch' OR (target_asset_id IS NOT NULL AND proposed_patch IS NOT NULL)",
            name="ck_agent_artifacts_asset_patch",
        ),
        CheckConstraint(
            "kind <> 'shot_patch' OR (target_shot_id IS NOT NULL AND proposed_patch IS NOT NULL)",
            name="ck_agent_artifacts_shot_patch",
        ),
        CheckConstraint(
            "(status = 'applied' AND applied_by IS NOT NULL AND applied_at IS NOT NULL AND "
            "apply_receipt IS NOT NULL) OR (status <> 'applied' AND applied_by IS NULL AND "
            "applied_at IS NULL AND apply_receipt IS NULL)",
            name="ck_agent_artifacts_adoption",
        ),
        CheckConstraint(
            "updated_at >= created_at AND (applied_at IS NULL OR applied_at >= created_at)",
            name="ck_agent_artifacts_time",
        ),
        OPTIONS,
    )
