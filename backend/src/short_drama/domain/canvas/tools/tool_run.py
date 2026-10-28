"""审美与分镜的私人执行授权；阶段请求与任务关联不可替换。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.canvas.document.canvas import CanvasChild, canvas_parent
from short_drama.domain.options import OPTIONS


class CanvasToolRun(CanvasChild, Base):
    __tablename__ = "canvas_tool_runs"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    client_key: Mapped[str] = mapped_column(VARCHAR(120, charset="ascii", collation="ascii_bin"))
    create_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    state_json: Mapped[dict] = mapped_column(JSON)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    source_media_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("media_files.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    model_config_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("ai_model_configs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    config_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1)
    execution_epoch: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=0)
    execution_owner: Mapped[str] = mapped_column(VARCHAR(120), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    __table_args__ = (
        canvas_parent(),
        UniqueConstraint("user_id", "client_key", name="uk_canvas_tool_run_client"),
        UniqueConstraint(
            "id", "user_id", "project_id", "canvas_id", name="uk_canvas_tool_run_scope"
        ),
        Index("idx_canvas_tool_run_author", "user_id", "updated_at", "id"),
        CheckConstraint("row_version > 0", name="ck_canvas_tool_run_version"),
        CheckConstraint(
            "(execution_owner = '' AND lease_expires_at IS NULL) OR "
            "(execution_owner <> '' AND lease_expires_at IS NOT NULL AND execution_epoch > 0)",
            name="ck_canvas_tool_run_lease",
        ),
        CheckConstraint(
            "CHAR_LENGTH(client_key) > 0 AND CHAR_LENGTH(create_hash) = 64 "
            "AND CHAR_LENGTH(source_hash) = 64 AND CHAR_LENGTH(config_hash) = 64",
            name="ck_canvas_tool_run_identity",
        ),
        OPTIONS,
    )


class CanvasToolSubmission(CanvasChild, Base):
    __tablename__ = "canvas_tool_submissions"
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    run_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    item_key: Mapped[str] = mapped_column(VARCHAR(160, charset="ascii", collation="ascii_bin"))
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"))
    request_json: Mapped[dict] = mapped_column(JSON)
    execution_json: Mapped[dict] = mapped_column(JSON)
    approved_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    async_task_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("async_tasks.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        canvas_parent(),
        ForeignKeyConstraint(
            ["run_id", "user_id", "project_id", "canvas_id"],
            [
                "canvas_tool_runs.id",
                "canvas_tool_runs.user_id",
                "canvas_tool_runs.project_id",
                "canvas_tool_runs.canvas_id",
            ],
            name="fk_canvas_tool_submission_run",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("run_id", "item_key", name="uk_canvas_tool_submission_stage"),
        UniqueConstraint("async_task_id", name="uk_canvas_tool_submission_task"),
        CheckConstraint(
            "async_task_id IS NULL OR approved_at IS NOT NULL",
            name="ck_canvas_tool_submission_approval",
        ),
        CheckConstraint(
            "CHAR_LENGTH(item_key) > 0 AND CHAR_LENGTH(request_hash) = 64",
            name="ck_canvas_tool_submission_identity",
        ),
        OPTIONS,
    )
