from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import INTEGER, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = _id()
    project_id: Mapped[int] = _number()
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
        _fk(["tool_call_id"], "agent_tool_calls", name="fk_agent_artifacts_tool"),
        _fk(["script_id"], "project_scripts", name="fk_agent_artifacts_script"),
        _fk(["parent_script_id"], "project_scripts", name="fk_agent_artifacts_parent_script"),
        _fk(["generation_task_id"], "async_tasks", name="fk_agent_artifacts_generation"),
        _fk(["media_asset_id"], "media_assets", name="fk_agent_artifacts_media_asset"),
        _fk(["media_id"], "media_files", name="fk_agent_artifacts_media"),
        _fk(["target_asset_id"], "assets", name="fk_agent_artifacts_asset"),
        _fk(["target_shot_id"], "shot_scripts", name="fk_agent_artifacts_shot"),
        _fk(["created_by"], "users", name="fk_agent_artifacts_creator"),
        _fk(["applied_by"], "users", name="fk_agent_artifacts_adopter"),
        UniqueConstraint("tool_call_id", "result_index", name="uk_agent_artifacts_result"),
        UniqueConstraint("script_id", name="uk_agent_artifacts_script"),
        Index("idx_agent_artifacts_creation", "project_id", "kind", "status", "created_at", "id"),
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
