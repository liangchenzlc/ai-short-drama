"""Account-owned assistant preferences, independent of model selection and tasks."""

from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.assistant.columns import _fk
from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class AgentPreference(Base):
    __tablename__ = "agent_preferences"

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    owner_user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    media_generation_enabled: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("1")
    )
    policy_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )

    __table_args__ = (
        _fk(["owner_user_id"], "users", name="fk_agent_preferences_owner"),
        UniqueConstraint("owner_user_id", name="uk_agent_preferences_owner"),
        CheckConstraint(
            "media_generation_enabled IN (0, 1)", name="ck_agent_preferences_media_enabled"
        ),
        CheckConstraint("policy_version >= 1", name="ck_agent_preferences_version"),
        OPTIONS,
    )
