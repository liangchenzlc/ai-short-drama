"""Account-owned assistant preferences, independent of model selection and tasks."""

from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON, MEDIUMTEXT, TINYINT
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
    custom_instructions: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
    settings_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )
    model_call_limit: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("240")
    )
    output_token_limit: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("2000000")
    )
    subagent_max_concurrency: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("3")
    )
    default_media_limits: Mapped[dict] = mapped_column(
        JSON, nullable=False, server_default=text("(JSON_OBJECT())")
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
        CheckConstraint("policy_version >= 0", name="ck_agent_preferences_version"),
        CheckConstraint("model_call_limit BETWEEN 20 AND 2000", name="ck_agent_preferences_calls"),
        CheckConstraint(
            "output_token_limit BETWEEN 100000 AND 20000000", name="ck_agent_preferences_tokens"
        ),
        CheckConstraint(
            "subagent_max_concurrency BETWEEN 1 AND 3", name="ck_agent_preferences_concurrency"
        ),
        OPTIONS,
    )
