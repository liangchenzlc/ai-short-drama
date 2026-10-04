"""Private conversation inputs and owner-managed Markdown creation skills."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .agent import _fk, _hash, _id, _json, _number, _time
from .base import Base
from .collaboration import OPTIONS


class AgentAttachment(Base):
    __tablename__ = "agent_attachments"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    conversation_id: Mapped[int] = _number()
    attached_message_id: Mapped[int | None] = _number(nullable=True)
    kind: Mapped[str] = mapped_column(VARCHAR(16, collation="utf8mb4_0900_bin"))
    name: Mapped[str] = mapped_column(VARCHAR(255))
    mime_type: Mapped[str] = mapped_column(VARCHAR(127))
    media_id: Mapped[int | None] = _number(nullable=True)
    text_content: Mapped[str | None] = mapped_column(MEDIUMTEXT(), nullable=True)
    checksum_sha256: Mapped[str] = _hash()
    byte_size: Mapped[int] = _number()
    input_metadata: Mapped[dict] = _json()
    create_key: Mapped[str | None] = _hash(nullable=True)
    create_hash: Mapped[str | None] = _hash(nullable=True)
    deleted_at: Mapped[datetime | None] = _time(nullable=True)
    created_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_attachment_owner"),
        _fk(["conversation_id"], "agent_conversations", name="fk_agent_attachment_conversation"),
        _fk(["attached_message_id"], "agent_messages", name="fk_agent_attachment_message"),
        _fk(["media_id"], "media_files", name="fk_agent_attachment_media"),
        UniqueConstraint("conversation_id", "create_key", name="uk_agent_attachment_create"),
        Index("idx_agent_attachment_conversation", "conversation_id", "id"),
        CheckConstraint(
            "kind IN ('text','image','video','audio')", name="ck_agent_attachment_kind"
        ),
        CheckConstraint(
            "(kind = 'text' AND text_content IS NOT NULL AND media_id IS NULL) OR "
            "(kind <> 'text' AND media_id IS NOT NULL)",
            name="ck_agent_attachment_content",
        ),
        CheckConstraint(
            "JSON_TYPE(input_metadata) = 'OBJECT'", name="ck_agent_attachment_metadata"
        ),
        CheckConstraint(
            "(create_key IS NULL) = (create_hash IS NULL)", name="ck_agent_attachment_create_pair"
        ),
        OPTIONS,
    )


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id: Mapped[int] = _id()
    owner_user_id: Mapped[int] = _number()
    name: Mapped[str] = mapped_column(VARCHAR(120))
    filename: Mapped[str] = mapped_column(VARCHAR(255))
    instructions: Mapped[str] = mapped_column(MEDIUMTEXT())
    content_version: Mapped[int] = _number(1)
    checksum_sha256: Mapped[str] = _hash()
    row_version: Mapped[int] = _number(1)
    enabled: Mapped[int] = _number(1)
    deleted_at: Mapped[datetime | None] = _time(nullable=True)
    created_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _time()

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_skill_owner"),
        Index("idx_agent_skill_owner", "owner_user_id", "deleted_at", "id"),
        CheckConstraint("enabled IN (0,1)", name="ck_agent_skill_enabled"),
        CheckConstraint("row_version > 0 AND content_version > 0", name="ck_agent_skill_versions"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(instructions)) > 0 AND OCTET_LENGTH(instructions) <= 65536",
            name="ck_agent_skill_content",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_agent_skill_time"),
        OPTIONS,
    )
