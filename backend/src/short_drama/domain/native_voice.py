"""Project-scoped voice bindings; historical generation records own voice candidates."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.dialects.mysql import BIGINT, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .episode_assembly import TABLE_OPTIONS


class ProjectSoundMode(Base):
    __tablename__ = "project_sound_modes"
    project_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    mode: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_projectsoundmode_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("mode IN ('legacy','native')", name="ck_project_sound_mode"),
        CheckConstraint("row_version > 0", name="ck_project_sound_mode_version"),
        TABLE_OPTIONS,
    )


class CharacterVoice(Base):
    __tablename__ = "character_voices"
    project_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    asset_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    record_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    media_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_charactervoice_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_charactervoice_asset_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["record_id"],
            ["ai_generation_records.id"],
            name="fk_charactervoice_record_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["media_files.id"],
            name="fk_charactervoice_media_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("row_version > 0", name="ck_character_voice_version"),
        TABLE_OPTIONS,
    )


class ShotDialogue(Base):
    __tablename__ = "shot_dialogues"
    shot_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    row_version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["shot_id"],
            ["shot_scripts.id"],
            name="fk_shotdialogue_shot_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("row_version > 0", name="ck_shot_dialogue_version"),
        CheckConstraint("JSON_TYPE(document) = 'OBJECT'", name="ck_shot_dialogue_document"),
        TABLE_OPTIONS,
    )
