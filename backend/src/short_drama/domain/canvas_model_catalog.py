"""本人渠道目录与稳定执行配置映射；凭据独立加密。"""

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, JSON, MEDIUMTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .canvas import CanvasAudit
from .collaboration import OPTIONS


class CanvasModelCatalog(CanvasAudit, Base):
    __tablename__ = "canvas_model_catalogs"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    channels_json: Mapped[list] = mapped_column(JSON)
    credentials_cipher: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", name="uk_canvas_model_catalog_user"), OPTIONS)


class CanvasChannelModel(CanvasAudit, Base):
    __tablename__ = "canvas_channel_models"
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    channel_key: Mapped[str] = mapped_column(VARCHAR(128, collation="utf8mb4_0900_bin"))
    model_key: Mapped[str] = mapped_column(VARCHAR(255, collation="utf8mb4_0900_bin"))
    model_config_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("ai_model_configs.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    __table_args__ = (
        UniqueConstraint("user_id", "channel_key", "model_key", name="uk_canvas_channel_model"),
        UniqueConstraint("model_config_id", name="uk_canvas_channel_config"),
        OPTIONS,
    )
