"""Immutable account skill packages and separate personal availability settings."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk, _hash, _id, _json, _number, _time
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentSkillVersion(Base):
    __tablename__ = "agent_skill_versions"

    id: Mapped[int] = _id()
    skill_key: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    skill_id: Mapped[int | None] = _number(nullable=True)
    owner_user_id: Mapped[int | None] = _number(nullable=True)
    content_version: Mapped[int] = _number()
    package_metadata: Mapped[dict] = _json()
    instructions: Mapped[str] = mapped_column(MEDIUMTEXT())
    resources: Mapped[list] = _json(array=True)
    checksum_sha256: Mapped[str] = _hash()
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_skill_version_owner"),
        _fk(
            ["skill_id", "owner_user_id"],
            "agent_skills",
            ["id", "owner_user_id"],
            name="fk_agent_skill_version_skill",
        ),
        UniqueConstraint("skill_key", "content_version", name="uk_agent_skill_version"),
        Index("idx_agent_skill_version_owner", "owner_user_id", "skill_id", "content_version"),
        CheckConstraint("content_version > 0", name="ck_agent_skill_version_positive"),
        CheckConstraint(
            "(skill_id IS NULL AND owner_user_id IS NULL) OR "
            "(skill_id IS NOT NULL AND owner_user_id IS NOT NULL "
            "AND skill_key = CONCAT('', skill_id))",
            name="ck_agent_skill_version_identity",
        ),
        CheckConstraint(
            "JSON_TYPE(package_metadata) = 'OBJECT' AND JSON_TYPE(resources) = 'ARRAY' "
            "AND JSON_LENGTH(resources) <= 31",
            name="ck_agent_skill_version_package",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(instructions)) > 0 AND OCTET_LENGTH(instructions) <= 65536",
            name="ck_agent_skill_version_content",
        ),
        OPTIONS,
    )


class AgentSkillSetting(Base):
    __tablename__ = "agent_skill_settings"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    skill_key: Mapped[str] = mapped_column(VARCHAR(80, collation="utf8mb4_0900_bin"))
    skill_id: Mapped[int | None] = _number(nullable=True)
    enabled: Mapped[int] = _number(1)
    row_version: Mapped[int] = _number(1)
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_skill_setting_owner"),
        _fk(
            ["skill_id", "owner_user_id"],
            "agent_skills",
            ["id", "owner_user_id"],
            name="fk_agent_skill_setting_skill",
        ),
        UniqueConstraint("owner_user_id", "skill_key", name="uk_agent_skill_setting"),
        CheckConstraint("enabled IN (0,1)", name="ck_agent_skill_setting_enabled"),
        CheckConstraint(
            "row_version > 0",
            name="ck_agent_skill_setting_values",
        ),
        CheckConstraint(
            "skill_id IS NULL OR skill_key = CONCAT('', skill_id)",
            name="ck_agent_skill_setting_identity",
        ),
        OPTIONS,
    )
