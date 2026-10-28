"""Small LangGraph snapshots and node writes; never store model context here."""

from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentGraphCheckpoint(Base):
    __tablename__ = "agent_graph_checkpoints"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    task_id: Mapped[int] = _number()
    run_id: Mapped[int] = _number()
    namespace: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    checkpoint_id: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    parent_id: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    graph_version: Mapped[str] = mapped_column(VARCHAR(40))
    checkpoint: Mapped[dict] = _json()
    checkpoint_metadata: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_graph_checkpoints_task"),
        _fk(["run_id"], "agent_runs", name="fk_agent_graph_checkpoints_run"),
        _fk(["owner_user_id"], "users", name="fk_agent_graph_checkpoints_owner"),
        UniqueConstraint("task_id", "namespace", "checkpoint_id", name="uk_agent_graph_checkpoint"),
        Index("idx_agent_graph_checkpoints_run", "run_id", "id"),
        OPTIONS,
    )


class AgentGraphWrite(Base):
    __tablename__ = "agent_graph_writes"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    task_id: Mapped[int] = _number()
    run_id: Mapped[int] = _number()
    namespace: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    checkpoint_id: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    task_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    write_index: Mapped[int] = mapped_column()
    task_path: Mapped[str] = mapped_column(VARCHAR(512))
    channel: Mapped[str] = mapped_column(VARCHAR(128))
    payload: Mapped[dict] = _json()
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["task_id"], "agent_tasks", name="fk_agent_graph_writes_task"),
        _fk(["run_id"], "agent_runs", name="fk_agent_graph_writes_run"),
        _fk(["owner_user_id"], "users", name="fk_agent_graph_writes_owner"),
        UniqueConstraint(
            "task_id",
            "namespace",
            "checkpoint_id",
            "task_key",
            "write_index",
            name="uk_agent_graph_write",
        ),
        Index("idx_agent_graph_writes_run", "run_id", "id"),
        OPTIONS,
    )
