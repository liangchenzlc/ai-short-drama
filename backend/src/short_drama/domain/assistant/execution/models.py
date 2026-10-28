from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, CHAR, INTEGER, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _hash, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    task_id: Mapped[int | None] = _number(nullable=True)
    trigger_message_id: Mapped[int] = _number()
    trigger_revision: Mapped[int] = _number(0)
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
            ["task_id", "conversation_id"],
            "agent_tasks",
            ["id", "conversation_id"],
            name="fk_agent_runs_task",
        ),
        Index("idx_agent_runs_task", "task_id", "id"),
        _fk(
            ["trigger_message_id", "conversation_id"],
            "agent_messages",
            ["id", "conversation_id"],
            name="fk_agent_runs_trigger_message",
        ),
        _fk(["initiated_by"], "users", name="fk_agent_runs_initiator"),
        _fk(["model_config_id"], "ai_model_configs", name="fk_agent_runs_model"),
        UniqueConstraint("trigger_message_id", "trigger_revision", name="uk_agent_runs_trigger"),
        CheckConstraint(
            "trigger_revision = 0 OR task_id IS NOT NULL", name="ck_agent_runs_trigger_revision"
        ),
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
