from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .episode_assembly import TABLE_OPTIONS


class EpisodeSound(Base):
    __tablename__ = "episode_sounds"
    assembly_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=False
    )
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    reviewed_timeline_hash: Mapped[str | None] = mapped_column(
        CHAR(64, charset="ascii"), nullable=True
    )
    last_receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["assembly_id"],
            ["episode_assemblies.id"],
            name="fk_sounds_assembly",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("row_version > 0", name="ck_sounds_version"),
        CheckConstraint("JSON_TYPE(document) = 'OBJECT'", name="ck_sounds_document"),
        TABLE_OPTIONS,
    )


class ProjectVoiceDefaults(Base):
    __tablename__ = "project_voice_defaults"
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=False
    )
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    voices: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_voice_defaults_project",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("row_version > 0", name="ck_voice_defaults_version"),
        CheckConstraint("JSON_TYPE(voices) = 'OBJECT'", name="ck_voice_defaults_voices"),
        TABLE_OPTIONS,
    )


class SoundMediaReference(Base):
    """Retain original/proxy/candidate media used by sound drafts or render history."""

    __tablename__ = "sound_media_references"
    assembly_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=False
    )
    media_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=False
    )
    proxy_media_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["proxy_media_id"],
            ["media_files.id"],
            name="fk_sound_refs_proxy",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assembly_id"],
            ["episode_assemblies.id"],
            name="fk_sound_refs_assembly",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_sound_refs_media",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        TABLE_OPTIONS,
    )
