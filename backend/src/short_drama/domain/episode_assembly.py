"""Saved edit decisions and independently scheduled local render jobs."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_row_format": "DYNAMIC",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class EpisodeAssembly(Base):
    __tablename__ = "episode_assemblies"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    episode_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    aspect: Mapped[str] = mapped_column(VARCHAR(8), nullable=False)
    resolution: Mapped[str] = mapped_column(
        VARCHAR(16), nullable=False, server_default=text("'720p'")
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    current_media_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    last_edit_receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    __table_args__ = (
        UniqueConstraint("episode_id", name="uk_assemblies_episode"),
        ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            name="fk_assemblies_episode",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_media_id"],
            ["media_files.id"],
            name="fk_assemblies_media",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("aspect IN ('16:9','9:16')", name="ck_assemblies_aspect"),
        CheckConstraint("resolution IN ('720p','1080p')", name="ck_assemblies_resolution"),
        CheckConstraint("row_version > 0", name="ck_assemblies_version"),
        {**TABLE_OPTIONS, "comment": "分集成片草稿与当前采用成片"},
    )


class EpisodeAssemblyClip(Base):
    __tablename__ = "episode_assembly_clips"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    assembly_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    shot_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    media_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    position: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    included: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("1")
    )
    muted: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    trim_in_ms: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )
    trim_out_ms: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    client_key: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"), nullable=True)
    removed: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    source_context_hash: Mapped[str | None] = mapped_column(
        CHAR(64, charset="ascii"), nullable=True
    )
    __table_args__ = (
        UniqueConstraint("assembly_id", "client_key", name="uk_assembly_clips_client"),
        Index("idx_assembly_clips_shot", "assembly_id", "shot_id"),
        UniqueConstraint("assembly_id", "position", name="uk_assembly_clips_position"),
        ForeignKeyConstraint(
            ["assembly_id"],
            ["episode_assemblies.id"],
            name="fk_assembly_clips_assembly",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["shot_id"],
            ["shot_scripts.id"],
            name="fk_assembly_clips_shot",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_assembly_clips_media",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("position > 0", name="ck_assembly_clips_position"),
        CheckConstraint("removed IN (0,1)", name="ck_assembly_clips_removed"),
        CheckConstraint("included IN (0,1) AND muted IN (0,1)", name="ck_assembly_clips_flags"),
        CheckConstraint(
            "trim_out_ms IS NULL OR trim_out_ms > trim_in_ms", name="ck_assembly_clips_trim"
        ),
        {**TABLE_OPTIONS, "comment": "成片片段顺序与非破坏性剪辑设置"},
    )


class EpisodeRenderJob(Base):
    __tablename__ = "episode_render_jobs"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    assembly_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    kind: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    status: Mapped[str] = mapped_column(
        VARCHAR(16), nullable=False, server_default=text("'queued'")
    )
    stage: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, server_default=text("'queued'"))
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    context_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        VARCHAR(128, collation="utf8mb4_0900_bin"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(CHAR(64, charset="ascii"), nullable=False)
    progress: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    attempts: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    message_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    next_run_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(CHAR(32, charset="ascii"), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    cancel_requested: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    output_media_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_of_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True, comment="原任务ID，由服务层验证同成片归属"
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    __table_args__ = (
        UniqueConstraint("assembly_id", "kind", "idempotency_key", name="uk_render_jobs_request"),
        Index("idx_render_jobs_schedule", "status", "next_run_at"),
        Index("idx_render_jobs_history", "assembly_id", "kind", "created_at", "id"),
        ForeignKeyConstraint(
            ["assembly_id"],
            ["episode_assemblies.id"],
            name="fk_render_jobs_assembly",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["output_media_id"],
            ["media_files.id"],
            name="fk_render_jobs_media",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("kind IN ('probe','export','preview')", name="ck_render_jobs_kind"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_render_jobs_status",
        ),
        CheckConstraint(
            "progress <= 100 AND cancel_requested IN (0,1)", name="ck_render_jobs_progress"
        ),
        CheckConstraint("JSON_TYPE(snapshot) = 'OBJECT'", name="ck_render_jobs_snapshot"),
        CheckConstraint(
            "manifest IS NULL OR JSON_TYPE(manifest) = 'OBJECT'", name="ck_render_jobs_manifest"
        ),
        CheckConstraint(
            "error IS NULL OR JSON_TYPE(error) = 'OBJECT'", name="ck_render_jobs_error"
        ),
        CheckConstraint("message_version > 0", name="ck_render_jobs_version"),
        {**TABLE_OPTIONS, "comment": "本地媒体探测与成片合成任务，不调用AI供应商"},
    )
