"""Account conversation tasks, immutable inputs, ordered controls and operation receipts."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _hash, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[int] = _id()
    conversation_id: Mapped[int] = _number()
    goal: Mapped[str] = mapped_column(MEDIUMTEXT())
    status: Mapped[str] = mapped_column(VARCHAR(24), server_default=text("'queued'"))
    phase: Mapped[str] = mapped_column(VARCHAR(24), server_default=text("'at_boundary'"))
    row_version: Mapped[int] = _number(1)
    plan_revision: Mapped[int] = _number(1)
    input_revision: Mapped[int] = _number(0)
    applied_input_revision: Mapped[int] = _number(0)
    control_seq: Mapped[int] = _number(0)
    applied_control_seq: Mapped[int] = _number(0)
    control_state: Mapped[str] = mapped_column(VARCHAR(24), server_default=text("'none'"))
    execution_target: Mapped[dict | None] = _json(nullable=True)
    memory_project_ids: Mapped[list] = _json(array=True)
    context_snapshot: Mapped[dict | None] = _json(nullable=True)
    selected_skills: Mapped[list] = _json(array=True)
    effective_state: Mapped[dict] = _json()
    media_limits: Mapped[dict] = _json()
    media_usage: Mapped[dict] = _json()
    error: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_tasks_conversation"),
        UniqueConstraint("id", "conversation_id", name="uk_agent_tasks_parent"),
        Index("idx_agent_tasks_queue", "conversation_id", "status", "id"),
        CheckConstraint(
            "status IN ('queued','running','waiting_media','succeeded','stopped',"
            "'paused_error','paused_budget')",
            name="ck_agent_tasks_status",
        ),
        CheckConstraint(
            "phase IN ('deciding','tool_running','waiting_media','settling_result',"
            "'reconciling','at_boundary','terminal')",
            name="ck_agent_tasks_phase",
        ),
        CheckConstraint(
            "control_state IN ('none','stop_pending','redirect_pending','delete_pending')",
            name="ck_agent_tasks_control",
        ),
        CheckConstraint(
            "row_version > 0 AND plan_revision > 0 AND input_revision >= applied_input_revision "
            "AND control_seq >= applied_control_seq",
            name="ck_agent_tasks_versions",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(goal)) > 0", name="ck_agent_tasks_goal"),
        CheckConstraint(
            "JSON_TYPE(memory_project_ids) = 'ARRAY' AND JSON_TYPE(selected_skills) = 'ARRAY' "
            "AND JSON_TYPE(effective_state) = 'OBJECT' AND JSON_TYPE(media_limits) = 'OBJECT' "
            "AND JSON_TYPE(media_usage) = 'OBJECT'",
            name="ck_agent_tasks_objects",
        ),
        OPTIONS,
    )


class AgentTaskInput(Base):
    __tablename__ = "agent_task_inputs"

    id: Mapped[int] = _id()
    task_id: Mapped[int] = _number()
    message_id: Mapped[int] = _number()
    input_revision: Mapped[int] = _number()
    payload: Mapped[dict] = _json()
    applied_plan_revision: Mapped[int | None] = _number(nullable=True)
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_task_inputs_task"),
        _fk(["message_id"], "agent_messages", name="fk_agent_task_inputs_message"),
        UniqueConstraint("task_id", "input_revision", name="uk_agent_task_inputs_revision"),
        UniqueConstraint("message_id", name="uk_agent_task_inputs_message"),
        CheckConstraint("input_revision > 0", name="ck_agent_task_inputs_revision"),
        CheckConstraint("JSON_TYPE(payload) = 'OBJECT'", name="ck_agent_task_inputs_payload"),
        OPTIONS,
    )


class AgentTaskControl(Base):
    __tablename__ = "agent_task_controls"

    id: Mapped[int] = _id()
    task_id: Mapped[int] = _number()
    seq: Mapped[int] = _number()
    kind: Mapped[str] = mapped_column(VARCHAR(16))
    request_key: Mapped[str] = _hash()
    request_hash: Mapped[str] = _hash()
    expected_control_seq: Mapped[int | None] = _number(nullable=True)
    source_message_id: Mapped[int | None] = _number(nullable=True)
    requested_after_operation_id: Mapped[int | None] = _number(nullable=True)
    applied_after_operation_id: Mapped[int | None] = _number(nullable=True)
    created_at: Mapped[datetime] = _time()
    applied_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_task_controls_task"),
        _fk(["source_message_id"], "agent_messages", name="fk_agent_task_controls_message"),
        UniqueConstraint("task_id", "seq", name="uk_agent_task_controls_seq"),
        UniqueConstraint("task_id", "request_key", name="uk_agent_task_controls_request"),
        CheckConstraint("seq > 0", name="ck_agent_task_controls_seq"),
        CheckConstraint(
            "kind IN ('stop','redirect','resume','delete')", name="ck_agent_task_controls_kind"
        ),
        OPTIONS,
    )


class AgentOperation(Base):
    __tablename__ = "agent_operations"

    id: Mapped[int] = _id()
    task_id: Mapped[int] = _number()
    run_id: Mapped[int] = _number()
    tool_call_id: Mapped[int] = _number()
    tool_name: Mapped[str] = mapped_column(VARCHAR(80))
    item_seq: Mapped[int | None] = _number(nullable=True)
    tool_version: Mapped[int] = _number(1)
    plan_revision: Mapped[int] = _number()
    input_revision: Mapped[int] = _number()
    payload_hash: Mapped[str] = _hash()
    arguments: Mapped[dict] = _json()
    status: Mapped[str] = mapped_column(VARCHAR(24), server_default=text("'running'"))
    phase: Mapped[str] = mapped_column(VARCHAR(24), server_default=text("'tool_running'"))
    row_version: Mapped[int] = _number(1)
    execution_epoch: Mapped[int] = _number(1)
    active_task_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        Computed(
            "CASE WHEN status IN ('running','waiting_media','reconciling') "
            "THEN task_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    result: Mapped[dict | None] = _json(nullable=True)
    result_digest: Mapped[str | None] = _hash(nullable=True)
    evidence_kind: Mapped[str | None] = mapped_column(
        VARCHAR(16, charset="ascii", collation="ascii_bin"), nullable=True
    )
    result_refs: Mapped[list] = _json(array=True)
    changes: Mapped[list] = _json(array=True)
    error: Mapped[dict | None] = _json(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()
    finished_at: Mapped[datetime | None] = _time(nullable=True)

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_operations_task"),
        _fk(["run_id"], "agent_runs", name="fk_agent_operations_run"),
        _fk(["tool_call_id"], "agent_tool_calls", name="fk_agent_operations_call"),
        UniqueConstraint("tool_call_id", name="uk_agent_operations_call"),
        UniqueConstraint("active_task_id", name="uk_agent_operations_active"),
        Index("idx_agent_operations_task", "task_id", "id"),
        Index("idx_agent_operations_item", "item_seq"),
        Index(
            "idx_agent_operations_evidence",
            "task_id",
            "result_digest",
            mysql_length={"result_digest": 64},
        ),
        CheckConstraint(
            "status IN ('running','waiting_media','reconciling','succeeded','failed','conflict')",
            name="ck_agent_operations_status",
        ),
        CheckConstraint(
            "evidence_kind IS NULL OR "
            "evidence_kind IN ('write','read','observation','skill','none')",
            name="ck_agent_operations_evidence",
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','conflict')) = (finished_at IS NOT NULL)",
            name="ck_agent_operations_terminal",
        ),
        CheckConstraint(
            "tool_version > 0 AND plan_revision > 0 AND row_version > 0 AND execution_epoch > 0",
            name="ck_agent_operations_versions",
        ),
        OPTIONS,
    )
