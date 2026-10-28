from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from short_drama.domain.base import Base
from short_drama.domain.options import OPTIONS


class UserModelPreference(Base):
    __tablename__ = "user_model_preferences"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    context_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    config_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("ai_model_configs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        UniqueConstraint("user_id", "context_key", name="uk_model_preference"),
        OPTIONS,
    )
